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
- **Acceso restringido** al personal de compras y a los administradores: el
  grupo técnico *Asistente de Compras IA* lo implican *Compras / Usuario* y
  *Ajustes* (no es una casilla de la ficha del usuario). La configuración y el
  consumo de IA son solo para administradores.

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

## Rotación, stock y pendientes (`ai_v_rotacion`, `ai_v_stock`, `ai_v_pendiente`)

Vistas SQL por producto y farmacia (`company_id`), en la unidad del producto,
en el menú *Compras IA → Análisis* y consultables por el agente. Las
pantallas muestran solo las farmacias permitidas del usuario (regla por
empresa, como el resto de Odoo); el chat, que consulta con el rol de solo
lectura, las ve todas.

- **Rotación** (`ai_v_rotacion`): demanda de las últimas N semanas
  **completas** (lunes a domingo en la hora de la sucursal, `res_partner.tz`
  de la empresa o `America/Caracas`; la semana en curso no entra). Demanda =
  TPV (pedidos pagados; sin los tickets que liquidan un pedido de venta) +
  pedidos de venta confirmados. Las semanas sin venta cuentan como 0. Trae
  media, mediana, desviación, MAD y la **demanda depurada**: la media sin las
  semanas que se alejan de la mediana más de *factor × MAD*. Si el MAD es
  menor que 1 se usa 1; si no, con ventas esporádicas (MAD = 0) toda semana
  con venta sería atípica. Consecuencia: un producto vendido en una sola
  semana, en más de *factor* unidades, queda con demanda depurada 0 (venta
  esporádica, filtro *Venta esporádica*). N (`rotation_window_weeks`, 8) y el
  factor (`outlier_mad_factor`, 3) se leen de Ajustes en cada consulta.
- **Stock** (`ai_v_stock`): suma de los quants de las ubicaciones internas;
  incluye las existencias en 0 y negativas.
- **Pendiente** (`ai_v_pendiente`): órdenes confirmadas con la recepción sin
  completar. Por línea, lo que falta por entrar en sus movimientos de stock;
  si la línea no tiene movimientos (compras importadas o simuladas), pedido −
  recibido. Lo que se cierra sin pendiente al recibir no cuenta. Aparte,
  `atrasado` (fecha prevista vencida) y `en_solicitud` (solicitudes de
  presupuesto sin confirmar, que no se restan).

En `dromax_ai_multi` (3 años simulados): 5186 filas de rotación (~3,5 s),
5576 de stock y 1613 de pendientes (~0,6 s cada una).

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

## Reporte de códigos de barras (`ai.barcode.mismatch`)

Menú *Compras IA → Códigos de barras*, botón **Actualizar reporte** (lo rehace
completo; ~11 s con la oferta actual). Tipos de incoherencia:

| Tipo | Qué es |
|---|---|
| Nombre parecido, código distinto | Producto propio (con o sin código) que se parece a una oferta cuyo código no casa con ningún producto propio; hasta 3 sugerencias por producto, con su similitud |
| Sin oferta en droguerías | Producto propio con código que ninguna droguería ofrece |
| Producto sin código de barras | Producto propio sin código |
| Código sin producto propio | Código que ofrecen las droguerías y no tenemos (una fila por código) |
| Posible otra presentación | Mismo código con un precio más del triple en otra droguería |
| Código duplicado | Dos productos propios con el mismo código normalizado |

Solo cuentan los productos activos, comprables y almacenables o consumibles.
Los nombres se comparan normalizados (tildes, abreviaturas, unidades pegadas al
número) con trigramas de `pg_trgm` y una regla calibrada con la oferta real:
mismos números y dosis, misma cantidad de empaque y talla, y que coincidan la
primera palabra y el laboratorio (última palabra no genérica). Así no casan
«10 MG» con «18 MG», «X 10» con «X 30» ni el mismo genérico de otro laboratorio.

Parámetros (`dromax_asistente_compras.*`): `barcode_similarity_threshold`
(0,6) y `barcode_presentation_ratio` (3). El agente puede consultar la tabla.

## Importar el inventario unificado (`scripts/import_inventory.py`)

Carga el Excel de inventario unificado de las farmacias («INVENTARIO CON
CANTIDAD FINAL.xlsx», una fila por producto y un bloque de columnas por
farmacia) en una base con las sucursales de Dromax. El Excel no va en el repo.

```bash
cd ~/odoo-dev && source env/bin/activate
INVENTORY_DB=<base> INVENTORY_XLSX="/ruta/INVENTARIO CON CANTIDAD FINAL.xlsx" \
INVENTORY_DRY_RUN=1 INVENTORY_REPORT=/tmp/incidencias.csv \
python3 odoo/odoo-bin shell -c odoo/debian/odoo.conf -d <base> --no-http \
    < custom_addons/dromax_asistente_compras/scripts/import_inventory.py
```

- `INVENTORY_DB` tiene que ser el nombre de la base (protección).
- `INVENTORY_DRY_RUN=1` solo informa; sin esa variable, carga.
- `INVENTORY_REPORT` guarda en CSV las incidencias (códigos de barras no
  válidos, repetidos o adicionales, filas con varias referencias, cantidades
  negativas).

Crea un producto por fila (clave: referencia interna, ID externo
`dromax_inventario.producto_<REF>`), con el primer código de barras válido y
libre, el precio de venta mediano y sin impuestos; y por sucursal, el costo y
las existencias del almacén principal como ajuste de inventario. Bloques →
sucursales: DROMAX, FARMAX → FARMACIA FARMAX, GANGA → FARMACIA FARMAGANGA,
IDEAL, LA45 → FARMACIA LA 45, MANAL, SOLIDARIA → FARMARCIA FSC (diccionario
`BRANCHES` del script). Se puede repetir: actualiza y solo ajusta las
existencias que cambiaron. Con el Excel del 2026-10-26: 5008 productos y
~441.000 unidades en ~6 min.

## Historial simulado sobre el inventario real (`scripts/simulate_history.py`)

Para probar el agente con productos que coinciden con la oferta de las
droguerías: sobre una base con el inventario ya importado (y la oferta FTP
copiada, sin conectarse a los FTP), inventa ventas y compras coherentes con
las existencias de cada farmacia. No toca el stock.

```bash
SIM_DB=<base> python3 odoo/odoo-bin shell -c odoo/debian/odoo.conf -d <base> --no-http \
    < custom_addons/dromax_asistente_compras/scripts/simulate_history.py
```

- `SIM_DB` tiene que ser el nombre de la base (protección); `SIM_SEED`
  (17) y `SIM_WEEKS` (26 semanas completas) son opcionales.
- La demanda semanal sale del stock real: la mayoría con 1 a 6 semanas de
  cobertura, y además productos cortos, sobrados, sin movimiento, nuevos y
  agotados (con stock en otras farmacias). Semanas atípicas por arriba y en
  cero.
- Ventas por TPV (un ticket por farmacia y día) y pedidos de venta grandes en
  la farmacia con más productos; compras recibidas cada lunes, pendientes
  (algunas atrasadas o parciales) y solicitudes en borrador, con la droguería
  y el precio de la oferta cuando el producto la tiene.
- Se escribe con SQL (sin movimientos de inventario ni contabilidad) y no se
  puede repetir sobre la misma base: hay que restaurarla.

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
