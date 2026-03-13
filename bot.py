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

# Hora Chile (UTC-3)
CHILE_TZ  = timezone(timedelta(hours=-3))
ahora     = datetime.now(CHILE_TZ)
hoy_dt    = ahora.date()
hoy       = ahora.strftime("%d de %B de %Y")
manana    = (ahora + timedelta(days=1)).strftime("%d de %B de %Y")
hoy_iso   = hoy_dt.isoformat()                        # "2026-03-13"
limite_iso = (hoy_dt + timedelta(days=5)).isoformat() # "2026-03-18"
anio_actual = ahora.year

QUERIES = [
    "evento gratis Santiago hoy",
    "inauguración gratis Santiago esta semana",
    "degustación gratis Santiago",
    "activación gratuita marca Santiago",
    "evento cultural gratis Santiago hoy",
    "pop-up gratuito Santiago",
    "feria gratuita Santiago este fin de semana",
    "site:instagram.com evento gratis Santiago hoy",
    "site:instagram.com inauguración gratis Santiago",
]

# ── Categorías ───────────────────────────────────────────────────────────────
CATEGORIAS = {
    "degustacion": "🍷 Degustación",
    "inauguracion": "🎊 Inauguración",
    "pop-up": "🛍 Pop-up",
    "activacion": "📣 Activación",
    "arte": "🎨 Arte/Cultura",
    "feria": "🏪 Feria",
    "musica": "🎵 Música",
    "otro": "📌 Evento",
}

SYSTEM_PROMPT = f"""Eres un filtro estricto de eventos gratuitos para Santiago de Chile.
Fecha y hora actual en Chile: {ahora.strftime("%d de %B de %Y, %H:%M")} (hora Chile).
Solo existen eventos desde HOY ({hoy_iso}) en adelante. Cualquier evento con fecha anterior a {hoy_iso} NO EXISTE para ti.

Recibirás un resultado de búsqueda web. Responde SOLO con este JSON, sin texto adicional:
{{
  "aprobado": true,
  "nombre": "Nombre del evento",
  "lugar": "Dirección o lugar, Santiago",
  "fecha_hora": "Fecha y hora (YYYY-MM-DD HH:MM o descripción si no hay hora exacta)",
  "fecha_iso": "YYYY-MM-DD o null si no se puede determinar",
  "descripcion": "1 línea atractiva del evento",
  "categoria": "degustacion | inauguracion | pop-up | activacion | arte | feria | musica | otro",
  "link": "URL original"
}}

O si rechazas:
{{"aprobado": false, "razon": "motivo breve"}}

REGLAS DE APROBACIÓN — debe cumplir TODO:
1. Santiago de Chile (no otra ciudad ni región)
2. Entrada gratuita/gratis (sin costo)
3. Evento puntual: degustación, inauguración, pop-up, activación de marca, feria, concierto gratis, evento cultural de uno o pocos días
4. FECHA: la fecha del evento debe ser {hoy_iso} o posterior (futuro o hoy)
   — Si el snippet dice "hoy", "este fin de semana", "esta semana" → fecha_iso: "{hoy_iso}"
   — Si no hay fecha pero todo indica que es próximo/vigente → fecha_iso: null, aprueba igual
   — RECHAZA si la fecha mencionada es anterior a {hoy_iso}
5. NO es atracción permanente (museo, parque, monumento)
6. NO es un artículo tipo "10 cosas gratis que hacer en Santiago" sin evento concreto

Sé estricto con fechas pasadas, flexible con eventos sin fecha exacta."""


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


# ── Filtro de fecha por published_date (pre-Groq, no agresivo) ───────────────
def es_demasiado_viejo(resultado: dict) -> bool:
    """Descarta solo si Tavily entrega published_date y es claramente anterior a ayer."""
    pub = resultado.get("published_date", "")
    if not pub:
        return False
    try:
        # Tavily devuelve algo como "2025-01-10T00:00:00Z"
        fecha_pub = datetime.fromisoformat(pub.replace("Z", "+00:00")).date()
        return fecha_pub < hoy_dt - timedelta(days=1)
    except Exception:
        return False


# ── Groq filter ─────────────────────────────────────────────────────────────
def groq_evaluar(resultado: dict) -> dict | None:
    groq = Groq(api_key=GROQ_API_KEY)
    contenido = (
        f"Título: {resultado.get('title','')}\n"
        f"Snippet: {resultado.get('content','')}\n"
        f"Fecha publicación web: {resultado.get('published_date', 'desconocida')}\n"
        f"URL: {resultado.get('url','')}"
    )
    try:
        chat = groq.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": contenido},
            ],
            temperature=0.1,
            max_tokens=350,
        )
        raw = chat.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)

        if not data.get("aprobado"):
            print(f"  → Rechazado: {data.get('razon','')}")
            return None

        # Validación extra: si Groq devolvió fecha_iso y es pasada, rechazar
        fecha_iso = data.get("fecha_iso")
        if fecha_iso:
            try:
                from datetime import date
                fecha_evento = date.fromisoformat(fecha_iso)
                if fecha_evento < hoy_dt:
                    print(f"  → Rechazado post-Groq: fecha {fecha_iso} ya pasó")
                    return None
            except Exception:
                pass

        return data

    except Exception as e:
        print(f"[Groq error] {e}")
        return None


# ── Formatear con categoría ──────────────────────────────────────────────────
def formatear_evento(ev: dict) -> str:
    cat_key   = ev.get("categoria", "otro")
    cat_label = CATEGORIAS.get(cat_key, "📌 Evento")
    return (
        f"{cat_label} — <b>{ev['nombre']}</b>\n"
        f"📍 {ev['lugar']}\n"
        f"🗓 {ev['fecha_hora']}\n"
        f"✨ {ev['descripcion']}\n"
        f"🔗 {ev['link']}"
    )


# ── Telegram send ────────────────────────────────────────────────────────────
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

        print(f"[Info] {len(todos)} resultados únicos")

        skipped = 0
        for r in todos:
            titulo = r.get('title','')[:65]

            # Pre-filtro suave: solo descarta si published_date es claramente viejo
            if es_demasiado_viejo(r):
                print(f"[Skip-fecha] {titulo}")
                skipped += 1
                continue

            print(f"[Evaluando] {titulo}")
            ev = groq_evaluar(r)
            if ev:
                aprobados.append(ev)
                print(f"  → ✅ Aprobado: {ev['nombre']} [{ev.get('categoria','?')}]")

        print(f"[Info] Skipped={skipped} | Aprobados={len(aprobados)}")

        # Agrupar por categoría para el mensaje
        if not aprobados:
            await telegram_send(
                client,
                f"🔍 <b>Eventos gratis Santiago — {hoy}</b>\n\n"
                "No encontré eventos gratuitos para hoy o los próximos días. ¡Intenta más tarde!"
            )
        else:
            # Ordenar por fecha_iso (nulos al final)
            aprobados.sort(key=lambda e: e.get("fecha_iso") or "9999")

            header = (
                f"🗺 <b>Eventos gratis en Santiago</b>\n"
                f"📅 {hoy}\n"
                f"{'─'*28}\n"
                f"Se encontraron <b>{len(aprobados)}</b> evento(s)"
            )
            await telegram_send(client, header)

            for ev in aprobados:
                await telegram_send(client, formatear_evento(ev))

        print(f"[Done] {len(aprobados)} evento(s) enviados")


if __name__ == "__main__":
    asyncio.run(main())
