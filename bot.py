import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
import httpx
import google.generativeai as genai

# ── Config ──────────────────────────────────────────────────────────────────
TAVILY_API_KEY   = os.environ["TAVILY_API_KEY"]
GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

genai.configure(api_key=GEMINI_API_KEY)
GEMINI_MODEL = genai.GenerativeModel("gemini-2.0-flash")

CHILE_TZ    = timezone(timedelta(hours=-3))
ahora       = datetime.now(CHILE_TZ)
hoy_dt      = ahora.date()
hoy         = ahora.strftime("%d de %B de %Y")
hoy_iso     = hoy_dt.isoformat()
mes_actual  = ahora.month

QUERIES = [
    "evento gratis Santiago hoy",
    "inauguración gratuita Santiago esta semana",
    "degustación gratis Santiago",
    "activación gratuita marca Santiago",
    "pop-up gratuito entrada libre Santiago",
    "feria gratuita Santiago este fin de semana",
    "concierto gratis Santiago hoy",
    "site:instagram.com evento gratis Santiago hoy",
]

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

SYSTEM_PROMPT = f"""Filtro de eventos gratuitos en Santiago de Chile. Hoy: {hoy}.

Responde SOLO JSON:
Si apruebas: {{"ok":true,"nombre":"...","lugar":"...","fecha":"...","fecha_iso":"YYYY-MM-DD o null","desc":"...","cat":"degustacion|inauguracion|pop-up|activacion|arte|feria|musica|otro","link":"..."}}
Si rechazas: {{"ok":false,"r":"motivo breve"}}

APRUEBA solo si:
- Santiago de Chile, entrada 100% gratis
- Evento puntual (1-3 días), no permanente
- Fecha: hoy ({hoy_iso}) o futura. RECHAZA si ya pasó o es de meses anteriores a {ahora.strftime("%B")}
- Tiene nombre y lugar concreto

RECHAZA si: artículo de lista, página de tickets, atracción permanente, pop-up de venta."""

# ── Dominios y títulos bloqueados ────────────────────────────────────────────
DOMINIOS_BLOQUEADOS = {"eventbrite.com", "eventbrite.cl", "ticketplus.cl", "puntoticket.com"}

TITULO_BASURA = [
    "10 panoramas", "10 cosas", "diez panoramas", "los mejores panoramas",
    "guía de", "ferias libres", "horarios y ubicación", "home -",
    "qué hacer en santiago", "cosas gratis que hacer",
]

MESES_PASADOS = {
    1: ["enero"], 2: ["enero","febrero"], 3: ["enero","febrero"],
    4: ["enero","febrero","marzo"], 5: ["enero","febrero","marzo","abril"],
    6: ["enero","febrero","marzo","abril","mayo"],
    7: ["enero","febrero","marzo","abril","mayo","junio"],
    8: ["enero","febrero","marzo","abril","mayo","junio","julio"],
    9: ["enero","febrero","marzo","abril","mayo","junio","julio","agosto"],
    10:["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre"],
    11:["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre"],
    12:["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre"],
}.get(mes_actual, [])


# ── Pre-filtro Python (sin IA, sin costo) ────────────────────────────────────
def prefiltro(r: dict) -> tuple[bool, str]:
    url     = r.get("url", "").lower()
    titulo  = r.get("title", "").lower()
    snippet = r.get("content", "").lower()
    texto   = titulo + " " + snippet

    for d in DOMINIOS_BLOQUEADOS:
        if d in url:
            return False, f"dominio bloqueado ({d})"

    for palabra in TITULO_BASURA:
        if palabra in titulo:
            return False, f"título genérico ({palabra})"

    pub = r.get("published_date", "")
    if pub:
        try:
            fecha_pub = datetime.fromisoformat(pub.replace("Z", "+00:00")).date()
            if fecha_pub < hoy_dt - timedelta(days=5):
                return False, f"publicado hace más de 5 días ({fecha_pub})"
        except Exception:
            pass

    meses_futuros = ["hoy", "mañana", "esta semana", "este fin de semana",
                     "próximo", "próxima", ahora.strftime("%B").lower()]
    tiene_futuro = any(m in texto for m in meses_futuros)
    if not tiene_futuro:
        for mes in MESES_PASADOS:
            if f" {mes} " in texto or f"\n{mes} " in texto:
                return False, f"menciona mes pasado ({mes}) sin señal futura"

    return True, ""


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


# ── Gemini filter ────────────────────────────────────────────────────────────
def gemini_evaluar(resultado: dict) -> dict | None:
    snippet = resultado.get("content", "")[:400]
    contenido = (
        f"Título: {resultado.get('title','')}\n"
        f"Snippet: {snippet}\n"
        f"Pub: {resultado.get('published_date','?')}\n"
        f"URL: {resultado.get('url','')}"
    )
    prompt = f"{SYSTEM_PROMPT}\n\n{contenido}"
    try:
        response = GEMINI_MODEL.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                temperature=0.0,
                max_output_tokens=200,
            )
        )
        raw = response.text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)

        if not data.get("ok"):
            print(f"  → ❌ Gemini: {data.get('r','')}")
            return None

        fi = data.get("fecha_iso")
        if fi:
            try:
                from datetime import date
                if date.fromisoformat(fi) < hoy_dt:
                    print(f"  → ❌ Post-val: {fi} ya pasó")
                    return None
            except Exception:
                pass

        return data

    except Exception as e:
        print(f"  → ⚠️  Gemini error: {str(e)[:100]}")
        return None


# ── Formatear ────────────────────────────────────────────────────────────────
def formatear_evento(ev: dict) -> str:
    cat_label = CATEGORIAS.get(ev.get("cat", "otro"), "📌 Evento")
    return (
        f"{cat_label} — <b>{ev['nombre']}</b>\n"
        f"📍 {ev['lugar']}\n"
        f"🗓 {ev['fecha']}\n"
        f"✨ {ev['desc']}\n"
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
    stats = {"total": 0, "skip_pre": 0, "rechazados_ia": 0, "aprobados": 0}

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
        print(f"[Info] {len(todos)} resultados únicos de Tavily")

        for r in todos:
            titulo = r.get("title", "")[:65]

            pasa, motivo = prefiltro(r)
            if not pasa:
                print(f"[Pre-skip] {titulo[:50]} — {motivo}")
                stats["skip_pre"] += 1
                continue

            print(f"[→ Gemini] {titulo}")
            ev = gemini_evaluar(r)
            if ev:
                aprobados.append(ev)
                stats["aprobados"] += 1
                print(f"  → ✅ {ev['nombre']} [{ev.get('cat','?')}]")
            else:
                stats["rechazados_ia"] += 1

        print(f"\n[Stats] total={stats['total']} | pre-skip={stats['skip_pre']} | ia-rechazó={stats['rechazados_ia']} | aprobados={stats['aprobados']}")

        if not aprobados:
            await telegram_send(
                client,
                f"🔍 <b>Eventos gratis Santiago — {hoy}</b>\n\n"
                "No encontré eventos gratuitos para hoy o los próximos días."
            )
        else:
            aprobados.sort(key=lambda e: e.get("fecha_iso") or "9999")
            await telegram_send(client, (
                f"🗺 <b>Eventos gratis en Santiago</b>\n"
                f"📅 {hoy} — <b>{len(aprobados)}</b> evento(s)"
            ))
            for ev in aprobados:
                await telegram_send(client, formatear_evento(ev))

        print(f"[Done] {len(aprobados)} evento(s) enviados")


if __name__ == "__main__":
    asyncio.run(main())
