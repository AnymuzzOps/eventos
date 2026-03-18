import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from groq import Groq

# ── Config ──────────────────────────────────────────────────────────────────
TAVILY_API_KEY = os.environ["TAVILY_API_KEY"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

GROQ_MODEL = "llama-3.3-70b-versatile"
PROCESADAS_PATH = Path("procesadas.txt")
MAX_EVENTOS = 8

CHILE_TZ = timezone(timedelta(hours=-3))
ahora = datetime.now(CHILE_TZ)
hoy_dt = ahora.date()
hoy = ahora.strftime("%-d de %B de %Y")
hoy_iso = hoy_dt.isoformat()
ANO_OBJETIVO = 2026
FECHA_MINIMA = date(2026, 3, 19)

QUERIES = [
    f"eventos gratis exclusivos Santiago Chile {ANO_OBJETIVO}",
    f"inauguración gratis Santiago Chile marzo {ANO_OBJETIVO}",
    f"degustación gratis Santiago Chile marzo {ANO_OBJETIVO}",
    f"pop up exclusivo gratis Santiago Chile marzo {ANO_OBJETIVO}",
    f"lanzamiento gratuito marca Santiago Chile marzo {ANO_OBJETIVO}",
    f"activación gratis Santiago centro marzo {ANO_OBJETIVO}",
    f"site:instagram.com Santiago Chile inauguración gratis marzo {ANO_OBJETIVO}",
    f"site:instagram.com Santiago Chile degustación gratis marzo {ANO_OBJETIVO}",
    f"site:tiktok.com Santiago Chile inauguración marzo {ANO_OBJETIVO}",
    f"site:santiagocultura.cl Santiago inauguración marzo {ANO_OBJETIVO}",
    f"site:chilecultura.gob.cl Santiago marzo {ANO_OBJETIVO} evento gratis",
    f"site:instagram.com Santiago centro experiencia gratis {ANO_OBJETIVO}",
]

CATEGORIAS = {
    "degustacion": "🍷 Degustación",
    "inauguracion": "🎊 Inauguración",
    "popup": "🛍 Pop-up",
    "activacion": "📣 Activación",
    "arte": "🎨 Arte/Cultura",
    "feria": "🏪 Feria",
    "musica": "🎵 Música",
    "experiencia": "✨ Experiencia",
    "otro": "📌 Evento",
}

COMUNAS_PERMITIDAS = {
    "santiago",
    "santiago centro",
    "casco histórico",
    "lastarria",
    "bellas artes",
    "barrio yungay",
    "parque o'higgins",
    "parque ohiggins",
    "quinta normal",
    "mapocho",
    "plaza de armas",
    "gam",
    "baquedano",
    "alameda",
    "estación central",  # útil si el texto viene mezclado con Santiago Centro
}

KEYWORDS_EXCLUSIVOS = [
    "inaugur",
    "opening",
    "apertura",
    "lanzamiento",
    "premiere",
    "preestreno",
    "experiencia",
    "activación",
    "activacion",
    "degust",
    "cata",
    "wine tasting",
    "edición limitada",
    "edicion limitada",
    "solo por",
    "cupos limitados",
    "única fecha",
    "unica fecha",
    "pop-up",
    "popup",
    "intervención",
    "intervencion",
]

KEYWORDS_ESTAFA = [
    "multinivel",
    "network marketing",
    "ganancias",
    "gana dinero",
    "independencia financiera",
    "corea",
    "emprendimiento coreano",
    "kit inicial",
    "inscripción",
    "inscripcion",
    "reserva con pago",
    "abono",
    "pirámide",
    "piramide",
]

DOMINIOS_BLOQUEADOS = {
    "eventbrite.com",
    "eventbrite.cl",
    "ticketplus.cl",
    "puntoticket.com",
    "freetour.com",
    "emprende.cl",
    "facebook.com",
}

TITULO_BASURA = [
    "10 panoramas",
    "10 cosas",
    "diez panoramas",
    "5 imperdibles",
    "los mejores panoramas",
    "qué hacer en santiago",
    "que hacer en santiago",
    "agenda cultural",
    "cartelera",
    "panoramas del fin de semana",
    "home -",
    "guía de",
    "guia de",
    "funcionamiento de",
    "municipalidad de",
    "free tour",
    "tour gastronómico",
    "tour gastronomico",
]

SYSTEM_PROMPT = f"""Eres un verificador MUY ESTRICTO de eventos presenciales gratis.

Fecha actual de referencia: {hoy_iso}.
Solo apruebas eventos que ocurren en Santiago de Chile, DESPUÉS del 2026-03-18.

Responde SOLO JSON:
Si apruebas:
{{
  "ok": true,
  "nombre": "...",
  "lugar": "...",
  "comuna": "...",
  "fecha": "texto legible",
  "fecha_iso": "YYYY-MM-DD",
  "hora": "HH:MM o null",
  "desc": "qué pasa y por qué vale la pena",
  "cat": "degustacion|inauguracion|popup|activacion|arte|feria|musica|experiencia|otro",
  "gratis": true,
  "exclusive_score": 0-5,
  "motivo_exclusivo": "por qué se siente especial/exclusivo",
  "fuente": "instagram|tiktok|web",
  "link": "..."
}}
Si rechazas:
{{"ok": false, "r": "motivo breve"}}

APRUEBA solo si TODO esto se cumple:
- Evento presencial en Santiago de Chile.
- Fecha real verificable entre 2026-03-19 y 2026-12-31.
- Gratis / entrada liberada / sin pago.
- Debe ser específico y atractivo: inauguración, degustación gratis, activación, lanzamiento, pop-up temporal, experiencia o evento especial.
- Debe verse como algo puntual y no una nota vieja, resumen, reel reutilizado o simple video mostrando un lugar.
- Debe incluir al menos nombre o marca/espacio reconocible + lugar concreto.

RECHAZA si detectas cualquiera de estos casos:
- Fecha de 2025 o anterior, o fecha ambigua sin evidencia.
- Contenido que solo habla de cómo quedó un lugar, sin evento real.
- Artículo/lista/agenda genérica.
- Evento fuera de Santiago.
- Venta, feria comercial común, clases, tour permanente, inscripción, ticketing.
- MLM, pirámide, captación, negocio coreano, "gana dinero", networking engañoso.
- Publicación reciente que promociona algo ya ocurrido.
"""


# ── Cache de procesadas ─────────────────────────────────────────────────────
def cargar_procesadas() -> dict[str, dict]:
    cache: dict[str, dict] = {}
    if not PROCESADAS_PATH.exists():
        return cache

    for line in PROCESADAS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = data.get("url")
        if url:
            cache[url] = data
    return cache


def guardar_procesada(url: str, estado: str, detalle: str, fecha_iso: str | None = None):
    entry = {
        "url": url,
        "estado": estado,
        "detalle": detalle,
        "fecha_iso": fecha_iso,
        "procesado_en": datetime.now(timezone.utc).isoformat(),
    }
    with PROCESADAS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── Helpers ─────────────────────────────────────────────────────────────────
def normalizar(texto: str) -> str:
    return (texto or "").strip().lower()


def extraer_dominio(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def contiene_keywords(texto: str, keywords: list[str]) -> bool:
    texto = normalizar(texto)
    return any(k in texto for k in keywords)


# ── Pre-filtro Python (sin IA, sin costo) ────────────────────────────────────
def prefiltro(r: dict) -> tuple[bool, str]:
    url = r.get("url", "")
    dominio = extraer_dominio(url)
    titulo = normalizar(r.get("title", ""))
    snippet = normalizar(r.get("content", ""))
    texto = f"{titulo} {snippet}"

    if any(d in dominio for d in DOMINIOS_BLOQUEADOS):
        return False, f"dominio bloqueado ({dominio})"

    if any(b in titulo for b in TITULO_BASURA):
        return False, "título genérico o nota/agenda"

    if any(year in texto for year in ["2024", "2025"]):
        return False, "menciona años pasados"

    if "2026" not in texto and not any(k in texto for k in ["marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]):
        return False, "sin señal temporal suficiente"

    if contiene_keywords(texto, KEYWORDS_ESTAFA):
        return False, "posible estafa / captación"

    if not contiene_keywords(texto, KEYWORDS_EXCLUSIVOS):
        return False, "no parece evento especial/exclusivo"

    if "santiago" not in texto:
        return False, "no menciona Santiago"

    if any(comuna in texto for comuna in ["providencia", "las condes", "vitacura", "ñuñoa", "nunoa", "maipú", "maipu", "la reina"]):
        return False, "fuera de comuna de Santiago"

    pub = r.get("published_date", "")
    if pub:
        try:
            fecha_pub = datetime.fromisoformat(pub.replace("Z", "+00:00")).date()
            if fecha_pub < hoy_dt - timedelta(days=45):
                return False, f"publicación demasiado antigua ({fecha_pub})"
        except ValueError:
            pass

    return True, ""


# ── Tavily search ────────────────────────────────────────────────────────────
async def tavily_search(client: httpx.AsyncClient, query: str) -> list[dict]:
    try:
        r = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "advanced",
                "max_results": 6,
                "include_answer": False,
                "days": 30,
            },
            timeout=25,
        )
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as e:
        print(f"[Tavily error] {query}: {e}")
        return []


# ── Groq filter ──────────────────────────────────────────────────────────────
def groq_evaluar(resultado: dict) -> dict | None:
    groq = Groq(api_key=GROQ_API_KEY)
    snippet = resultado.get("content", "")[:900]
    contenido = (
        f"Título: {resultado.get('title', '')}\n"
        f"Snippet: {snippet}\n"
        f"Publicado: {resultado.get('published_date', '?')}\n"
        f"URL: {resultado.get('url', '')}"
    )
    try:
        chat = groq.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": contenido},
            ],
            temperature=0.0,
            max_tokens=320,
            response_format={"type": "json_object"},
        )
        data = json.loads(chat.choices[0].message.content)

        if not data.get("ok"):
            print(f"  → ❌ {data.get('r', '')}")
            return None

        fecha_iso = data.get("fecha_iso")
        if not fecha_iso:
            print("  → ❌ sin fecha_iso")
            return None

        fecha_evento = date.fromisoformat(fecha_iso)
        if fecha_evento < FECHA_MINIMA:
            print(f"  → ❌ fecha fuera de rango ({fecha_iso})")
            return None

        if fecha_evento.year != ANO_OBJETIVO:
            print(f"  → ❌ no corresponde a {ANO_OBJETIVO} ({fecha_iso})")
            return None

        if not data.get("gratis", False):
            print("  → ❌ no es gratis")
            return None

        if int(data.get("exclusive_score", 0)) < 2:
            print("  → ❌ poco exclusivo")
            return None

        comuna = normalizar(data.get("comuna", ""))
        lugar = normalizar(data.get("lugar", ""))
        if not any(c in f"{comuna} {lugar}" for c in COMUNAS_PERMITIDAS):
            print(f"  → ❌ comuna fuera del foco ({data.get('comuna', '')})")
            return None

        return data

    except Exception as e:
        msg = str(e)
        if "429" in msg:
            print("  → ⚠️ Rate limit Groq")
        else:
            print(f"  → ⚠️ Groq error: {msg[:120]}")
        return None


# ── Formatear ────────────────────────────────────────────────────────────────
def formatear_evento(ev: dict) -> str:
    cat_label = CATEGORIAS.get(ev.get("cat", "otro"), "📌 Evento")
    hora = ev.get("hora") or "Por confirmar"
    comuna = ev.get("comuna") or "Santiago"
    fuente = ev.get("fuente") or extraer_dominio(ev.get("link", ""))
    motivo_exclusivo = ev.get("motivo_exclusivo") or "Edición puntual"
    return (
        f"{cat_label} — <b>{ev['nombre']}</b>\n"
        f"📍 <b>Lugar:</b> {ev['lugar']} ({comuna})\n"
        f"🗓 <b>Fecha:</b> {ev['fecha']}\n"
        f"🕒 <b>Hora:</b> {hora}\n"
        f"🎟 <b>Acceso:</b> Gratis\n"
        f"✨ <b>Qué pasa:</b> {ev['desc']}\n"
        f"🔐 <b>Qué lo hace especial:</b> {motivo_exclusivo}\n"
        f"🌐 <b>Fuente:</b> {fuente}\n"
        f"🔗 {ev['link']}"
    )


# ── Telegram ─────────────────────────────────────────────────────────────────
async def telegram_send(client: httpx.AsyncClient, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    await client.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=15,
    )


# ── Main ─────────────────────────────────────────────────────────────────────
async def main():
    aprobados: list[dict] = []
    urls_vistas: set[str] = set()
    procesadas = cargar_procesadas()
    stats = {
        "total": 0,
        "skip_cache": 0,
        "skip_pre": 0,
        "rechazados_groq": 0,
        "aprobados": 0,
    }

    async with httpx.AsyncClient() as client:
        resultados_por_query = await asyncio.gather(*(tavily_search(client, q) for q in QUERIES))

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
            url = r.get("url", "")
            titulo = r.get("title", "")[:80]

            if url in procesadas:
                previo = procesadas[url]
                print(f"[Cache-skip] {titulo} — {previo.get('estado')}: {previo.get('detalle')}")
                stats["skip_cache"] += 1
                continue

            pasa, motivo = prefiltro(r)
            if not pasa:
                print(f"[Pre-skip] {titulo} — {motivo}")
                stats["skip_pre"] += 1
                guardar_procesada(url, "prefiltro_rechazado", motivo)
                procesadas[url] = {"estado": "prefiltro_rechazado", "detalle": motivo}
                continue

            print(f"[→ Groq] {titulo}")
            ev = groq_evaluar(r)
            if ev:
                aprobados.append(ev)
                stats["aprobados"] += 1
                guardar_procesada(url, "aprobado", ev.get("nombre", "ok"), ev.get("fecha_iso"))
                procesadas[url] = {"estado": "aprobado", "detalle": ev.get("nombre", "ok")}
                print(f"  → ✅ {ev['nombre']} [{ev.get('cat', '?')}] {ev.get('fecha_iso')}")
            else:
                stats["rechazados_groq"] += 1
                guardar_procesada(url, "groq_rechazado", "sin evidencia suficiente")
                procesadas[url] = {"estado": "groq_rechazado", "detalle": "sin evidencia suficiente"}

        aprobados = sorted(
            aprobados,
            key=lambda e: (e.get("fecha_iso") or "9999-99-99", e.get("hora") or "99:99"),
        )[:MAX_EVENTOS]

        print(
            "\n[Stats] "
            f"total={stats['total']} | cache={stats['skip_cache']} | pre-skip={stats['skip_pre']} | "
            f"groq-rechazó={stats['rechazados_groq']} | aprobados={stats['aprobados']}"
        )

        if not aprobados:
            await telegram_send(
                client,
                (
                    f"🔎 <b>Eventos exclusivos y gratis en Santiago</b>\n"
                    f"📅 Desde el 19 de marzo de 2026\n\n"
                    "No encontré eventos que cumplan el filtro estricto: gratis, en Santiago y con fecha verificable de 2026."
                ),
            )
        else:
            await telegram_send(
                client,
                (
                    f"🗺 <b>Eventos exclusivos y gratis — Santiago de Chile</b>\n"
                    f"📅 Filtro: posteriores al 18 de marzo de 2026\n"
                    f"✅ Encontrados: <b>{len(aprobados)}</b> evento(s) con fecha verificable"
                ),
            )
            for ev in aprobados:
                await telegram_send(client, formatear_evento(ev))

        print(f"[Done] {len(aprobados)} evento(s) enviados")


if __name__ == "__main__":
    asyncio.run(main())
