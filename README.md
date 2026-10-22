# Dromax · Asistente de Compras IA (Odoo 17)

Módulo de Odoo 17 que arma, en un chat, una propuesta de compra semanal por
laboratorio y, con el visto bueno del comprador, la convierte en solicitudes de
presupuesto (`purchase.order`) en borrador.

El avance, las decisiones y lo pendiente están en [ESTADO.md](ESTADO.md).

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
Las vistas `ai_v_*` del módulo no lo necesitan: se recrean al actualizar el
módulo y su `init()` vuelve a dar el permiso al rol configurado en `ro_user`.

## Oferta de droguerías (`ai_v_oferta`)

La oferta llega del módulo FTP (`advanced_interbranch_transfer`, dependencia
del módulo): una fila de `supplier.product.staging` por producto y droguería.
La vista `ai_v_oferta` (menú *Compras IA → Oferta por producto*) la une a los
productos propios por **código de barras normalizado** y añade la comparación
entre droguerías: cada una entra con su mejor oferta disponible (con stock),
aunque traiga varios lotes; `orden_precio` es su puesto (1 = la más barata) y
cada fila lleva el mejor precio, el de la segunda droguería y cuántas tienen
el código disponible. El costo que cuenta es el
precio final, que ya trae todos los descuentos.

La normalización (`services/barcode.py`) deja todo como EAN-13: quita lo que
no sean dígitos, rellena con ceros los UPC-A de 12 dígitos y los EAN-8, y
convierte los GTIN-14 con indicador de empaque (INTERCONTINENTAL) al artículo,
recalculando el dígito de control. Se guarda en `ai_barcode_norm`, tanto en
`product.product` como en la oferta. Si dos productos propios comparten
código normalizado, la vista toma el más antiguo.

## Chat del agente

Menú **Compras IA → Chat** (grupo *Asistente de Compras IA / Usuario*).

- Preguntas predefinidas (`services/presets.py`), cada una con su nivel de
  modelo: *Arma la compra de esta semana* (avanzado), *Productos por
  agotarse*, *Más vendidos de las últimas 4 semanas*, *Compras pendientes de
  recibir*, *Compara precios entre droguerías* y *Stock sin rotación*. También se puede escribir libremente
  eligiendo el nivel.
- El agente (`services/agent.py`) llama a OpenRouter con la herramienta
  `run_readonly_sql` hasta tener respuesta (tope `agent_max_steps`, 8 por
  defecto). Bajo cada respuesta se ven el modelo, el costo, la duración y las
  consultas SQL que hizo.
- El system prompt (`services/prompt.py`) lleva el contexto del negocio, las
  reglas contra el sobre-stock y el esquema de las tablas permitidas, filtrado
  contra las columnas que existen en la base (`services/schema_catalog.py`).
- Las conversaciones (`ai.purchase.chat.session` / `.message`) son privadas:
  cada comprador ve solo las suyas.

Los parámetros del cálculo (semanas de historial, umbral de atípicos en MAD y
semanas de cobertura) se cambian en Ajustes; los valores iniciales (8, 3 y 1)
son provisionales hasta hablar con el encargado de compras.

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
