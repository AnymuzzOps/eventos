from datetime import UTC, datetime, timedelta

import pytest

import bot


def test_validar_configuracion_reporta_todas_las_variables(monkeypatch):
    for nombre in ("TAVILY_API_KEY", "GROQ_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.setattr(bot, nombre, "")

    with pytest.raises(RuntimeError) as error:
        bot.validar_configuracion()

    assert "TAVILY_API_KEY" in str(error.value)
    assert "TELEGRAM_CHAT_ID" in str(error.value)


def test_formatear_evento_escapa_html_no_confiable():
    texto = bot.formatear_evento(
        {
            "cat": "arte",
            "nombre": "Evento <script>",
            "lugar": "Sala & patio",
            "fecha": "26 < 27",
            "desc": "Una <b>descripción</b>",
            "link": "https://example.com/?a=1&b=2",
        }
    )

    assert "<script>" not in texto
    assert "&lt;script&gt;" in texto
    assert "Sala &amp; patio" in texto
    assert "<b>Evento" in texto


def test_cache_rechazada_expira_despues_de_su_ttl():
    antigua = datetime.now(UTC) - timedelta(days=bot.CACHE_TTL_RECHAZADO_DIAS + 1)
    entry = {"estado": "groq_rechazado", "procesado_en": antigua.isoformat()}

    assert bot.deberia_omitir_por_cache(entry) is False


def test_prefiltro_rechaza_url_de_busqueda():
    resultado = {
        "url": "https://example.com/search?q=evento",
        "title": "Evento gratis Santiago 2026",
        "content": "Inauguración gratis en Santiago en julio 2026",
    }

    pasa, motivo, _, _ = bot.prefiltro(resultado)

    assert pasa is False
    assert "búsqueda" in motivo
