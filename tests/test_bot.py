import asyncio
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

import bot


def config() -> bot.Config:
    return bot.Config(
        "",
        "",
        "",
        "",
        date(2026, 8, 1),
        date(2026, 8, 31),
        "Santiago",
        "Región Metropolitana",
        True,
        True,
        20,
        True,
        False,
        False,
    )


def panorama(**changes):
    item = {
        "titulo": "Museo vespertino",
        "categoria": "museo",
        "descripcion": "Exposición",
        "fecha_inicio": "2026-08-01",
        "fecha_fin": "2026-08-31",
        "horario": "10:00 a 18:00",
        "comuna": "Santiago",
        "direccion": "Plaza 1",
        "es_gratis": True,
        "precio_texto": "Entrada liberada",
        "requiere_reserva": False,
        "reserva_gratuita": False,
        "url_reserva": "",
        "fuente": "Museo oficial",
        "url_fuente": "https://museo.gob.cl/agenda",
        "interior_exterior": "interior",
        "apto_para_pareja": True,
        "razon_para_pareja": "Permite conversar",
        "confianza": 90,
    }
    item.update(changes)
    return item


def valid(item):
    return bot.validate_panorama(item, config(), today=date(2026, 7, 27))[0]


def test_incluye_inicio_y_fin_de_agosto():
    assert valid(panorama(fecha_inicio="2026-08-01", fecha_fin="2026-08-01"))
    assert valid(panorama(fecha_inicio="2026-08-31", fecha_fin="2026-08-31"))


def test_excluye_fechas_fuera_del_periodo_y_otro_ano():
    assert not valid(panorama(fecha_inicio="2026-09-01", fecha_fin="2026-09-01"))
    ok, reason = bot.validate_panorama(
        panorama(fecha_inicio="2025-08-10", fecha_fin="2025-08-10"), config(), date(2025, 7, 1)
    )
    assert not ok
    assert "periodo" in reason or "año" in reason


def test_excluye_pagadas_y_precio_ambiguo():
    assert not valid(panorama(es_gratis=False, precio_texto="$5.000"))
    assert not valid(panorama(precio_texto="Precio no informado"))
    assert not valid(panorama(precio_texto="Entrada desde $2.000"))


def test_incluye_reserva_gratuita_y_rechaza_reserva_ambigua():
    assert valid(
        panorama(
            requiere_reserva=True, reserva_gratuita=True, url_reserva="https://museo.gob.cl/reserva"
        )
    )
    assert not valid(panorama(requiere_reserva=True, reserva_gratuita=False))


def test_valida_comunas_de_la_region_metropolitana():
    assert bot.is_rm_comuna("Ñuñoa")
    assert bot.is_rm_comuna("Puente Alto")
    assert not bot.is_rm_comuna("Valparaíso")


def test_deduplica_y_prioriza_fuente_oficial():
    secondary = panorama(url_fuente="https://medio.example/evento", fuente="Diario")
    official = panorama(url_fuente="https://museo.gob.cl/evento", fuente="Museo oficial")
    chosen = bot.prefer_official([secondary, official])
    assert len(chosen) == 1
    assert chosen[0]["url_fuente"] == official["url_fuente"]


def test_escapa_html_externo():
    text = bot.format_panorama(
        panorama(titulo="Cita <script>", descripcion="Arte & vino"), date(2026, 7, 27)
    )
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "Arte &amp; vino" in text


def test_divide_mensajes_largos_sin_perder_contenido():
    text = "\n\n".join(["bloque " + "x" * 80] * 10)
    chunks = bot.split_message(text, limit=200)
    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert sum(chunk.count("bloque") for chunk in chunks) == 10


def test_historial_persiste_expira_y_detecta_cambios(tmp_path):
    path = tmp_path / "history.jsonl"
    item = panorama()
    bot.save_history(item, path)
    history = bot.load_history(path)
    assert not bot.select_updates([item], history)
    assert bot.select_updates([panorama(horario="11:00")], history)
    assert bot.history_is_fresh(next(iter(history.values())), 1)
    stale = {"processed_at": (datetime.now(UTC) - timedelta(days=2)).isoformat()}
    assert not bot.history_is_fresh(stale, 1)


def test_carga_historial_tolera_lineas_invalidas(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text("inválido\n" + json.dumps({"key": "ok", "processed_at": "x"}) + "\n")
    assert "ok" in bot.load_history(path)


def test_dry_run_imprime_y_no_usa_cliente(capsys):
    class Client:
        async def post(self, *args, **kwargs):
            raise AssertionError("DRY_RUN no debe llamar Telegram")

    asyncio.run(bot.send_message(Client(), "mensaje simulado", config()))
    assert "mensaje simulado" in capsys.readouterr().out


def test_configuracion_es_dinamica(monkeypatch):
    monkeypatch.setenv("EVENT_START_DATE", "2027-01-01")
    monkeypatch.setenv("EVENT_END_DATE", "2027-01-15")
    monkeypatch.setenv("DRY_RUN", "true")
    parsed = bot.Config.from_env()
    assert parsed.start_date == date(2027, 1, 1)
    assert parsed.end_date == date(2027, 1, 15)
    queries = bot.build_queries(parsed)
    assert any("enero 2027" in query for query in queries)
    assert not any("agosto 2026" in query for query in queries)


class TelegramResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        request = httpx.Request("POST", "https://api.telegram.org/bot-redacted/sendMessage")
        self.response = httpx.Response(
            status_code,
            request=request,
            json=payload or {},
            headers=headers,
        )

    def raise_for_status(self):
        self.response.raise_for_status()


class TelegramClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def post(self, url, json, timeout):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def telegram_config(dry_run=False):
    return replace(
        config(),
        telegram_token="secret-token",
        telegram_chat_id="secret-chat-id",
        dry_run=dry_run,
    )


def no_wait(monkeypatch):
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return delays


def test_telegram_exito_inmediato(monkeypatch):
    delays = no_wait(monkeypatch)
    client = TelegramClient([TelegramResponse()])

    asyncio.run(bot.send_message(client, "mensaje", telegram_config()))

    assert len(client.calls) == 1
    assert delays == []


def test_telegram_502_seguido_de_exito(monkeypatch):
    delays = no_wait(monkeypatch)
    client = TelegramClient([TelegramResponse(502), TelegramResponse()])

    asyncio.run(bot.send_message(client, "mensaje", telegram_config()))

    assert len(client.calls) == 2
    assert delays == [2]


def test_telegram_tres_502_y_exito_en_cuarto_intento(monkeypatch):
    delays = no_wait(monkeypatch)
    client = TelegramClient([TelegramResponse(502)] * 3 + [TelegramResponse()])

    asyncio.run(bot.send_message(client, "mensaje", telegram_config()))

    assert len(client.calls) == 4
    assert delays == [2, 4, 8]


def test_telegram_cuatro_503_relanza(monkeypatch):
    delays = no_wait(monkeypatch)
    client = TelegramClient([TelegramResponse(503)] * 4)

    with pytest.raises(httpx.HTTPStatusError) as error:
        asyncio.run(bot.send_message(client, "mensaje", telegram_config()))

    assert error.value.response.status_code == 503
    assert len(client.calls) == 4
    assert delays == [2, 4, 8]


def test_telegram_401_no_reintenta(monkeypatch):
    delays = no_wait(monkeypatch)
    client = TelegramClient([TelegramResponse(401)])

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(bot.send_message(client, "mensaje", telegram_config()))

    assert len(client.calls) == 1
    assert delays == []


def test_telegram_429_usa_parameters_retry_after(monkeypatch):
    delays = no_wait(monkeypatch)
    client = TelegramClient(
        [TelegramResponse(429, {"parameters": {"retry_after": 12}}), TelegramResponse()]
    )

    asyncio.run(bot.send_message(client, "mensaje", telegram_config()))

    assert delays == [12]


def test_telegram_429_usa_header_retry_after_y_limita_espera(monkeypatch):
    delays = no_wait(monkeypatch)
    client = TelegramClient(
        [TelegramResponse(429, headers={"Retry-After": "90"}), TelegramResponse()]
    )

    asyncio.run(bot.send_message(client, "mensaje", telegram_config()))

    assert delays == [bot.TELEGRAM_MAX_RETRY_AFTER_SECONDS]


def test_telegram_timeout_seguido_de_exito(monkeypatch):
    delays = no_wait(monkeypatch)
    request = httpx.Request("POST", "https://api.telegram.org/bot-redacted/sendMessage")
    client = TelegramClient([httpx.ReadTimeout("timeout", request=request), TelegramResponse()])

    asyncio.run(bot.send_message(client, "mensaje", telegram_config()))

    assert len(client.calls) == 2
    assert delays == [2]


def test_telegram_dry_run_no_hace_llamadas_ni_espera(monkeypatch):
    delays = no_wait(monkeypatch)
    client = TelegramClient([])

    asyncio.run(bot.send_message(client, "mensaje", telegram_config(dry_run=True)))

    assert client.calls == []
    assert delays == []


def test_telegram_logs_no_exponen_token_ni_chat_id(monkeypatch, capsys):
    no_wait(monkeypatch)
    client = TelegramClient([TelegramResponse(502), TelegramResponse()])

    asyncio.run(bot.send_message(client, "mensaje", telegram_config()))

    logs = capsys.readouterr().out
    assert "secret-token" not in logs
    assert "secret-chat-id" not in logs
    assert "api.telegram.org" not in logs


class ExaResponse:
    def __init__(self, payload=None, status_code=200):
        request = httpx.Request("POST", "https://api.exa.ai/search")
        self.response = httpx.Response(status_code, request=request, json=payload or {})

    def raise_for_status(self):
        self.response.raise_for_status()

    def json(self):
        return self.response.json()


class ExaClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def post(self, url, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def test_config_lee_exa_sin_exigir_proveedor_anterior(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "exa-secret")
    monkeypatch.delenv("PREVIOUS_PROVIDER_API_KEY", raising=False)
    parsed = bot.Config.from_env(require_secrets=False)
    assert parsed.exa_api_key == "exa-secret"
    assert set(parsed.__dataclass_fields__).isdisjoint({"previous_provider_api_key"})


def test_exa_search_payload_autenticacion_y_mapeo(capsys):
    config_value = replace(config(), exa_api_key="exa-secret")
    client = ExaClient(
        ExaResponse(
            {
                "results": [
                    {
                        "title": "Evento",
                        "url": "https://example.com/evento",
                        "highlights": ["primero", "segundo"],
                        "publishedDate": "2026-08-01",
                    },
                    {"title": "Sin URL", "highlights": ["omitido"]},
                ]
            }
        )
    )
    results = asyncio.run(bot.exa_search(client, "consulta", config_value, asyncio.Semaphore(1)))
    call = client.calls[0]
    assert call["url"] == "https://api.exa.ai/search"
    assert call["headers"]["Authorization"] == "Bearer exa-secret"
    assert call["json"] == {
        "query": "consulta",
        "type": "auto",
        "numResults": 8,
        "contents": {"highlights": True},
    }
    assert results == [
        {
            "title": "Evento",
            "url": "https://example.com/evento",
            "content": "primero\nsegundo",
            "raw_content": "primero\nsegundo",
            "published_date": "2026-08-01",
            "source": "example.com",
        }
    ]
    assert "exa-secret" not in capsys.readouterr().out


def test_build_queries_tiene_exactamente_diez_consultas():
    assert len(bot.build_queries(config())) == 10


class PreflightClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def get(self, url, timeout):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_telegram_preflight_exitoso(monkeypatch):
    no_wait(monkeypatch)
    client = PreflightClient([TelegramResponse()])
    assert asyncio.run(bot.telegram_preflight(client, telegram_config()))
    assert client.calls == 1


def test_telegram_preflight_fallido_detiene_tras_dos_intentos(monkeypatch):
    delays = no_wait(monkeypatch)
    client = PreflightClient([TelegramResponse(503), TelegramResponse(503)])
    assert not asyncio.run(bot.telegram_preflight(client, telegram_config()))
    assert client.calls == 2
    assert delays == [2]


def test_telegram_preflight_dry_run_no_hace_solicitudes(monkeypatch):
    delays = no_wait(monkeypatch)
    client = PreflightClient([])
    assert asyncio.run(bot.telegram_preflight(client, telegram_config(dry_run=True)))
    assert client.calls == 0
    assert delays == []


def test_exa_errores_no_exponen_clave(capsys):
    config_value = replace(config(), exa_api_key="exa-secret")
    request = httpx.Request("POST", "https://api.exa.ai/search")
    response = httpx.Response(429, request=request)
    client = ExaClient(httpx.HTTPStatusError("quota", request=request, response=response))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(bot.exa_search(client, "consulta", config_value, asyncio.Semaphore(1)))
    assert "exa-secret" not in capsys.readouterr().out


class RunClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def test_run_deduplica_y_limita_a_20_sin_extraccion(monkeypatch):
    evaluated = []
    results = [
        {
            "title": f"Evento {index}",
            "url": f"https://example.com/{index}",
            "content": "gratis",
            "raw_content": "gratis",
        }
        for index in range(25)
    ]

    async def fake_exa(*args, **kwargs):
        return results + [results[0]]

    async def fake_preflight(*args, **kwargs):
        return True

    def fake_groq(result, config_value):
        evaluated.append(result["url"])
        return None

    monkeypatch.setattr(bot.httpx, "AsyncClient", RunClient)
    monkeypatch.setattr(bot, "exa_search", fake_exa)
    monkeypatch.setattr(bot, "telegram_preflight", fake_preflight)
    monkeypatch.setattr(bot, "groq_evaluate", fake_groq)

    assert asyncio.run(bot.run(replace(config(), dry_run=True))) == []
    assert len(evaluated) == bot.MAX_CANDIDATES == 20
    assert len(set(evaluated)) == 20
    assert not hasattr(bot, "exa_extract")


def test_run_timeout_de_una_consulta_conserva_otras(monkeypatch):
    calls = 0
    evaluated = []

    async def fake_exa(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("timeout")
        return [
            {
                "title": "Evento",
                "url": "https://example.com/evento",
                "content": "gratis",
                "raw_content": "gratis",
            }
        ]

    async def fake_preflight(*args, **kwargs):
        return True

    def fake_groq(result, config_value):
        evaluated.append(result["url"])
        return None

    monkeypatch.setattr(bot.httpx, "AsyncClient", RunClient)
    monkeypatch.setattr(bot, "exa_search", fake_exa)
    monkeypatch.setattr(bot, "telegram_preflight", fake_preflight)
    monkeypatch.setattr(bot, "groq_evaluate", fake_groq)

    asyncio.run(bot.run(replace(config(), dry_run=True)))
    assert evaluated == ["https://example.com/evento"]
