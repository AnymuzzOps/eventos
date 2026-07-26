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
SPANISH_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def parse_bool(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "si", "sí"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} debe ser true o false")


@dataclass(frozen=True)
class Config:
    tavily_api_key: str
    groq_api_key: str
    telegram_token: str
    telegram_chat_id: str
    start_date: date
    end_date: date
    city: str
    region: str
    only_free: bool
    couple_mode: bool
    max_events: int
    dry_run: bool
    force_resend: bool
    manual_run: bool
    groq_model: str = "llama-3.3-70b-versatile"

    @classmethod
    def from_env(cls, require_secrets: bool = True) -> "Config":
        try:
            start = date.fromisoformat(os.getenv("EVENT_START_DATE", "2026-08-01"))
            end = date.fromisoformat(os.getenv("EVENT_END_DATE", "2026-08-31"))
        except ValueError as error:
            raise ValueError("EVENT_START_DATE y EVENT_END_DATE deben usar YYYY-MM-DD") from error
        if start > end:
            raise ValueError("EVENT_START_DATE no puede ser posterior a EVENT_END_DATE")
        try:
            maximum = int(os.getenv("MAX_EVENTS_PER_RUN", "20"))
        except ValueError as error:
            raise ValueError("MAX_EVENTS_PER_RUN debe ser un entero") from error
        if not 1 <= maximum <= 100:
            raise ValueError("MAX_EVENTS_PER_RUN debe estar entre 1 y 100")
        config = cls(
            tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
            telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            start_date=start,
            end_date=end,
            city=os.getenv("EVENT_CITY", "Santiago").strip() or "Santiago",
            region=os.getenv("EVENT_REGION", "Región Metropolitana").strip()
            or "Región Metropolitana",
            only_free=parse_bool(os.getenv("ONLY_FREE", "true"), "ONLY_FREE"),
            couple_mode=parse_bool(os.getenv("COUPLE_MODE", "true"), "COUPLE_MODE"),
            max_events=maximum,
            dry_run=parse_bool(os.getenv("DRY_RUN", "false"), "DRY_RUN"),
            force_resend=parse_bool(os.getenv("FORCE_RESEND", "false"), "FORCE_RESEND"),
            manual_run=os.getenv("GITHUB_EVENT_NAME", "") == "workflow_dispatch",
        )
        if require_secrets and not config.dry_run:
            required = {
                "TAVILY_API_KEY": config.tavily_api_key,
                "GROQ_API_KEY": config.groq_api_key,
                "TELEGRAM_TOKEN": config.telegram_token,
                "TELEGRAM_CHAT_ID": config.telegram_chat_id,
            }
            missing = [key for key, value in required.items() if not value]
            if missing:
                raise RuntimeError("Faltan variables de entorno requeridas: " + ", ".join(missing))
        return config

LCOMUNAS_PERMITIDAS = {
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
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def validate_panorama(item: dict, config: Config, today: date | None = None) -> tuple[bool, str]:
    today = today or datetime.now(CHILE_TZ).date()
    start = parse_event_date(item.get("fecha_inicio"))
    end = parse_event_date(item.get("fecha_fin")) or start
    if not start or not end:
        return False, "fecha no comprobable"
    if end < config.start_date or start > config.end_date or end < today:
        return False, "fecha fuera del periodo o vencida"
    if start.year != config.start_date.year or end.year != config.end_date.year:
        return False, "información correspondiente a otro año"
    if item.get("es_gratis") is not True:
        return False, "gratuidad no confirmada"
    price = normalize(item.get("precio_texto"))
    if any(word in price for word in BLOCKED_PRICE_WORDS) or not any(
        word in price for word in ("gratis", "gratuito", "entrada liberada", "sin costo")
    ):
        return False, "precio no informado claramente como gratuito"
    if item.get("requiere_reserva") and item.get("reserva_gratuita") is not True:
        return False, "reserva no confirmada como gratuita"
    if not is_rm_comuna(item.get("comuna")):
        return False, "comuna fuera de la Región Metropolitana"
    source_url = str(item.get("url_fuente") or "")
    if not source_url.startswith(("https://", "http://")) or is_search_url(source_url):
        return False, "fuente directa no verificable"
    if int(item.get("confianza") or 0) < MIN_CONFIDENCE:
        return False, "confianza baja"
    if config.couple_mode and item.get("apto_para_pareja") is not True:
        return False, "no apto para una salida en pareja"
    return True, ""


def dedupe_key(item: dict) -> str:
    fields = (
        item.get("titulo"),
        item.get("fecha_inicio"),
        item.get("direccion"),
        item.get("comuna"),
        domain(str(item.get("url_fuente") or "")),
    )
    return "|".join(normalize(field) for field in fields)


def content_fingerprint(item: dict) -> str:
    relevant = {
        key: item.get(key)
        for key in (
            "titulo",
            "fecha_inicio",
            "fecha_fin",
            "horario",
            "comuna",
            "direccion",
            "requiere_reserva",
            "url_reserva",
            "url_fuente",
            "precio_texto",
        )
    }
    return hashlib.sha256(
        json.dumps(relevant, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def prefer_official(items: list[dict]) -> list[dict]:
    chosen: dict[str, dict] = {}
    for item in items:
        key = "|".join(normalize(item.get(field)) for field in ("titulo", "fecha_inicio", "comuna"))
        current = chosen.get(key)
        if current is None or (
            is_official_source(str(item.get("url_fuente", "")), str(item.get("fuente", "")))
            and not is_official_source(
                str(current.get("url_fuente", "")), str(current.get("fuente", ""))
            )
        ):
            chosen[key] = item
    return list(chosen.values())


def build_queries(config: Config) -> list[str]:
    month = config.start_date.strftime("%m/%Y")
    month_name = SPANISH_MONTHS[config.start_date.month - 1]
    month_year = f"{month_name} {config.start_date.year}"
    period = f"{config.start_date.isoformat()} {config.end_date.isoformat()}"
    base = [
        f"eventos gratis {config.city} {month_year}",
        f"panoramas gratuitos {config.city} {month_year}",
        f"actividades gratuitas {config.region} {month_year}",
        "jardines gratuitos Santiago",
        "parques para visitar en pareja Santiago",
        "museos gratis Santiago",
        f"exposiciones gratuitas {config.city} {month_year}",
        f"conciertos gratis {config.city} {month_year}",
        f"actividades municipales {month_year} {config.region}",
        f"entrada liberada {config.city} {month_year}",
        f"recorridos patrimoniales gratis {config.region} {month}",
        f"cine al aire libre gratis {config.region} {period}",
        f"site:chilecultura.gob.cl gratis {config.region} {month_year}",
        f"site:santiagocultura.cl gratis {month_year}",
        f"site:parquemet.cl actividades gratis {period}",
        f"museos bibliotecas centros culturales entrada liberada {config.region} {period}",
    ]
    for comuna in COMUNAS_BUSQUEDA:
        base.append(f"site:*.cl actividades gratis {comuna} {month_year} municipalidad cultura")
        base.append(f"panoramas gratis {comuna} {month_year} fuente oficial")
    return list(dict.fromkeys(base))


def system_prompt(config: Config) -> str:
    return f"""Eres un extractor estricto de panoramas gratuitos para adultos en pareja.
Periodo permitido: {config.start_date.isoformat()} a {config.end_date.isoformat()} en America/Santiago.
Ubicación: {config.city}, {config.region}, Chile. No inventes ni completes datos sin respaldo.
Rechaza pagos, precio ambiguo, online, exclusivamente infantil, sorteos, publicidad, otra región/año,
fuentes sin fecha y reservas no confirmadas como gratuitas. Para lugares permanentes exige evidencia
de disponibilidad dentro del periodo. Prioriza fuente oficial. Responde SOLO un objeto JSON con:
{{"titulo":"","categoria":"","descripcion":"","fecha_inicio":"YYYY-MM-DD","fecha_fin":"YYYY-MM-DD",
"horario":"","comuna":"","direccion":"","es_gratis":true,"precio_texto":"",
"requiere_reserva":false,"reserva_gratuita":false,"url_reserva":"","fuente":"","url_fuente":"",
"interior_exterior":"interior|exterior|mixta|no informado","recomendacion_lluvia":"",
"apto_para_pareja":true,"razon_para_pareja":"","confianza":0}}.
Usa "No informado" solo en campos no esenciales. URL, comuna, fecha y gratuidad son esenciales."""


def prefilter(result: dict) -> bool:
    text = normalize(" ".join(str(result.get(k, "")) for k in ("title", "content", "raw_content")))
    url = str(result.get("url", ""))
    if not url.startswith(("http://", "https://")) or is_search_url(url):
        return False
    negatives = (
        "evento online",
        "solo online",
        "sorteo",
        "concurso",
        "entrada desde",
        "tickets desde",
    )
    return not any(word in text for word in negatives)


async def tavily_search(
    client: httpx.AsyncClient, query: str, config: Config, semaphore: asyncio.Semaphore
) -> list[dict]:
    async with semaphore:
        response = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": config.tavily_api_key,
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

async def run(config: Config) -> list[str]:
    queries = build_queries(config)
    semaphore = asyncio.Semaphore(SEARCH_CONCURRENCY)
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
