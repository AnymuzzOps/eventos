import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from groq import Groq

# ── Config ──────────────────────────────────────────────────────────────────
TAVILY_API_KEY = os.environ["TAVILY_API_KEY"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

GROQ_MODEL = "llama-3.3-70b-versatile"
PROCESADAS_PATH = Path("procesadas.txt")
MAX_EVENTOS = 12
MAX_CANDIDATOS_GROQ = 32
EXTRACT_CHARS = 5000
CACHE_TTL_APROBADO_DIAS = 90
CACHE_TTL_RECHAZADO_DIAS = 10

CHILE_TZ = timezone(timedelta(hours=-3))
ahora = datetime.now(CHILE_TZ)
hoy_dt = ahora.date()
hoy = ahora.strftime("%-d de %B de %Y")
hoy_iso = hoy_dt.isoformat()
ANO_OBJETIVO = 2026
FECHA_MINIMA = date(2026, 3, 19)
FECHA_MAXIMA = date(2026, 9, 30)
MESES_BUSQUEDA = [
    "marzo 2026",
    "abril 2026",
    "mayo 2026",
    "junio 2026",
    "julio 2026",
    "agosto 2026",
    "septiembre 2026",
]


# ── Diccionarios ─────────────────────────────────────────────────────────────
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
    "centro de santiago",
    "casco histórico",
    "casco historico",
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
    "matucana",
    "recoleta",
    "patronato",
    "estación central",
    "estacion central",
    "mercado central",
    "san diego",
    "club hípico",
    "club hipico",
}

COMUNAS_EXCLUIDAS = [
    "providencia",
    "las condes",
    "vitacura",
    "ñuñoa",
    "nunoa",
    "maipú",
    "maipu",
    "la reina",
    "peñalolén",
    "penalolen",
    "lo barnechea",
    "pudahuel",
    "huechuraba",
    "quilicura",
    "san miguel",
]

TIPOS_EVENTO = [
    "inauguración",
    "degustación",
    "activación",
    "lanzamiento",
    "pop up",
    "apertura",
    "experiencia",
    "feria gastronómica",
    "festival gratis",
    "música en vivo gratis",
]

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
    "showroom",
    "guest",
    "market temporal",
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
    "mentor financiero",
    "inversión garantizada",
    "inversion garantizada",
    "seminario de negocios",
]

KEYWORDS_RELIGION = [
    "dios",
    "iglesia",
    "cristo",
    "jesús",
    "jesus",
    "evangelismo",
    "adoración",
    "adoracion",
    "oración",
    "oracion",
    "espiritual",
    "espiritualidad",
    "profético",
    "profetico",
    "ministerio",
    "culto",
    "avivamiento",
    "predica",
]

KEYWORDS_EVENTO_PAGO = [
    "lollapalooza",
    "festival pagado",
    "ticket requerido",
    "requiere entrada",
    "requiere ticket",
    "dentro de lollapalooza",
    "solo para asistentes",
    "cargadores gratis",
    "beneficio para asistentes",
]

KEYWORDS_GRATIS = [
    "gratis",
    "gratuito",
    "gratuita",
    "entrada liberada",
    "entrada libre",
    "sin costo",
    "free",
    "free entry",
    "liberado",
]

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
    "discover",
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

DOMINIOS_PRIORITARIOS = {
    "instagram.com",
    "tiktok.com",
    "santiagocultura.cl",
    "chilecultura.gob.cl",
    "santiagoturismo.cl",
    "biobiochile.cl",
    "publimetro.cl",
    "theclinic.cl",
    "df.cl",
    "latercera.com",
    "lacuarta.com",
    "adnradio.cl",
    "chvnoticias.cl",
}


# ── Prompt ──────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = f"""Eres un verificador de eventos presenciales gratis.

Fecha actual de referencia: {hoy_iso}.
Debes encontrar eventos en Santiago de Chile posteriores al 2026-03-18.
El objetivo es detectar eventos reales y atractivos como inauguraciones, degustaciones gratis, activaciones, lanzamientos, pop-ups temporales, festivales puntuales o experiencias especiales.

Responde SOLO JSON.
Si apruebas:
{{
  "ok": true,
  "nombre": "...",
  "lugar": "...",
  "comuna": "...",
  "fecha": "texto legible",
  "fecha_iso": "YYYY-MM-DD",
  "hora": "HH:MM o null",
  "desc": "resumen claro del evento",
  "cat": "degustacion|inauguracion|popup|activacion|arte|feria|musica|experiencia|otro",
  "gratis": true,
  "exclusive_score": 0-5,
  "motivo_exclusivo": "por qué se siente especial o distinto",
  "evidencia_fecha": "qué texto o dato respalda la fecha",
  "fuente": "instagram|tiktok|web",
  "link": "..."
}}
Si rechazas:
{{"ok": false, "r": "motivo breve"}}

APRUEBA si se cumplen estas condiciones:
- Es un evento presencial en Santiago de Chile.
- La fecha del evento es verificable y ocurre entre 2026-03-19 y 2026-09-30.
- Se ve gratis o entrada liberada.
- Hay suficiente evidencia en título, snippet o contenido extraído.
- Puede ser especial por formato, marca, apertura, experiencia puntual, festival o cupos limitados. No hace falta que use la palabra "exclusivo" literal.

RECHAZA si pasa cualquiera de estas cosas:
- El contenido solo muestra un lugar o resume panoramas, sin evento puntual.
- Es una página de búsqueda, discover, hashtag, perfil o query genérica, no un post/evento concreto.
- Es una activación secundaria dentro de un festival o evento principal pagado.
- Tiene enfoque religioso o espiritual.
- Publicación reciente sobre un evento ya terminado.
- Fecha 2025 o anterior, o sin evidencia mínima.
- Si la fecha está en formato "27 de junio" sin año, debes confirmar con el contexto que sea 2026; si el contexto apunta a 2025 o es ambiguo, rechaza.
- Es venta, ticketing, curso, feria comercial común, tour permanente o pauta evergreen.
- Es MLM, captación, seminario para ganar dinero o posible estafa.
- Está fuera de Santiago.

Si la evidencia es parcial pero razonable, aprueba solo si la fecha y gratuidad están respaldadas por el contenido.
"""


# ── Queries ─────────────────────────────────────────────────────────────────
def construir_queries() -> list[str]:
    base_queries = [
        f"eventos gratis Santiago Chile {ANO_OBJETIVO}",
        f"eventos gratis santiago centro {ANO_OBJETIVO}",
        f"eventos con entrada liberada Santiago {ANO_OBJETIVO}",
        f"site:santiagocultura.cl Santiago gratis {ANO_OBJETIVO}",
        f"site:chilecultura.gob.cl Santiago gratis {ANO_OBJETIVO}",
        f"site:santiagoturismo.cl Santiago evento gratis {ANO_OBJETIVO}",
    ]

    for mes in MESES_BUSQUEDA:
        for tipo in TIPOS_EVENTO:
            base_queries.append(f"{tipo} gratis Santiago Chile {mes}")
            base_queries.append(f"{tipo} Santiago centro gratis {mes}")

        base_queries.extend(
            [
                f"site:instagram.com/p/ inauguración Santiago gratis {mes}",
                f"site:instagram.com/p/ degustación Santiago gratis {mes}",
                f"site:instagram.com/p/ pop up Santiago centro {mes}",
                f"site:instagram.com/p/ activación Santiago {mes}",
                f"site:tiktok.com Santiago gratis inauguración {mes}",
                f"site:biobiochile.cl Santiago gratis {mes}",
                f"site:latercera.com Santiago gratis {mes}",
                f"site:publimetro.cl Santiago evento gratis {mes}",
            ]
        )

    return list(dict.fromkeys(base_queries))


QUERIES = construir_queries()


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


def deberia_omitir_por_cache(entry: dict) -> bool:
    procesado_en = entry.get("procesado_en")
    estado = entry.get("estado", "")
    if not procesado_en:
        return False

    try:
        fecha_cache = datetime.fromisoformat(procesado_en.replace("Z", "+00:00"))
    except ValueError:
        return False

    edad = datetime.now(timezone.utc) - fecha_cache
    ttl_dias = CACHE_TTL_APROBADO_DIAS if estado == "aprobado" else CACHE_TTL_RECHAZADO_DIAS
    return edad <= timedelta(days=ttl_dias)


# ── Helpers ─────────────────────────────────────────────────────────────────
def normalizar(texto: str) -> str:
    return (texto or "").strip().lower()


def extraer_dominio(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def es_url_busqueda(url: str) -> bool:
    parsed = urlparse(url)
    path = normalizar(parsed.path)
    query = parse_qs(parsed.query)

    if "/discover/" in path or "/search" in path or "/explore/" in path:
        return True

    if any(key in query for key in ["q", "query", "keyword", "search"]):
        return True

    return False


def contiene_keywords(texto: str, keywords: list[str]) -> bool:
    texto = normalizar(texto)
    return any(k in texto for k in keywords)


def extraer_texto_base(r: dict) -> str:
    return " ".join(
        [
            normalizar(r.get("title", "")),
            normalizar(r.get("content", "")),
            normalizar(r.get("raw_content", "")),
        ]
    ).strip()


def dedupe_key_evento(ev: dict) -> str:
    nombre = normalizar(ev.get("nombre", ""))
    fecha_iso = ev.get("fecha_iso", "")
    lugar = normalizar(ev.get("lugar", ""))
    return f"{nombre}|{fecha_iso}|{lugar}"


def score_resultado(r: dict) -> tuple[int, list[str]]:
    texto = extraer_texto_base(r)
    dominio = extraer_dominio(r.get("url", ""))
    score = 0
    razones: list[str] = []

    if dominio in DOMINIOS_PRIORITARIOS:
        score += 2
        razones.append("dominio_prioritario")

    if "2026" in texto:
        score += 3
        razones.append("año_2026")

    if any(mes in texto for mes in ["marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre"]):
        score += 2
        razones.append("mes_objetivo")

    if contiene_keywords(texto, KEYWORDS_GRATIS):
        score += 2
        razones.append("gratis")

    if contiene_keywords(texto, KEYWORDS_EXCLUSIVOS):
        score += 2
        razones.append("tipo_evento")

    if "santiago" in texto:
        score += 2
        razones.append("santiago")

    if any(c in texto for c in COMUNAS_PERMITIDAS):
        score += 2
        razones.append("comuna_foco")

    pub = r.get("published_date", "")
    if pub:
        try:
            fecha_pub = datetime.fromisoformat(pub.replace("Z", "+00:00")).date()
            if fecha_pub.year == ANO_OBJETIVO:
                score += 1
                razones.append("publicacion_2026")
            elif fecha_pub < FECHA_MINIMA - timedelta(days=180):
                score -= 3
                razones.append("publicacion_muy_antigua")
        except ValueError:
            pass

    if "instagram.com" in dominio or "tiktok.com" in dominio:
        score += 1
        razones.append("social_source")

    return score, razones


# ── Pre-filtro Python (sin IA, sin costo) ────────────────────────────────────
def prefiltro(r: dict) -> tuple[bool, str, int, list[str]]:
    url = r.get("url", "")
    dominio = extraer_dominio(url)
    titulo = normalizar(r.get("title", ""))
    texto = extraer_texto_base(r)
    score, razones = score_resultado(r)

    if any(d in dominio for d in DOMINIOS_BLOQUEADOS):
        return False, f"dominio bloqueado ({dominio})", score, razones

    if es_url_busqueda(url):
        return False, "url de búsqueda/discover, no evento concreto", score, razones

    if any(b in titulo for b in TITULO_BASURA):
        return False, "título genérico o nota/agenda", score, razones

    if any(year in texto for year in ["2024", "2025"]):
        return False, "menciona años pasados", score, razones

    if contiene_keywords(texto, KEYWORDS_ESTAFA):
        return False, "posible estafa / captación", score, razones

    if contiene_keywords(texto, KEYWORDS_RELIGION):
        return False, "contenido religioso/espiritual", score, razones

    if contiene_keywords(texto, KEYWORDS_EVENTO_PAGO):
        return False, "activación asociada a evento pagado", score, razones

    if any(comuna in texto for comuna in COMUNAS_EXCLUIDAS):
        return False, "fuera de comuna de Santiago", score, razones

    if "santiago" not in texto and not any(c in texto for c in COMUNAS_PERMITIDAS):
        return False, "sin señal de Santiago", score, razones

    if score < 3:
        return False, f"score insuficiente ({score})", score, razones

    return True, "", score, razones


# ── Tavily ───────────────────────────────────────────────────────────────────
async def tavily_search(client: httpx.AsyncClient, query: str) -> list[dict]:
    try:
        r = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "advanced",
                "max_results": 8,
                "include_answer": False,
                "include_raw_content": False,
                "days": 210,
            },
            timeout=35,
        )
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as e:
        print(f"[Tavily search error] {query}: {e}")
        return []


async def tavily_extract(client: httpx.AsyncClient, urls: list[str]) -> dict[str, str]:
    if not urls:
        return {}

    try:
        r = await client.post(
            "https://api.tavily.com/extract",
            json={
                "api_key": TAVILY_API_KEY,
                "urls": urls,
                "extract_depth": "advanced",
            },
            timeout=45,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception as e:
        print(f"[Tavily extract error] {e}")
        return {}

    extraidos: dict[str, str] = {}
    for item in results:
        url = item.get("url")
        contenido = item.get("raw_content") or item.get("content") or ""
        if url and contenido:
            extraidos[url] = contenido[:EXTRACT_CHARS]
    return extraidos


# ── Groq filter ──────────────────────────────────────────────────────────────
def groq_evaluar(resultado: dict) -> dict | None:
    groq = Groq(api_key=GROQ_API_KEY)
    snippet = resultado.get("content", "")[:1500]
    raw_content = resultado.get("raw_content", "")[:EXTRACT_CHARS]
    contenido = (
        f"Título: {resultado.get('title', '')}\n"
        f"Snippet: {snippet}\n"
        f"Publicado: {resultado.get('published_date', '?')}\n"
        f"URL: {resultado.get('url', '')}\n"
        f"Contenido extraído: {raw_content}"
    )
    try:
        chat = groq.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": contenido},
            ],
            temperature=0.0,
            max_tokens=420,
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
        if not (FECHA_MINIMA <= fecha_evento <= FECHA_MAXIMA):
            print(f"  → ❌ fecha fuera de rango ({fecha_iso})")
            return None

        if not data.get("gratis", False):
            print("  → ❌ no es gratis")
            return None

        if int(data.get("exclusive_score", 0)) < 1:
            print("  → ❌ poco especial")
            return None

        texto = extraer_texto_base(resultado)
        if contiene_keywords(texto, KEYWORDS_RELIGION):
            print("  → ❌ religioso")
            return None

        if contiene_keywords(texto, KEYWORDS_EVENTO_PAGO):
            print("  → ❌ activación de evento pagado")
            return None

        comuna = normalizar(data.get("comuna", ""))
        lugar = normalizar(data.get("lugar", ""))
        contexto = f"{comuna} {lugar}"
        if not any(c in contexto for c in COMUNAS_PERMITIDAS):
            print(f"  → ❌ comuna fuera del foco ({data.get('comuna', '')})")
            return None

        data["link"] = resultado.get("url", data.get("link", ""))
        return data

    except Exception as e:
        msg = str(e)
        if "429" in msg:
            print("  → ⚠️ Rate limit Groq")
        else:
            print(f"  → ⚠️ Groq error: {msg[:140]}")
        return None


# ── Formatear ────────────────────────────────────────────────────────────────
def formatear_evento(ev: dict) -> str:
    cat_label = CATEGORIAS.get(ev.get("cat", "otro"), "📌 Evento")
    hora = ev.get("hora") or "Por confirmar"
    comuna = ev.get("comuna") or "Santiago"
    fuente = ev.get("fuente") or extraer_dominio(ev.get("link", ""))
    motivo_exclusivo = ev.get("motivo_exclusivo") or "Se ve como una fecha puntual con valor especial."
    evidencia_fecha = ev.get("evidencia_fecha") or "Fecha identificada en la publicación o contenido fuente."
    return (
        f"{cat_label} — <b>{ev['nombre']}</b>\n"
        f"📍 <b>Lugar:</b> {ev['lugar']} ({comuna})\n"
        f"🗓 <b>Fecha:</b> {ev['fecha']}\n"
        f"🕒 <b>Hora:</b> {hora}\n"
        f"🎟 <b>Acceso:</b> Gratis\n"
        f"✨ <b>Qué pasa:</b> {ev['desc']}\n"
        f"🔐 <b>Por qué destaca:</b> {motivo_exclusivo}\n"
        f"🧾 <b>Evidencia de fecha:</b> {evidencia_fecha}\n"
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
        timeout=20,
    )


# ── Main ─────────────────────────────────────────────────────────────────────
async def main():
    aprobados: list[dict] = []
    dedupe_eventos: set[str] = set()
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

        candidatos: list[dict] = []
        for r in todos:
            url = r.get("url", "")
            titulo = r.get("title", "")[:90]

            if url in procesadas and deberia_omitir_por_cache(procesadas[url]):
                previo = procesadas[url]
                print(f"[Cache-skip] {titulo} — {previo.get('estado')}: {previo.get('detalle')}")
                stats["skip_cache"] += 1
                continue

            pasa, motivo, score, razones = prefiltro(r)
            if not pasa:
                print(f"[Pre-skip] {titulo} — {motivo}")
                stats["skip_pre"] += 1
                guardar_procesada(url, "prefiltro_rechazado", motivo)
                procesadas[url] = {
                    "estado": "prefiltro_rechazado",
                    "detalle": motivo,
                    "procesado_en": datetime.now(timezone.utc).isoformat(),
                }
                continue

            r["prefiltro_score"] = score
            r["prefiltro_razones"] = razones
            candidatos.append(r)
            print(f"[Candidato] {titulo} — score={score} razones={','.join(razones)}")

        candidatos.sort(key=lambda r: r.get("prefiltro_score", 0), reverse=True)
        candidatos = candidatos[:MAX_CANDIDATOS_GROQ]

        extraidos = await tavily_extract(client, [r["url"] for r in candidatos])
        for r in candidatos:
            r["raw_content"] = extraidos.get(r["url"], "")

        for r in candidatos:
            url = r.get("url", "")
            titulo = r.get("title", "")[:90]
            print(f"[→ Groq] {titulo} — score={r.get('prefiltro_score')}")
            ev = groq_evaluar(r)
            if ev:
                clave = dedupe_key_evento(ev)
                if clave in dedupe_eventos:
                    print(f"  → ↪ duplicado {ev['nombre']} {ev.get('fecha_iso')}")
                    continue
                dedupe_eventos.add(clave)
                aprobados.append(ev)
                stats["aprobados"] += 1
                guardar_procesada(url, "aprobado", ev.get("nombre", "ok"), ev.get("fecha_iso"))
                procesadas[url] = {
                    "estado": "aprobado",
                    "detalle": ev.get("nombre", "ok"),
                    "procesado_en": datetime.now(timezone.utc).isoformat(),
                }
                print(f"  → ✅ {ev['nombre']} [{ev.get('cat', '?')}] {ev.get('fecha_iso')}")
            else:
                stats["rechazados_groq"] += 1
                guardar_procesada(url, "groq_rechazado", "sin evidencia suficiente")
                procesadas[url] = {
                    "estado": "groq_rechazado",
                    "detalle": "sin evidencia suficiente",
                    "procesado_en": datetime.now(timezone.utc).isoformat(),
                }

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
                    f"📅 Ventana: 19 de marzo a 30 de septiembre de 2026\n\n"
                    "No encontré resultados con evidencia suficiente de fecha y gratuidad. "
                    "El bot descartó notas genéricas, búsquedas vacías, eventos pasados, religión y publicaciones sospechosas."
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
