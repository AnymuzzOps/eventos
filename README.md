# Eventos gratis en Santiago

Bot Python que busca eventos gratuitos mediante Tavily, valida candidatos con Groq y envía
los resultados a un chat de Telegram. Se ejecuta dos veces al día mediante GitHub Actions.

## Requisitos

- Python 3.11 (versión usada en CI).
- Credenciales para Tavily, Groq y un bot/chat de Telegram.

No hay base de datos: `procesadas.txt` es un caché JSON Lines local. El workflow actual no
persiste sus cambios entre ejecuciones.

## Configuración local

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Completa `.env` sin confirmarlo en Git y exporta sus variables con la herramienta de tu
preferencia; el script no carga `.env` automáticamente:

```bash
set -a; source .env; set +a
python bot.py
```

Variables obligatorias:

| Variable | Uso |
| --- | --- |
| `TAVILY_API_KEY` | Búsqueda y extracción de fuentes web. |
| `GROQ_API_KEY` | Validación estructurada de candidatos. |
| `TELEGRAM_TOKEN` | Autenticación del bot de Telegram. |
| `TELEGRAM_CHAT_ID` | Destino de los mensajes. |

## Calidad y pruebas

```bash
ruff format --check .
ruff check .
mypy bot.py
pytest
python -m compileall -q bot.py tests
```

Las pruebas no llaman servicios externos. Ejecutar `python bot.py` sí consume cuotas y envía
mensajes reales; usa credenciales y un chat de pruebas durante desarrollo.

## Despliegue

1. Configura las cuatro variables como *Actions secrets* del repositorio.
2. Habilita el workflow `Bot Eventos Gratis Santiago`.
3. Ejecuta inicialmente `workflow_dispatch` con un chat de pruebas.
4. Revisa los logs (nunca deben contener secretos) y después permite la programación cron.

GitHub Actions instala `requirements.txt` y ejecuta `bot.py`. Las horas cron están expresadas
en UTC; no se ajustan automáticamente al horario de verano chileno.

## Arquitectura y límites conocidos

Toda la lógica reside en `bot.py`: configuración, generación de consultas, filtros, caché,
clientes externos, formato y orquestación. Es suficiente para el tamaño actual, pero conviene
separar clientes y dominio antes de agregar más proveedores. Las fechas objetivo del negocio
siguen fijadas a marzo-septiembre de 2026; deben revisarse antes de reutilizar el bot para otro
periodo. Consulta [`AUDIT.md`](AUDIT.md) para la auditoría, decisiones y trabajo pendiente.
