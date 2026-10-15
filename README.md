# Dromax · Asistente de Compras IA (Odoo 17)

Módulo de Odoo 17 que arma, en un chat, una propuesta de compra semanal por
laboratorio y, con el visto bueno del comprador, la convierte en solicitudes de
presupuesto (`purchase.order`) en borrador.

## Principios

- **Reactivo, no autónomo**: el comprador dispara el análisis con preguntas
  predefinidas; el cron del lunes solo precalcula.
- **Solo lectura** sobre Postgres: SQL validado (una sola sentencia
  `SELECT`/`WITH … SELECT`, lista blanca de tablas y vistas) ejecutado con el
  rol `dromax_ai_ro`.
- **Única escritura**: `purchase.order` en borrador, creada por un botón, nunca
  por el modelo.
- **Acceso restringido** al grupo *Asistente de Compras IA / Usuario*.

## Plan

| Fase | Contenido |
|------|-----------|
| 0 — Preparación | Módulo FTP de droguerías, inventario de farmacias, rol `dromax_ai_ro`, grupo de acceso |
| 1 — Asistente de análisis | Vistas `ai_v_rotacion`, `ai_v_stock`, `ai_v_pendiente`; validador SQL y `run_readonly_sql`; niveles de modelo y `ai.usage.log`; reporte de códigos de barras |
| 2 — Agente de compras | Vistas `ai_v_oferta`, `ai_v_faltantes`; `ai.purchase.proposal` y cron; botones, ajuste en el chat y generación de borrador |
| 3 — Contexto externo | Flujo dinámico con búsqueda web |

## Consultas de solo lectura (`run_readonly_sql`)

El modelo consulta Postgres con la herramienta `run_readonly_sql`
(`services/tools.py`). Cada consulta pasa por dos barreras:

1. **Validador** (`services/sql_validator.py`): una sola sentencia `SELECT` o
   `WITH … SELECT`; sin palabras de escritura, DDL ni bloqueo; sin funciones de
   sistema (`pg_*`, `dblink`, `query_to_xml`…); solo tablas de la lista blanca
   del esquema `public`. Ignora lo que haya en comentarios y literales.
2. **Rol `dromax_ai_ro`**: conexión propia con `psycopg2`, separada del cursor
   de Odoo, en transacción de solo lectura, con `statement_timeout` y SELECT
   únicamente sobre la lista blanca.

El resultado se corta en `sql_max_rows` filas (marca `truncated`) y los textos
largos se recortan.

### Crear el rol

Como superusuario de Postgres, conectado a la base de Odoo:

```bash
psql -d <base> -v ro_password='<clave>' -f scripts/create_readonly_role.sql
```

Se puede volver a ejecutar cuando se añadan tablas o vistas a la lista blanca.

## OpenRouter: niveles de modelo y consumo

Una sola llave y tres niveles, configurables en **Ajustes → Asistente de
Compras IA** (o en el menú *Compras IA → Configuración*):

| Nivel | Modelo inicial | Uso |
|---|---|---|
| Básico | `deepseek/deepseek-v4-flash` | Consultas simples y rankings |
| Medio | `google/gemini-3.8-flash` | Análisis de ventas e inventario |
| Avanzado | `anthropic/claude-opus-5.5` | Propuesta de compra, ajustes y flujo dinámico |

`services/openrouter.py` elige el modelo por nivel; las tareas de compras
(`purchase_proposal`, `proposal_adjustment`, `dynamic_flow`) van siempre al
avanzado, las pida quien las pida. El botón *Probar llave* muestra el consumo y
el tope de crédito de la llave.

Cada llamada, buena o fallida, queda en `ai.usage.log` (*Compras IA →
Configuración → Consumo de IA*): usuario, nivel, modelo, tokens de entrada,
salida, caché y razonamiento, costo que informa OpenRouter y duración. Se
guarda en una transacción aparte, para que el gasto quede registrado aunque la
operación que llamó al modelo falle después. Solo lo ven los administradores.

## Configuración

Las llaves nunca van en el repositorio. Parámetros del sistema (Ajustes →
Técnico → Parámetros del sistema), opción en `odoo.conf` o variable de entorno:

| Parámetro (`dromax_asistente_compras.*`) | `odoo.conf` / entorno | Defecto |
|---|---|---|
| `openrouter_api_key` | `ai_purchase_openrouter_api_key` / `OPENROUTER_API_KEY` | — |
| `model_basic`, `model_medium`, `model_advanced` | `ai_purchase_model_*` | ver tabla de niveles |
| `http_timeout_seconds` | `ai_purchase_http_timeout_seconds` | 120 |
| `ro_user` | `ai_purchase_ro_user` / `AI_PURCHASE_RO_USER` | — |
| `ro_password` | `ai_purchase_ro_password` / `AI_PURCHASE_RO_PASSWORD` | — |
| `sql_whitelist` | `ai_purchase_sql_whitelist` | ver `DEFAULT_WHITELIST` |
| `sql_max_rows` | `ai_purchase_sql_max_rows` | 200 |
| `sql_timeout_seconds` | `ai_purchase_sql_timeout_seconds` | 30 |

`res_partner` queda fuera de la lista blanca a propósito: mezcla droguerías con
clientes finales y a OpenRouter no deben salir datos de clientes.
