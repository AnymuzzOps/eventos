import asyncio
import json
from datetime import UTC, date, datetime, timedelta

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
