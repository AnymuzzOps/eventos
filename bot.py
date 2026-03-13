import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
import httpx
from groq import Groq

# ── Config ──────────────────────────────────────────────────────────────────
TAVILY_API_KEY   = os.environ["TAVILY_API_KEY"]
GROQ_API_KEY     = os.environ["GROQ_API_KEY"]
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

GROQ_MODEL = "llama-3.3-70b-versatile"

CHILE_TZ   = timezone(timedelta(hours=-3))
ahora      = datetime.now(CHILE_TZ)
hoy_dt     = ahora.date()
hoy        = ahora.strftime("%d de %B de %Y")
hoy_iso    = hoy_dt.isoformat()
anio_actual = ahora.year

# ── Dominios bloqueados ──────────────────────────────────────────────────────
# Eventbrite: páginas de listado genérico, no eventos concretos
# (si Tavily trae una URL de eventbrite con ID de evento específico, igual bloqueamos
#  porque el snippet nunca tiene info suficiente de fecha/lugar)
DOMINIOS_BLOQUEADOS = {
    "eventbrite.com",
    "eventbrite.cl",
}

# ── Queries ──────────────────────────────────────────────────────────────────
QUERIES = [
    f"evento gratis Santiago {ahora.strftime('%B %Y')}",
    "inauguración gratuita Santiago esta semana",
    "degustación gratis Santiago",
    "activación gratuita marca Santiago",
    "evento cultural gratis Santiago hoy",
    "pop-up gratuito entrada libre Santiago",
    "feria gratuita Santiago este fin de semana",
    "concierto gratis Santiago hoy",
    "site:instagram.com evento gratis Santiago hoy",
]

# ── Categorías ───────────────────────────────────────────────────────────────
CATEGORIAS = {
    "degustacion": "🍷 Degustación",
    "inauguracion": "🎊 Inauguración",
    "pop-up":       "🛍 Pop-up",
    "activacion":   "📣 Activación",
    "arte":         "🎨 Arte/Cultura",
    "feria":        "🏪 Feria",
    "musica":       "🎵 Música",
    "otro":         "📌 Evento",
}

# ── System prompt ────────────────────────────────────────────────────────────
SYSTEM_PROMPT = f"""Eres un filtro MUY ESTRICTO de eventos gratuitos para Santiago de Chile.
Fecha actual en Chile: {ahora.strftime("%d de %B de %Y, %H:%M")} (hora Chile).
Año actual: {anio_actual}.

Recibirás título + snippet + fecha_publicación + URL de un resultado web.
Responde SOLO con JSON, sin texto adicional.

Si APRUEBAS:
{{
  "aprobado": true,
  "nombre": "Nombre exacto del evento",
  "lugar": "Dirección o lugar, Santiago",
  "fecha_hora": "Descripción legible de fecha/hora",
  "fecha_iso": "YYYY-MM-DD o null",
  "descripcion": "1 línea atractiva, máx 100 caracteres",
  "categoria": "degustacion|inauguracion|pop-up|activacion|arte|feria|musica|otro",
  "link": "URL original"
}}

Si RECHAZAS:
{{"aprobado": false, "razon": "motivo en 5 palabras"}}

═══ CRITERIOS — rechaza si falla CUALQUIERA ═══

1. CIUDAD: debe ser Santiago de Chile. Rechaza otras ciudades.

2. GRATUITO REAL: la entrada al evento debe ser gratis.
   — RECHAZA si es un pop-up o tienda donde se vende (aunque entrar sea gratis)
   — RECHAZA si dice "preventa", "tickets", "entradas desde $X"
   — APRUEBA solo si es explícitamente "entrada gratuita", "gratis", "sin costo", "libre"

3. EVENTO PUNTUAL: debe ser un evento de 1-3 días, no permanente.
   — RECHAZA si es museo/galería con horario fijo permanente
   — RECHAZA si es artículo tipo "10 cosas gratis en Santiago"
   — RECHAZA si es página de listado de eventos (Eventbrite genérico, etc.)

4. FECHA FUTURA — esta es la regla más importante:
   — La fecha del EVENTO (no de publicación) debe ser {hoy_iso} o posterior
   — RECHAZA si el evento ya ocurrió (aunque sea de {anio_actual})
   — Ejemplos de rechazar: "enero 2026", "febrero 2026", "verano 2026", cualquier mes anterior a {ahora.strftime("%B")}
   — Si el snippet menciona fechas pasadas como "se realizó", "fue", "tuvo lugar" → RECHAZA
   — Si no hay fecha explícita y el snippet no tiene señales de que es próximo → RECHAZA
   — Solo aprueba sin fecha si el snippet usa palabras como "hoy", "mañana", "este viernes", "este fin de semana", "próximo", "próximos días"

5. CONTENIDO CONCRETO: debe tener al menos nombre del evento y alguna indicación de lugar o fecha.
   — RECHAZA si es demasiado vago (ej: "concierto en Santiago", sin más detalles)

Sé implacable. Prefiero perder un evento real que enviar basura."""


# ── Helpers ──────────────────────────────────────────────────────────────────
def dominio_bloqueado(url: str) -> bool:
    for d in DOMINIOS_BLOQUEADOS:
        if d in url:
            return True
    return False


def published_date_es_vieja(resultado: dict) -> bool:
    """Descarta si Tavily entrega published_date claramente antigua (>4 días)."""
    pub = resultado.get("published_date", "")
    if not pub:
        return False
    try:
        fecha_pub = datetime.fromisoformat(pub.replace("Z", "+00:00")).date()
        return fecha_pub < hoy_dt - timedelta(days=4)
    except Exception:
        return False


# ── Tavily search ────────────────────────────────────────────────────────────
async def tavily_search(client: httpx.AsyncClient, query: str) -> list[dict]:
    try:
        r = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "max_results": 5,
                "include_answer": False,
                "days": 3,
            },
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as e:
        print(f"[Tavily error] {query}: {e}")
        return []


# ── Groq filter ──────────────────────────────────────────────────────────────
def groq_evaluar(resultado: dict) -> dict | None:
    groq = Groq(api_key=GROQ_API_KEY)
    contenido = (
        f"Título: {resultado.get('title','')}\n"
        f"Snippet: {resultado.get('content','')}\n"
        f"Fecha de publicación web: {resultado.get('published_date', 'desconocida')}\n"
        f"URL: {resultado.get('url','')}"
    )
    try:
        chat = groq.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": contenido},
            ],
            temperature=0.0,
            max_tokens=350,
        )
        raw = chat.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)

        if not data.get("aprobado"):
            print(f"  → ❌ {data.get('razon','')}")
            return None

        # Validación extra en Python: si Groq dio fecha_iso y es pasada, rechazar
        fecha_iso = data.get("fecha_iso")
        if fecha_iso:
            try:
                from datetime import date
                if date.fromisoformat(fecha_iso) < hoy_dt:
                    print(f"  → ❌ Post-validación: {fecha_iso} ya pasó")
                    return None
            except Exception:
                pass

        return data

    except Exception as e:
        print(f"[Groq error] {e}")
        return None


# ── Formatear ────────────────────────────────────────────────────────────────
def formatear_evento(ev: dict) -> str:
    cat_label = CATEGORIAS.get(ev.get("categoria", "otro"), "📌 Evento")
    return (
        f"{cat_label} — <b>{ev['nombre']}</b>\n"
        f"📍 {ev['lugar']}\n"
        f"🗓 {ev['fecha_hora']}\n"
        f"✨ {ev['descripcion']}\n"
        f"🔗 {ev['link']}"
    )


# ── Telegram ─────────────────────────────────────────────────────────────────
async def telegram_send(client: httpx.AsyncClient, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    await client.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }, timeout=15)


# ── Main ─────────────────────────────────────────────────────────────────────
async def main():
    aprobados: list[dict] = []
    urls_vistas: set[str] = set()
    stats = {"total": 0, "skip_dominio": 0, "skip_fecha_pub": 0, "rechazados": 0}

    async with httpx.AsyncClient() as client:
        tasks = [tavily_search(client, q) for q in QUERIES]
        resultados_por_query = await asyncio.gather(*tasks)

        todos: list[dict] = []
        for resultados in resultados_por_query:
            for r in resultados:
                url = r.get("url", "")
                if url and url not in urls_vistas:
                    urls_vistas.add(url)
                    todos.append(r)

        stats["total"] = len(todos)
        print(f"[Info] {len(todos)} resultados únicos")

        for r in todos:
            titulo = r.get("title", "")[:65]
            url    = r.get("url", "")

            if dominio_bloqueado(url):
                print(f"[Skip-dominio] {titulo}")
                stats["skip_dominio"] += 1
                continue

            if published_date_es_vieja(r):
                print(f"[Skip-pub] {titulo}")
                stats["skip_fecha_pub"] += 1
                continue

            print(f"[Evaluando] {titulo}")
            ev = groq_evaluar(r)
            if ev:
                aprobados.append(ev)
                print(f"  → ✅ {ev['nombre']} [{ev.get('categoria','?')}]")
            else:
                stats["rechazados"] += 1

        print(f"[Stats] total={stats['total']} | skip_dom={stats['skip_dominio']} | skip_pub={stats['skip_fecha_pub']} | rechazados={stats['rechazados']} | aprobados={len(aprobados)}")

        if not aprobados:
            await telegram_send(
                client,
                f"🔍 <b>Eventos gratis Santiago — {hoy}</b>\n\n"
                "No encontré eventos gratuitos para hoy o los próximos días."
            )
        else:
            aprobados.sort(key=lambda e: e.get("fecha_iso") or "9999")
            header = (
                f"🗺 <b>Eventos gratis en Santiago</b>\n"
                f"📅 {hoy}\n"
                f"{'─'*28}\n"
                f"<b>{len(aprobados)}</b> evento(s) encontrado(s)"
            )
            await telegram_send(client, header)
            for ev in aprobados:
                await telegram_send(client, formatear_evento(ev))

        print(f"[Done] {len(aprobados)} evento(s) enviados")


if __name__ == "__main__":
    asyncio.run(main())
