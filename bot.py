import asyncio
import hashlib
import html
import json
import os
import unicodedata
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
from groq import Groq

CHILE_TZ = ZoneInfo("America/Santiago")
PROCESADAS_PATH = Path("procesadas.txt")
TELEGRAM_MAX_CHARS = 3900
TELEGRAM_MAX_ATTEMPTS = 4
TELEGRAM_RETRY_BASE_SECONDS = 2
TELEGRAM_MAX_RETRY_AFTER_SECONDS = 60
CACHE_TTL_APROBADO_DIAS = 1
CACHE_TTL_RECHAZADO_DIAS = 7
MIN_CONFIDENCE = 70
MAX_CANDIDATES = 20
SEARCH_CONCURRENCY = 5
EXTRACT_CHARS = 5000

COMUNAS_RM = {
    "alhue",
    "buin",
    "calera de tango",
    "cerrillos",
    "cerro navia",
    "colina",
    "conchali",
    "curacavi",
    "el bosque",
    "el monte",
    "estacion central",
    "huechuraba",
    "independencia",
    "isla de maipo",
    "la cisterna",
    "la florida",
    "la granja",
    "la pintana",
    "la reina",
    "lampa",
    "las condes",
    "lo barnechea",
    "lo espejo",
    "lo prado",
    "macul",
    "maipu",
    "maria pinto",
    "melipilla",
    "nunoa",
    "padre hurtado",
    "paine",
    "pedro aguirre cerda",
    "penaflor",
    "penalolen",
    "pirque",
    "providencia",
    "pudahuel",
    "puente alto",
    "quilicura",
    "quinta normal",
    "recoleta",
    "renca",
    "san bernardo",
    "san joaquin",
    "san jose de maipo",
    "san miguel",
    "san pedro",
    "san ramon",
    "santiago",
    "talagante",
    "tiltil",
    "vitacura",
}
COMUNAS_BUSQUEDA = [
    "Santiago",
    "Providencia",
    "Ñuñoa",
    "Las Condes",
    "Vitacura",
    "La Reina",
    "Peñalolén",
    "San Miguel",
    "Estación Central",
    "Quinta Normal",
    "Maipú",
    "Puente Alto",
    "La Florida",
    "Recoleta",
    "Independencia",
    "San José de Maipo",
    "Buin",
    "Melipilla",
]
OFFICIAL_DOMAINS = (
    ".gob.cl",
    ".muni.cl",
    ".cl/cultura",
    "municipalidad",
    "museo",
    "biblioteca",
    "cultur",
    "uchile.cl",
    "uc.cl",
    "usach.cl",
    "parquemet.cl",
)
BLOCKED_PRICE_WORDS = ("desde $", "entrada desde", "ticket desde", "valor desde")
CATEGORIES = {
    "parque": "🌿 Parques y jardines",
    "jardin": "🌿 Parques y jardines",
    "naturaleza": "🌿 Parques y jardines",
    "museo": "🏛️ Museos y cultura",
    "cultura": "🏛️ Museos y cultura",
    "musica": "🎶 Música y espectáculos",
    "espectaculo": "🎶 Música y espectáculos",
    "feria": "🎨 Ferias, talleres y exposiciones",
    "taller": "🎨 Ferias, talleres y exposiciones",
    "exposicion": "🎨 Ferias, talleres y exposiciones",
    "recorrido": "🚶 Recorridos y panoramas urbanos",
    "urbano": "🚶 Recorridos y panoramas urbanos",
    "interior": "🌧️ Panoramas bajo techo",
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
    exa_api_key: str
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
            exa_api_key=os.getenv("EXA_API_KEY", ""),
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
                "EXA_API_KEY": config.exa_api_key,
                "GROQ_API_KEY": config.groq_api_key,
                "TELEGRAM_TOKEN": config.telegram_token,
                "TELEGRAM_CHAT_ID": config.telegram_chat_id,
            }
            missing = [key for key, value in required.items() if not value]
            if missing:
                raise RuntimeError("Faltan variables de entorno requeridas: " + ", ".join(missing))
        return config


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).lower().split())


def domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def is_search_url(url: str) -> bool:
    parsed = urlparse(url)
    return any(part in parsed.path.lower() for part in ("/search", "/discover", "/explore")) or any(
        key in parse_qs(parsed.query) for key in ("q", "query", "search", "keyword")
    )


def is_official_source(url: str, source: str = "") -> bool:
    value = f"{domain(url)} {normalize(source)}"
    return any(marker in value for marker in OFFICIAL_DOMAINS)


def is_rm_comuna(comuna: object) -> bool:
    return normalize(comuna) in COMUNAS_RM


def parse_event_date(value: object) -> date | None:
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
    month_name = SPANISH_MONTHS[config.start_date.month - 1]
    month_year = f"{month_name} {config.start_date.year}"
    period = f"{config.start_date.isoformat()} {config.end_date.isoformat()}"
    return [
        f"eventos gratis {config.city} {month_year}",
        f"panoramas gratuitos {config.region} {period}",
        f"conciertos gratuitos {config.city} {month_year}",
        f"exposiciones gratuitas {config.city} {month_year}",
        f"museos y centros culturales gratuitos {config.region} {period}",
        f"actividades municipales gratuitas {config.city} {month_year}",
        f"cine al aire libre gratis {config.region} {period}",
        f"ferias y talleres gratuitos {config.city} {month_year}",
        f"recorridos patrimoniales gratuitos {config.region} {period}",
        f"parques jardines y naturaleza gratuitos {config.region} {month_year}",
    ]


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


async def exa_search(
    client: httpx.AsyncClient,
    query: str,
    config: Config,
    semaphore: asyncio.Semaphore,
    include_domains: list[str] | None = None,
) -> list[dict]:
    async with semaphore:
        payload: dict[str, object] = {
            "query": query,
            "type": "auto",
            "numResults": 8,
            "contents": {"highlights": True},
        }
        if include_domains:
            payload["includeDomains"] = include_domains
        response = await client.post(
            "https://api.exa.ai/search",
            headers={
                "Authorization": f"Bearer {config.exa_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=45,
        )
        response.raise_for_status()
        mapped_results: list[dict] = []
        for item in response.json().get("results", []):
            highlights = item.get("highlights") or []
            if not isinstance(highlights, list):
                highlights = []
            content = "\n".join(str(highlight) for highlight in highlights if highlight)
            url = str(item.get("url") or "")
            if not url:
                continue
            mapped_results.append(
                {
                    "title": str(item.get("title") or ""),
                    "url": url,
                    "content": content,
                    "raw_content": content,
                    "published_date": item.get("publishedDate"),
                    "source": domain(url),
                }
            )
        return mapped_results


def groq_evaluate(result: dict, config: Config) -> dict | None:
    content = "\n".join(
        f"{key}: {result.get(key, '')}"
        for key in ("title", "content", "published_date", "url", "raw_content")
    )[: EXTRACT_CHARS + 2500]
    response = Groq(api_key=config.groq_api_key).chat.completions.create(
        model=config.groq_model,
        messages=[
            {"role": "system", "content": system_prompt(config)},
            {"role": "user", "content": content},
        ],
        temperature=0.0,
        max_tokens=700,
        response_format={"type": "json_object"},
    )
    response_content = response.choices[0].message.content
    if response_content is None:
        return None
    item = json.loads(response_content)
    item["url_fuente"] = result.get("url", "")
    return item


def category_label(item: dict) -> str:
    category = normalize(item.get("categoria"))
    interior = normalize(item.get("interior_exterior"))
    if interior == "interior":
        return CATEGORIES["interior"]
    return next(
        (label for key, label in CATEGORIES.items() if key in category),
        "🚶 Recorridos y panoramas urbanos",
    )


def safe(value: object, fallback: str = "No informado") -> str:
    return html.escape(str(value or fallback), quote=False)


def format_panorama(item: dict, verified_at: date) -> str:
    reservation = "Requiere reserva gratuita" if item.get("requiere_reserva") else "Sin reserva"
    reservation_url = (
        f"\n🔗 <b>Inscripción:</b> {safe(item.get('url_reserva'))}"
        if item.get("url_reserva")
        else ""
    )
    rain = (
        f"\n☔ <b>Si llueve:</b> {safe(item.get('recomendacion_lluvia'))}"
        if item.get("recomendacion_lluvia")
        else ""
    )
    return (
        f"<b>{safe(item.get('titulo'))}</b>\n"
        f"📅 <b>Fecha:</b> {safe(item.get('fecha_inicio'))} a {safe(item.get('fecha_fin') or item.get('fecha_inicio'))}\n"
        f"🕒 <b>Horario:</b> {safe(item.get('horario'))}\n"
        f"📍 <b>{safe(item.get('comuna'))}</b> — {safe(item.get('direccion'))}\n"
        f"💰 <b>Gratis</b>\n🎟️ {safe(reservation)}{reservation_url}\n"
        f"🌦️ <b>Tipo:</b> {safe(item.get('interior_exterior'))}{rain}\n\n"
        f"{safe(item.get('descripcion'))}\n\n💑 {safe(item.get('razon_para_pareja'))}\n"
        f"🔗 <b>Fuente:</b> {safe(item.get('fuente'))} — {safe(item.get('url_fuente'))}\n"
        f"✅ <b>Verificado:</b> {verified_at.isoformat()}"
    )


def split_message(text: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(block) > limit:
            cut = block.rfind("\n", 0, limit)
            cut = cut if cut > 0 else limit
            chunks.append(block[:cut])
            block = block[cut:].lstrip("\n")
        current = block
    if current:
        chunks.append(current)
    return chunks


def grouped_messages(items: list[dict], verified_at: date) -> list[str]:
    groups: dict[str, list[str]] = {}
    for item in items:
        groups.setdefault(category_label(item), []).append(format_panorama(item, verified_at))
    messages = []
    for label, entries in groups.items():
        messages.extend(split_message(f"<b>{label}</b>\n\n" + "\n\n".join(entries)))
    return messages


def load_history(path: Path = PROCESADAS_PATH) -> dict[str, dict]:
    history: dict[str, dict] = {}
    if not path.exists():
        return history
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
            history[entry.get("key") or entry.get("url", "")] = entry
        except (json.JSONDecodeError, AttributeError):
            continue
    return history


def history_is_fresh(entry: dict, ttl_days: int) -> bool:
    try:
        processed = datetime.fromisoformat(str(entry["processed_at"]).replace("Z", "+00:00"))
        if processed.tzinfo is None:
            processed = processed.replace(tzinfo=UTC)
        return datetime.now(UTC) - processed <= timedelta(days=ttl_days)
    except (KeyError, TypeError, ValueError):
        return False


def save_history(item: dict, path: Path = PROCESADAS_PATH) -> None:
    entry = {
        "key": dedupe_key(item),
        "url": item.get("url_fuente"),
        "fingerprint": content_fingerprint(item),
        "processed_at": datetime.now(UTC).isoformat(),
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def select_updates(items: list[dict], history: dict[str, dict], force: bool = False) -> list[dict]:
    updates = []
    for item in prefer_official(items):
        old = history.get(dedupe_key(item))
        if force or not old or old.get("fingerprint") != content_fingerprint(item):
            updates.append(item)
    return updates


def create_telegram_client() -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=1)
    timeout = httpx.Timeout(
        timeout=30.0,
        connect=10.0,
        read=30.0,
        write=20.0,
        pool=10.0,
    )
    return httpx.AsyncClient(
        transport=transport,
        timeout=timeout,
        trust_env=False,
        follow_redirects=True,
    )


async def send_message(client: httpx.AsyncClient, text: str, config: Config) -> None:
    if config.dry_run:
        print(text)
        return

    retryable_statuses = {429, 500, 502, 503, 504}
    for attempt in range(1, TELEGRAM_MAX_ATTEMPTS + 1):
        try:
            response = await client.post(
                f"https://api.telegram.org/bot{config.telegram_token}/sendMessage",
                json={
                    "chat_id": config.telegram_chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=30,
            )
            response.raise_for_status()
            print(f"[Telegram] intento {attempt}/{TELEGRAM_MAX_ATTEMPTS} enviado correctamente")
            return
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status not in retryable_statuses or attempt == TELEGRAM_MAX_ATTEMPTS:
                raise

            delay = TELEGRAM_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            if status == 429:
                retry_after: object = None
                with suppress(ValueError, AttributeError):
                    retry_after = error.response.json().get("parameters", {}).get("retry_after")
                retry_after = retry_after or error.response.headers.get("Retry-After")
                with suppress(TypeError, ValueError):
                    assert isinstance(retry_after, (str, int, float))
                    delay = min(float(retry_after), TELEGRAM_MAX_RETRY_AFTER_SECONDS)

            print(
                f"[Telegram] intento {attempt}/{TELEGRAM_MAX_ATTEMPTS} falló: HTTP {status}; "
                f"reintento en {delay:g}s"
            )
        except (httpx.TimeoutException, httpx.RequestError) as error:
            if attempt == TELEGRAM_MAX_ATTEMPTS:
                raise
            delay = TELEGRAM_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            print(
                f"[Telegram] intento {attempt}/{TELEGRAM_MAX_ATTEMPTS} falló: "
                f"{type(error).__name__}; reintento en {delay}s"
            )

        await asyncio.sleep(delay)


async def telegram_preflight(client: httpx.AsyncClient, config: Config) -> bool:
    if config.dry_run:
        return True
    for attempt in range(1, 3):
        try:
            response = await client.get(
                f"https://api.telegram.org/bot{config.telegram_token}/getMe", timeout=20
            )
            response.raise_for_status()
            return True
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status in {401, 403, 404}:
                label = "token inválido" if status == 401 else "error permanente"
                print(f"[Telegram preflight] {label}: HTTP {status}")
                return False
            print(f"[Telegram preflight] intento {attempt}/2 falló: HTTP {status}")
        except (httpx.TimeoutException, httpx.RequestError) as error:
            print(f"[Telegram preflight] intento {attempt}/2 falló: {type(error).__name__}")
        if attempt < 2:
            await asyncio.sleep(TELEGRAM_RETRY_BASE_SECONDS)
    return False


async def run(config: Config) -> list[str]:
    queries = build_queries(config)
    semaphore = asyncio.Semaphore(SEARCH_CONCURRENCY)
    async with (
        httpx.AsyncClient() as search_client,
        create_telegram_client() as telegram_client,
    ):
        if not await telegram_preflight(telegram_client, config):
            print("[Telegram preflight] ejecución detenida antes de consultar Exa")
            raise RuntimeError("Telegram no está disponible desde el runner")
        batches = await asyncio.gather(
            *(exa_search(search_client, query, config, semaphore) for query in queries),
            return_exceptions=True,
        )
        by_url: dict[str, dict] = {}
        successful_queries = 0
        for batch in batches:
            if isinstance(batch, BaseException):
                if isinstance(batch, httpx.HTTPStatusError):
                    print(f"[Exa] consulta omitida: HTTP {batch.response.status_code}")
                else:
                    print(f"[Exa] consulta omitida: {type(batch).__name__}")
                continue
            successful_queries += 1
            for result in batch:
                if prefilter(result):
                    by_url.setdefault(str(result.get("url")), result)
        if successful_queries == 0:
            print("[Exa] no fue posible consultar las fuentes de panoramas")
            if config.manual_run:
                await send_message(
                    telegram_client,
                    "No fue posible consultar las fuentes de panoramas en esta ejecución.",
                    config,
                )
            return []
        candidates = sorted(
            by_url.values(), key=lambda r: is_official_source(str(r.get("url", ""))), reverse=True
        )[:MAX_CANDIDATES]
        accepted = []
        for result in candidates:
            try:
                item = groq_evaluate(result, config)
                valid, reason = validate_panorama(item or {}, config)
                if valid and item is not None:
                    accepted.append(item)
                else:
                    print(f"[Groq] descartado: {reason}")
            except (json.JSONDecodeError, ValueError, TypeError, KeyError) as error:
                print(f"[Groq] respuesta inválida: {type(error).__name__}")
            except Exception as error:
                # El SDK puede exponer distintas clases según la versión; solo registramos
                # el tipo para no filtrar respuestas, URLs con parámetros ni credenciales.
                print(f"[Groq] solicitud omitida: {type(error).__name__}")
        updates = select_updates(accepted, load_history(), config.force_resend)[: config.max_events]
        messages = grouped_messages(updates, datetime.now(CHILE_TZ).date())
        if updates:
            summary = f"💑 <b>Panoramas gratuitos</b>\n\nEncontré <b>{len(updates)}</b> nuevos panoramas gratuitos en {safe(config.city)} y {safe(config.region)}."
            await send_message(telegram_client, summary, config)
            for message in messages:
                await send_message(telegram_client, message, config)
            if not config.dry_run:
                for item in updates:
                    save_history(item)
        elif config.manual_run:
            await send_message(
                telegram_client,
                "💑 <b>Panoramas gratuitos</b>\n\nNo encontré novedades verificadas.",
                config,
            )
        return messages


async def main() -> None:
    await run(Config.from_env())


if __name__ == "__main__":
    asyncio.run(main())
