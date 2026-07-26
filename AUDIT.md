# Auditoría técnica y plan de actualización

Fecha: 2026-07-26. Alcance: todos los archivos versionados del repositorio en el commit base
`2674a0a`. La revisión fue estática; no se invocaron Tavily, Groq ni Telegram.

## Resumen ejecutivo

El proyecto es un bot pequeño en Python 3.11, sin servidor web, base de datos, Docker ni
migraciones. Integra Tavily por HTTP, Groq mediante su SDK y Telegram por HTTP, y se programa
con GitHub Actions. Antes de esta actualización carecía de manifiestos reproducibles, pruebas,
documentación operativa, archivo de ejemplo de entorno y controles de calidad. La lógica era
comprensible, pero estaba concentrada en un único módulo de más de 800 líneas.

El riesgo técnico inicial era **alto** y la mantenibilidad **media-baja**. Las prioridades son
evitar inyección en mensajes HTML, detectar fallos de entrega, reproducir el entorno, probar la
lógica crítica y reducir permisos/superposición en CI. No se hallaron secretos materializados:
solo nombres de variables y referencias a GitHub Secrets. `procesadas.txt` estaba vacío.

## Inventario

- **Lenguaje/runtime:** Python 3.11 en CI; sintaxis mínima efectiva 3.10.
- **Dependencias directas:** `httpx` y `groq`; sus transitivas se resuelven al instalar. No
  existía lockfile y el entorno sin acceso a PyPI impidió generar uno de forma verificable.
- **Persistencia:** archivo JSONL `procesadas.txt`, relativo al directorio de ejecución.
- **Servicios:** Tavily Search/Extract, Groq Chat Completions y Telegram Bot API.
- **CI/CD:** cron y despacho manual en GitHub Actions; no hay Docker, infraestructura,
  despliegue adicional, cobertura ni workflow de PR.
- **Variables:** `TAVILY_API_KEY`, `GROQ_API_KEY`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`.

## Hallazgos

| Severidad | Componente | Problema y riesgo | Recomendación / impacto |
| --- | --- | --- | --- |
| Alto | `formatear_evento` | Datos web/LLM se interpolaban en HTML de Telegram, permitiendo etiquetas no deseadas o mensajes rotos. | Escapar cada valor externo conservando solo las etiquetas propias; cambio visual únicamente ante caracteres especiales. Implementado. |
| Alto | Dependencias/CI | Instalación flotante sin manifiesto hacía cada ejecución no reproducible y permitía cambios transitivos inesperados. | Declarar rangos compatibles y generar lock con hashes cuando PyPI esté disponible. Parcialmente implementado. |
| Alto | Configuración | Leer variables con `os.environ` durante la importación impedía pruebas y producía errores poco orientativos. | Validar juntas al iniciar, sin mostrar valores. Implementado. |
| Medio | Telegram | No se comprobaba el estado HTTP; el bot podía informar éxito tras un rechazo remoto. | Aplicar `raise_for_status`; la ejecución ahora falla de forma visible. Implementado. |
| Medio | Zona horaria | UTC-3 fijo era incorrecto durante parte del año chileno. | Usar `America/Santiago` de `zoneinfo`. Implementado. |
| Medio | Tests/calidad | No había tests, lint, formato ni tipado automatizado. | Añadir suite inicial y configuración; agregar workflow de PR como siguiente paso. Implementado localmente. |
| Resuelto | Fechas | La ventana estaba acoplada a 2026. | Configuración y validación por entorno permiten cambiar el periodo sin editar código. |
| Mitigado | Disponibilidad/coste | Las búsquedas se lanzaban sin límite de concurrencia. | Semáforo de cinco solicitudes y máximo de 40 candidatos; quedan pendientes reintentos acordes a las cuotas del proveedor. |
| Mitigado | Caché | GitHub Actions no persistía `procesadas.txt`. | Cache con clave nueva, `restore-keys` y concurrencia única; almacenamiento externo sigue recomendado si se requiere garantía transaccional. |
| Bajo | Arquitectura | Un único módulo mezcla dominio, I/O y configuración. | Extraer módulos gradualmente cuando se amplíe; hacerlo ahora produciría un diff de riesgo innecesario. |
| Bajo | Tipos/errores | Predominan `dict` sin esquema y capturas amplias que agrupan fallos de red, parseo y contrato LLM. | Introducir `TypedDict`/modelos y excepciones específicas incrementalmente. Pendiente. |
| Bajo | Documentación | README solo contenía el nombre del proyecto. | Documentar instalación, operación, pruebas y despliegue. Implementado. |
| Mejora opcional | Observabilidad | Logs con `print`, sin niveles ni métricas; imprimen títulos/URLs públicos, no secretos. | Logging estructurado con redacción y correlación de ejecución. |

No aplican CORS, cookies, sesiones, autenticación de usuarios, SEO, accesibilidad web,
compatibilidad móvil, consultas SQL, migraciones ni carga de archivos porque no existe una
aplicación web, servidor o base de datos en este repositorio.

## Plan y compatibilidad

### Automático y seguro

1. Proteger HTML y propagación de errores HTTP.
2. Validar configuración sin importar secretos y corregir zona horaria.
3. Declarar dependencias/runtimes, ignorados, entorno de ejemplo y herramientas de calidad.
4. Añadir tests unitarios sin tráfico externo y documentación completa.
5. Endurecer el workflow con permisos mínimos, timeout y control de concurrencia.

### Requiere revisión

- Persistencia transaccional del historial y límites/reintentos específicos según cuotas de APIs.
- Contrato tipado estricto para la salida del modelo y una estrategia explícita de logs.

### Potencialmente incompatible / versiones mayores

- No se migra de proveedor/modelo, no se cambian filtros ni formato funcional y no se
  reestructura como paquete.
- Los rangos bloquean versiones mayores de dependencias. Antes de elevarlos deben revisarse
  notas de migración y ejecutarse pruebas con APIs de sandbox. No se identificó ni aplicó una
  actualización mayor automática.

## Riesgos residuales

La suite cubre helpers críticos, pero no respuestas reales ni fallos de los tres proveedores.
No hay lock transitivo con hashes, análisis SAST/secret scanning en CI, cobertura medida ni
persistencia transaccional garantizada. El modelo Groq y los endpoints pueden cambiar externamente; deben
verificarse en un chat de prueba antes del despliegue.
