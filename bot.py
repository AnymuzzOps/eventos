import os
import asyncio
from datetime import datetime, timedelta
import httpx
from groq import Groq

# ── Config ──────────────────────────────────────────────────────────────────
TAVILY_API_KEY   = os.environ["TAVILY_API_KEY"]
GROQ_API_KEY     = os.environ["GROQ_API_KEY"]
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

GROQ_MODEL = "llama-3.3-70b-versatile"

hoy    = datetime.now().strftime("%d de %B de %Y")
manana = (datetime.now() + timedelta(days=1)).strftime("%d de %B de %Y")
anio_actual = datetime.now().year

QUERIES = [
    "evento gratis Santiago hoy",
    "inauguración gratis Santiago esta semana",
    "degustación gratis Santiago",
    "activación gratuita marca Santiago",
    "evento cultural gratis Santiago hoy",
    "pop-up gratuito Santiago",
    "site:instagram.com evento gratis Santiago hoy",
    "site:instagram.com inauguración gratis Santiago",
]

SYSTEM_PROMPT = f"""Eres un filtro estricto de eventos para Santiago de Chile.
Hoy es {hoy}. Mañana es {manana}. El año actual es {anio_actual}.

Recibiras un resultado de búsqueda web (título + snippet + url) y debes responder SOLO con un JSON así:
{{
  "aprobado": true/false,
  "nombre": "Nombre del evento",
  "lugar": "Dirección o lugar, Santiago",
  "fecha_hora": "Fecha y hora según la info disponible",
  "descripcion": "1 línea atractiva del evento",
  "link": "URL original"
}}

Aprueba SOLO si cumple TODO:
1. Es en Santiago de Chile (no otra ciudad)
2. Es GRATIS o gratuito (sin costo de entrada)
3. Es un evento puntual/express: degustación, inauguración, pop-up, activación de marca, evento cultural de un día
4. La fecha del evento es en {anio_actual} Y dentro de los próximos 3 días desde hoy ({hoy})
   — RECHAZA cualquier evento de {anio_actual - 1} o años anteriores
   — RECHAZA si la fecha del evento ya pasó
   — RECHAZA si el snippet no menciona una fecha concreta o menciona solo "próximamente"
5. NO es un lugar permanente (museo siempre gratis, parque, etc.)

Si hay CUALQUIER duda sobre la fecha o el año, devuelve aprobado: false.
Responde SOLO el JSON, sin texto adicional."""


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


# ── Groq filter ─────────────────────────────────────────────────────────────
def groq_evaluar(resultado: dict) -> dict | None:
    groq = Groq(api_key=GROQ_API_KEY)
    contenido = f"Título: {resultado.get('title','')}\nSnippet: {resultado.get('content','')}\nURL: {resultado.get('url','')}"
    try:
        chat = groq.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": contenido},
            ],
            temperature=0.1,
            max_tokens=300,
        )
        import json
        raw = chat.choices[0].message.content.strip()
        data = json.loads(raw)
        return data if data.get("aprobado") else None
    except Exception as e:
        print(f"[Groq error] {e}")
        return None


# ── Telegram send ────────────────────────────────────────────────────────────
async def telegram_send(client: httpx.AsyncClient, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    await client.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }, timeout=15)


def formatear_evento(ev: dict) -> str:
    return (
        f"🎉 <b>{ev['nombre']}</b>\n"
        f"📍 {ev['lugar']}\n"
        f"🗓 {ev['fecha_hora']}\n"
        f"✨ {ev['descripcion']}\n"
        f"🔗 {ev['link']}"
    )


# ── Main ─────────────────────────────────────────────────────────────────────
async def main():
    aprobados: list[dict] = []
    urls_vistas: set[str] = set()

    async with httpx.AsyncClient() as client:
        # Búsquedas en paralelo
        tasks = [tavily_search(client, q) for q in QUERIES]
        resultados_por_query = await asyncio.gather(*tasks)

        # Deduplicar y evaluar
        todos: list[dict] = []
        for resultados in resultados_por_query:
            for r in resultados:
                url = r.get("url", "")
                if url and url not in urls_vistas:
                    urls_vistas.add(url)
                    todos.append(r)

        print(f"[Info] {len(todos)} resultados únicos para evaluar")

        for r in todos:
            # Pre-filtro barato: descartar si el snippet menciona solo años viejos
            contenido_raw = (r.get("title", "") + r.get("content", "")).lower()
            anios_viejos = [str(a) for a in range(2020, anio_actual)]
            if any(f" {a} " in contenido_raw or f"/{a}" in contenido_raw for a in anios_viejos):
                if str(anio_actual) not in contenido_raw:
                    print(f"[Skipped - año viejo] {r.get('title','')[:60]}")
                    continue

            ev = groq_evaluar(r)
            if ev:
                aprobados.append(ev)
                print(f"[Aprobado] {ev['nombre']}")

        # Enviar por Telegram
        if not aprobados:
            await telegram_send(client, "🔍 <b>Eventos gratis Santiago</b>\n\nNo encontré eventos gratuitos express para hoy o mañana. ¡Intenta más tarde!")
        else:
            header = f"🗺 <b>Eventos gratis en Santiago — {hoy}</b>\n{len(aprobados)} evento(s) encontrado(s)\n"
            await telegram_send(client, header)
            for ev in aprobados:
                await telegram_send(client, formatear_evento(ev))

        print(f"[Done] {len(aprobados)} evento(s) enviados")


if __name__ == "__main__":
    asyncio.run(main())
