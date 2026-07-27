# Panoramas gratuitos para parejas

Bot en Python 3.11 que busca con Exa panoramas gratuitos en Santiago y la Región
Metropolitana, extrae y verifica información estructurada con Groq y envía novedades agrupadas
a Telegram. Acepta eventos con fecha y lugares visitables cuando una fuente verificable acredita
su disponibilidad dentro del periodo.

## Qué busca y qué excluye

Busca parques, jardines, museos, exposiciones, centros culturales, ferias, conciertos, cine,
recorridos patrimoniales, miradores, barrios, festivales, talleres para adultos y actividades
municipales. No exige que una publicación diga “romántico”: Groq evalúa si permite caminar,
conversar, fotografiar, hacer picnic o compartir una actividad agradable.

Solo acepta resultados con fecha vigente, comuna de la Región Metropolitana, gratuidad explícita,
fuente directa, confianza mínima y datos suficientes. Rechaza precios ambiguos, actividades
pagadas u online, contenido exclusivamente infantil, sorteos, publicidad, otros años y reservas
que no estén confirmadas como gratuitas.

## Requisitos e instalación

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Se requieren credenciales de Exa, Groq y Telegram. Nunca confirmes `.env` ni uses secretos de
producción durante pruebas.

## Configurar agosto de 2026

```dotenv
EVENT_START_DATE=2026-08-01
EVENT_END_DATE=2026-08-31
EVENT_CITY=Santiago
EVENT_REGION=Región Metropolitana
ONLY_FREE=true
COUPLE_MODE=true
MAX_EVENTS_PER_RUN=20
DRY_RUN=true
FORCE_RESEND=false
```

Para cambiar de mes basta con modificar `EVENT_START_DATE` y `EVENT_END_DATE`. Las fechas usan
`YYYY-MM-DD`, se validan al iniciar y se comparan en `America/Santiago`. `MAX_EVENTS_PER_RUN`
debe estar entre 1 y 100.

## Prueba segura sin enviar a Telegram

Carga las variables locales y conserva `DRY_RUN=true`:

```bash
set -a; source .env; set +a
python bot.py
```

Este modo **sí consulta Exa y Groq** si se ejecuta el flujo completo, pero imprime los mensajes
y nunca contacta Telegram. La suite automatizada no llama ninguna API:

```bash
ruff format --check .
ruff check .
mypy bot.py
pytest
python -m compileall -q bot.py tests
```

Para una prueba totalmente offline usa `pytest`; sus clientes y datos son simulados.

## Ejecución real y manual

Cambia `DRY_RUN=false`, carga las cuatro credenciales y ejecuta `python bot.py`. En GitHub Actions,
configura `EXA_API_KEY`, `GROQ_API_KEY`, `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID` como *Secrets*.
Luego abre **Actions → Panoramas gratuitos para parejas → Run workflow**. El formulario permite
cambiar fechas, ciudad, región, máximo de resultados y reenvío de procesados.

El cron se ejecuta miércoles y domingo a las **13:00 UTC**, aproximadamente **09:00 en Chile
durante el horario de invierno** (la equivalencia cambia con el horario de verano). Esta frecuencia
busca novedades sin gastar cuota dos veces al día.

## Fuentes, verificación y consumo

Las consultas cubren municipios, Chile Cultura, Santiago Cultura, Parquemet, museos, bibliotecas,
universidades, centros culturales, organizadores, prensa confiable y redes oficiales indexables.
Se prioriza una URL oficial al consolidar publicaciones duplicadas.

“Verificado” significa que, en la fecha indicada en el mensaje, el bot encontró una fuente directa
y la información superó las validaciones automáticas. No garantiza que el organizador no cambie o
cancele posteriormente; abre siempre el enlace antes de salir.

Una ejecución genera 10 consultas Exa, con hasta 8 resultados por consulta y como máximo 20
clasificaciones Groq. Los highlights devueltos por Exa se usan directamente, sin una extracción
separada. Errores de búsquedas individuales no cancelan todo el proceso; revisa las cuotas de cada
proveedor antes de ampliar las consultas.

## Deduplicación e historial

`procesadas.txt` almacena JSON Lines con una clave normalizada de título, fecha, lugar, comuna y
dominio, además de una huella de campos relevantes. Un panorama solo reaparece si es nuevo, si
cambia fecha/horario/lugar/reserva/precio/fuente, o si `FORCE_RESEND=true`. Cuando varias fuentes
publican lo mismo se conserva la oficial.

GitHub Actions restaura el historial mediante `actions/cache/restore` usando `restore-keys` y guarda
una clave inmutable nueva por ejecución. La concurrencia única evita dos escritores simultáneos.
Cache no es almacenamiento transaccional: para auditoría estricta o múltiples bots se recomienda
migrar el historial a un almacén externo.

## Formato de Telegram

Primero se envía un resumen y después los panoramas agrupados en parques, cultura, música,
ferias/talleres, recorridos o panoramas bajo techo. Los bloques se dividen automáticamente por
debajo de 3.900 caracteres. Si no hay novedades, una ejecución programada guarda silencio; una
ejecución manual informa que no encontró novedades.
