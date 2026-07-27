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


class ExtractResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.request = httpx.Request("POST", "https://api.tavily.com/extract")
        self.response = httpx.Response(status_code, request=self.request)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "extract failed", request=self.request, response=self.response
            )

    def json(self):
        return self.payload


class ExtractClient:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    async def post(self, url, json, timeout):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self.responses:
            return self.responses.pop(0)
        return ExtractResponse(
            {"results": [{"url": item, "raw_content": f"content:{item}"} for item in json["urls"]]}
        )


def test_tavily_extract_divide_45_urls_en_tres_lotes():
    urls = [f"https://example.com/{index}" for index in range(45)]
    client = ExtractClient()

    extracted = asyncio.run(bot.tavily_extract(client, urls, config()))

    batches = [call["json"]["urls"] for call in client.calls]
    assert [len(batch) for batch in batches] == [20, 20, 5]
    assert all(len(batch) <= bot.TAVILY_EXTRACT_BATCH_SIZE for batch in batches)
    assert list(extracted) == urls


def test_tavily_extract_elimina_duplicados_y_conserva_orden():
    urls = ["https://example.com/1", "https://example.com/2", "https://example.com/1", ""]
    client = ExtractClient()

    asyncio.run(bot.tavily_extract(client, urls, config()))

    assert client.calls[0]["json"]["urls"] == urls[:2]


def test_tavily_extract_lista_vacia_no_hace_solicitudes():
    client = ExtractClient()

    assert asyncio.run(bot.tavily_extract(client, [], config())) == {}
    assert client.calls == []


def test_tavily_extract_conserva_resultados_si_un_lote_falla():
    urls = [f"https://example.com/{index}" for index in range(45)]

    def successful(batch):
        return ExtractResponse({"results": [{"url": url, "content": url} for url in batch]})

    client = ExtractClient(
        [
            successful(urls[:20]),
            ExtractResponse({}, status_code=400),
            successful(urls[40:]),
        ]
    )

    extracted = asyncio.run(bot.tavily_extract(client, urls, config()))

    assert list(extracted) == urls[:20] + urls[40:]
    assert len(client.calls) == 3


def test_tavily_extract_tolera_failed_results():
    client = ExtractClient(
        [
            ExtractResponse(
                {
                    "results": [{"url": "https://example.com/ok", "content": "contenido"}],
                    "failed_results": [{"url": "https://example.com/fail", "error": "failed"}],
                }
            )
        ]
    )

    extracted = asyncio.run(
        bot.tavily_extract(client, ["https://example.com/ok", "https://example.com/fail"], config())
    )

    assert extracted == {"https://example.com/ok": "contenido"}
