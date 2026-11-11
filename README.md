# Dromax · Agente IA (Odoo 17)

Módulo de Odoo 17 que arma, en un chat, una propuesta de compra semanal por
laboratorio y, con el visto bueno del comprador, la convierte en solicitudes de
presupuesto (`purchase.order`) en borrador; después reparte lo comprado a las
farmacias con traslados.

**Flujo de Dromax:** toda compra la hace la sucursal matriz (Dromax, la
droguería del grupo), que recibe la mercancía y la distribuye a las farmacias
con traslados cuando lo necesitan. Las farmacias no compran a las droguerías
ni se trasladan mercancía entre ellas.

El avance, las decisiones y lo pendiente están en [ESTADO.md](ESTADO.md).

Instalación en el servidor de Dromax: ver [INSTALACION.md](INSTALACION.md).

## Principios

- **Reactivo, no autónomo**: el comprador dispara el análisis con preguntas
  predefinidas; el cron del lunes solo precalcula.
- **Solo lectura** sobre Postgres: SQL validado (una sola sentencia
  `SELECT`/`WITH … SELECT`, lista blanca de tablas y vistas) ejecutado con el
  rol `dromax_ai_ro`.
- **Escrituras acotadas**: el modelo solo ajusta líneas de la propuesta de
  compra cuando el comprador se lo pide (`update_proposal_lines`). Las
  `purchase.order` en borrador las crea un botón, nunca el modelo.
- **Acceso restringido** al personal de compras y a los administradores: el
  grupo técnico *Agente IA* lo implican *Compras / Usuario* y
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
en el menú *Agente IA → Análisis* y consultables por el agente. Las
pantallas muestran solo las farmacias permitidas del usuario (regla por
empresa, como el resto de Odoo); el chat, que consulta con el rol de solo
lectura, las ve todas (la compra es de la matriz para toda la red).

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

## Necesidad de compra (`ai_v_faltantes`)

Lo que tiene que comprar la sucursal matriz para toda la red: una fila por
producto con faltante > 0, en *Agente IA → Análisis → Necesidad de compra*.
Es la base de la propuesta. Comparte el cálculo con el reabastecimiento
(`services/network_sql.py`), para que lo que se compra y lo que se reparte
salgan de la misma cuenta.

- **Punto de pedido y objetivo** (Ajustes, en `services/network_sql.py`):
  una farmacia solo tiene falta cuando lo que tiene (stock + pendiente + en
  camino) baja al *punto de pedido*, un % de la venta de las semanas de
  cobertura (`reorder_point_pct`, 50). Entonces se repone hasta el
  *objetivo*: esa venta más el *margen de seguridad* (`safety_margin_pct`,
  20), en unidades enteras (al menos la venta redondeada hacia arriba; el
  margen se redondea al más cercano, para que en poca venta no duplique el
  pedido). Así no se mueve ni se compra por una o dos unidades debajo de la
  venta, y queda margen para una semana alta. Ejemplo con 1 semana: venta
  10/sem → entra con 5 o menos y se repone hasta 12. Con 100 y 0 se vuelve al
  cálculo sin umbrales.
- **Falta de cada farmacia** = ⌈objetivo − stock − pendiente − lo que ya
  viene en camino por traslado⌉. El stock negativo cuenta como 0 (no se
  compra para tapar ventas sin existencia). Con stock en cero también entra la
  venta esporádica (demanda depurada 0): se repone su venta media. Lo que le
  sobra a una farmacia no cubre a otra.
- **Cuándo compra la matriz**: si, después de mandar lo que les falta a las
  farmacias que llegaron a su punto de pedido, le queda su propio punto de
  pedido o menos (el de su venta propia; 0 si no vende). Así no compra una
  unidad cuando todavía tiene para mandar, y sin falta en las farmacias solo
  compra para su venta al llegar a su punto.
- **Cantidad sugerida** = ⌈Σ faltas de todas las farmacias debajo del
  objetivo (también las que aún no llegaron a su punto: si se compra, se
  repone a todas) + objetivo de la venta de la matriz − stock libre de la
  matriz (su stock menos lo que ya sale hacia las farmacias) − compras
  pendientes de la matriz⌉. Las solicitudes sin confirmar no se restan
  (`en_solicitud` avisa).
  `detalle_farmacias` dice cuánto le falta a cada farmacia («FARMAX (10), LA
  45 (5)»). `cobertura_actual` = (stock libre + pendiente) / venta semanal de
  la red: menor = más urgente, y es el orden de la lista.
- **Droguería sugerida**, entre las ofertas disponibles (`ai_v_oferta`):
  primero las de precio coherente con el costo de la matriz (hasta el
  triple), luego las que tienen stock para toda la cantidad, y entre ellas la
  más barata. `revisar_presentacion` avisa si la elegida pasa del triple del
  costo: algunas droguerías usan el mismo código de barras para un empaque
  mayor (p. ej. un dispensador de 50 blísteres frente al blíster que vende la
  farmacia). También trae precio, mejor precio, monto, stock de la droguería,
  vencimiento y laboratorio según la droguería.
- Ajustes: semanas de cobertura (`coverage_weeks`, 1), punto de pedido
  (`reorder_point_pct`, 50 %), margen de seguridad (`safety_margin_pct`, 20 %;
  el 0 se guarda explícito, porque Odoo guarda un entero 0 como vacío) y
  *Sucursal matriz* (`matrix_company_id`; vacío = la que tenga DROMAX en el
  nombre), leídos en cada consulta.

En `dromax_ai_inventario` (inventario real + 26 semanas simuladas), con 1
semana, 50 % y 20 %: 371 productos a comprar por DROMAX (7082 unidades), ~3 s.
Sin umbrales eran 709 (8160 unidades) y 381 de ellos de 1 o 2 unidades; ahora
son 183, y son productos de poca venta en la sucursal que los necesita (61 con
venta de la red de 2/sem o menos).

## Reabastecimiento desde la matriz (`ai_v_reabastecimiento`)

Menú *Agente IA → Reabastecimiento*. Lo que la sucursal matriz (Dromax, la
droguería del grupo que distribuye a las farmacias) puede mandar por traslado
a cada farmacia. Una fila por producto y farmacia:

- **Falta** = objetivo (venta semanal × semanas de cobertura + margen de
  seguridad) − stock − compras pendientes − lo que **ya viene en camino por
  traslado** (borradores y traslados sin terminar que llegan a la farmacia
  desde otra sucursal o desde tránsito). Solo entra al llegar al **punto de
  pedido** (ver la necesidad de compra). Estado **En cero** (sin stock) o
  **Por agotarse**. Con stock en
  cero también entra la venta esporádica (demanda depurada 0 pero con venta en
  el período): se repone su venta media.
- **Disponible en la matriz** = su stock − lo que ya sale hacia las
  farmacias − su propia venta de las mismas semanas. Lo que no alcanza lo
  compra la matriz (está en la necesidad de compra). Si no alcanza para
  todas, se reparte primero a la farmacia más urgente (menor cobertura) y la
  línea queda **Parcial**. Solo productos almacenables, en su unidad.
- **Valor** = a trasladar × costo de la matriz (el costo que pasa el traslado).
  Vista de tabla dinámica y gráfico con el valor por farmacia y estado.
- **Generar borradores de traslado** (botón de la lista al elegir líneas;
  requiere Inventario / Usuario y acceso a la matriz y a las farmacias): un
  *Traslado interno* en borrador de la matriz a cada farmacia, del tipo de
  traslado interno de su almacén, de sus existencias a las de la farmacia, con
  origen «Reabastecimiento <fecha>». Es el borrador del módulo de traslados
  (`advanced_interbranch_transfer`): en cada uno, **Generar Traslados
  Definitivos** arma la cadena por tránsito con el costo de origen, los lotes
  y los pasos de cada almacén. Las cantidades se vuelven a leer al pulsar, y
  lo ya generado deja de sugerirse (si se cancela un borrador, vuelve).
- Ajustes: *Sucursal matriz* (`matrix_company_id`; vacío = la que tenga
  DROMAX en el nombre), las semanas de historial y de cobertura, el punto de
  pedido y el margen de seguridad.
- Chat: *¿A quién abastezco desde Dromax?* (nivel medio) resume la vista por
  farmacia y estado.

En `dromax_ai_inventario`, con 1 semana, 50 % y 20 %: 111 líneas (1210
unidades, 3173 de valor), ~2,5 s. Sin umbrales eran 272 (1648 unidades) y 145
de 1 o 2 unidades; ahora son 46, casi todas (40) de productos que la farmacia
vende 2/sem o menos.

## Propuesta de compra (`ai.purchase.proposal`)

Menú *Agente IA → Propuestas*. **Una propuesta por semana de la sucursal
matriz para toda la red, con una línea por producto**, organizada por
laboratorio en las líneas (no una propuesta por laboratorio: con datos reales
saldrían ~190 por semana con una mediana de 3 líneas).

- **Cálculo:** la tarea programada *Agente IA: propuesta de compra
  semanal* (lunes 03:00 de Caracas, 07:00 UTC; `noupdate`, se ajusta en
  producción) o el botón *Calcular propuesta* de la lista. Copia
  `ai_v_faltantes` como foto: una línea por producto, con la falta de cada
  farmacia, la venta de la red y de la matriz, el stock libre y lo pendiente
  de la matriz, droguería, precio, descuento, vencimiento y un **motivo**
  legible («Falta: farmacias 15 (FARMAX (10), LA 45 (5)) + venta propia 2/sem
  × 1 sem − stock 4 − pendiente 0 = 13. …»). No llama a la IA ni crea órdenes. Las
  propuestas del mismo modo que seguían abiertas pasan a *Descartada*.
- **Laboratorio** de cada línea: el campo del producto indicado en Ajustes
  (*Campo de laboratorio*, para el módulo de laboratorios de Dromax); si no
  hay, el texto entre corchetes al final del nombre («… [LETI]»); si no, el
  que informa la droguería; si no, «SIN LABORATORIO».
- **Revisión:** el comprador cambia la cantidad final (0 = no pedir), la
  droguería (entre las que tienen oferta del producto; el precio, descuento,
  stock y vencimiento se actualizan solos) y el motivo. La cantidad y la
  droguería sugeridas no cambian: *Líneas ajustadas* y *Ajuste del
  comprador (%)* (Σ |final − sugerida| / Σ sugerida) miden cuánto corrige al
  agente. Al primer cambio la propuesta pasa a *En revisión*.
- **Generar borradores** (botón de la propuesta, o *Acción → Generar
  borradores* sobre las líneas seleccionadas de la lista, p. ej. un
  laboratorio): una orden de compra en borrador de la matriz por droguería
  (`origin` = nombre de la propuesta), con la cantidad final y el precio final
  de la oferta (ya con descuentos), en la unidad del producto; impuestos,
  descripción y fecha prevista como los pondría Odoo al elegir el producto. Se
  puede hacer por partes: lo nuevo se suma al borrador que la misma propuesta
  ya creó para esa droguería; los borradores hechos a mano no se
  tocan. No entran las líneas sin droguería, con cantidad 0, ya pedidas o
  marcadas *Revisar presentación* que el comprador no cambió. Cada línea
  guarda su línea de compra y ya no se puede modificar. Hace falta el permiso
  de Compras y acceso a la sucursal matriz. Lo creado aparece en `ai_v_pendiente.en_solicitud`. La
  propuesta pasa a *Convertida en borradores* cuando no queda nada por pedir.
- Estados: *Lista* → *En revisión* → *Convertida en borradores* o
  *Descartada*; cerrada no se modifica.
- Permisos: compradores leen, calculan y ajustan; borrar, solo
  administradores. Las líneas son de la matriz: las ve quien tiene acceso a
  ella.

## Automatización: borradores de compra y de traslado

Para que compras no dependa de que alguien entre a pulsar botones ni de que
una farmacia avise que algo falta. Se activa en *Ajustes → Agente IA →
Automatización* (cada una con su responsable y un botón **Ejecutar ahora**,
que hace lo mismo que la tarea programada).

**Compra semanal** (tarea *Agente IA: propuesta de compra semanal*, lunes
03:00 en Caracas; ajuste *Borradores de compra automáticos*):

1. Cancela los borradores de propuestas anteriores que siguen sin confirmar
   (los reemplaza la compra de hoy; los hechos a mano en Compras y las
   solicitudes ya enviadas no se tocan).
2. Calcula la propuesta de la semana (siempre, aunque el ajuste esté
   apagado).
3. Crea las órdenes de compra en borrador de la matriz, una por droguería,
   con todas las líneas por pedir. Quedan fuera, para decidir en la
   propuesta: las líneas sin droguería, las marcadas para revisar la
   presentación y los productos que ya están en otras solicitudes sin
   confirmar (se pedirían dos veces).
4. Deja al *Responsable de compras* una actividad «Revisar la compra de la
   semana» en la propuesta: los borradores creados con su monto, lo que quedó
   fuera, lo cancelado y un aviso si la oferta tiene más de un día. Él queda
   como comprador de las órdenes. Sin el ajuste, la actividad solo avisa que
   la propuesta está lista.

**Reabastecimiento diario** (tarea *Agente IA: borradores de reabastecimiento
diario*, 06:00 en Caracas; ajuste *Borradores de traslado automáticos*):
crea los borradores de traslado de todas las líneas de *Reabastecimiento*
(uno por farmacia) y deja al *Responsable de despacho* una actividad en cada
uno («Despachar reabastecimiento a …»). Lo que ya va en borrador cuenta como
en camino, así que al día siguiente no se duplica; lo que la matriz no tiene
para mandar entra en la compra del lunes.

Probado sobre `dromax_ai_inventario` (y deshecho): la compra crea 3
borradores (75, 134 y 37 líneas; 123 líneas sin droguería y 2 para revisar
quedan fuera) en ~13 s; el reabastecimiento, 6 borradores en ~7 s, y una
segunda corrida no crea ninguno.

## Oferta de droguerías (`ai_v_oferta`)

La oferta llega del módulo FTP (`advanced_interbranch_transfer`, dependencia
del módulo): una fila de `supplier.product.staging` por producto y droguería.
La vista `ai_v_oferta` (menú *Agente IA → Oferta por producto*) la une a los
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

## Comparador de precios (`ai_v_comparador`)

Menú *Agente IA → Comparador de precios* (pedido en la reunión del
2026-11-09, inspirado en el portal i-compra 360). **Una fila por producto**
(código de barras válido), aunque cada droguería lo escriba distinto: nombre
de nuestro catálogo (o el de la droguería más barata), droguerías con stock,
la más barata con su precio y descuentos, la segunda, el ahorro y alertas.
Al abrirla, el **visor** con la oferta de cada droguería (precio de lista,
descuentos en cascada, % total, precio final, stock y vencimiento). Al buscar
«ATAMEL» sale una fila por presentación.

- Sale de `ai_v_oferta` y comparte sus reglas: solo cuentan las ofertas con
  stock, precio, **lote no vencido** y **código válido** (EAN-13 con dígito de
  control correcto, `ai_barcode_valid`; códigos como «0» o «10» juntaban
  productos distintos y ya no se cruzan). Estas reglas valen también para la
  necesidad de compra y la propuesta.
- Alertas: *Posible otra presentación* (el precio más alto pasa del triple del
  más bajo; el ahorro no es real), *vence pronto* (lote de la más barata a
  menos de 90 días) y *poco stock* (1 o 2 unidades).
- Filtros: con stock (por defecto), en 2 o más droguerías, en nuestro
  catálogo, ahorro de 10 % o más, y las alertas; agrupa por laboratorio o
  droguería más barata.
- **Clic en un producto:** una ventana emergente con la mejor oferta de cada
  droguería (la más barata primero), sin salir de la lista. La ficha
  completa, con todos los lotes, sigue disponible.
- **Añadir a borrador de compra** (permiso de Compras; botón de la lista al
  marcar productos o de la ventana de uno): un asistente con cada producto,
  la droguería (por defecto la más barata, se puede cambiar entre las que lo
  tienen; el precio sale de su oferta) y la cantidad (por defecto la de la
  necesidad de compra, o 1, sin pasar del stock de la droguería). Al
  confirmar, se agregan a la orden en borrador de la sucursal matriz con esa
  droguería que creó el comparador para el usuario (origen «Comparador de
  precios»; si no hay, se crea) y, si el producto ya está, se suma la
  cantidad: así se arma un pedido en varias búsquedas. Los borradores hechos
  a mano o desde la propuesta no se tocan. Solo se pueden pedir productos de
  nuestro catálogo; los demás se avisan.
- **Tope de stock:** no se puede pedir más de lo que la droguería tiene en
  la oferta elegida, sumando lo que ya está en su borrador del comparador.
  En el asistente, al pasarse, la cantidad baja al stock y se avisa (la fila
  se marca en rojo); al confirmar se vuelve a comprobar con la oferta de ese
  momento (precio y stock) y, si no alcanza, se dice cuánto se puede agregar
  todavía o que la droguería ya no lo tiene.
- **Aviso con «Ver en Compras»:** al confirmar, la ventana se cierra y un
  aviso dice en qué órdenes quedó cada producto, con un botón que abre esas
  órdenes (la ficha, si es una sola) en la misma pestaña; la ruta de migas
  permite volver al comparador. Para seguir comparando, basta cerrar el aviso.
- El agente lo usa para *Compara precios entre droguerías*.

En `dromax_ai_inventario`: 15 724 ofertas en 8788 productos (4690 en 2 o más
droguerías, 140 con posible otra presentación), ~0,2 s. La lista de antes
sigue como *Oferta por droguería*.

## Reporte de códigos de barras (`ai.barcode.mismatch`)

Menú *Agente IA → Códigos de barras*, botón **Actualizar reporte** (lo rehace
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
libre, en Unidades (todo se trabaja por unidad), el precio de venta mediano y
sin impuestos (el IVA del 16 % se configura en producción); y por sucursal, el
costo y las existencias del almacén principal como ajuste de inventario. Bloques →
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

Menú **Agente IA → Chat** (grupo *Agente IA / Usuario*).

- Preguntas predefinidas (`services/presets.py`), cada una con su nivel de
  modelo: *Arma la compra de esta semana* (avanzado), *Revisa un laboratorio*
  (avanzado; pide el laboratorio en el cuadro de texto antes de enviarse),
  *¿A quién abastezco desde Dromax?* (medio), *Productos por agotarse*, *Más vendidos
  de las últimas 4 semanas*, *Compras pendientes de recibir*, *Compara precios
  entre droguerías* y *Stock sin rotación*. También se puede escribir
  libremente eligiendo el nivel.
- El agente (`services/agent.py`) llama a OpenRouter con sus herramientas
  hasta tener respuesta (tope `agent_max_steps`, 8 por defecto). Bajo cada
  respuesta se ven el modelo, el costo, la duración y las consultas que hizo.
- Herramientas (`services/tools.py`):
  - `run_readonly_sql`: SQL de solo lectura (ver arriba).
  - `get_proposal` (`services/proposal_tools.py`): la propuesta semanal
    guardada (la vigente o la que se indique). Sin filtros devuelve el
    resumen: totales, laboratorios de mayor monto, cuántas líneas necesitan
    atención y las de mayor monto a revisar por presentación. Con filtros
    (laboratorio, producto, farmacia —las líneas en que a esa farmacia le
    falta el producto— o por pedir / ajustadas / revisar presentación / sin
    droguería / en solicitud / pedidas) devuelve 40
    líneas (el modelo puede pedir hasta 80) con su `linea_id`, el motivo y las ofertas de las demás
    droguerías, más el total de líneas y el monto de todas las que coinciden.
    Cada línea trae cuánto le falta a cada farmacia. Un laboratorio sin líneas
    devuelve los nombres parecidos (trigramas).
  - `update_proposal_lines`: hasta 50 cambios de cantidad final, droguería
    (por nombre, entre las que ofertan el producto; el precio sale de su
    oferta) o nota (se agrega al motivo). Pasa por el `write` de la línea, con
    sus mismas reglas (propuesta cerrada, línea ya pedida). Aplica los cambios
    válidos, devuelve el antes y el después y los errores, y deja en el
    historial de la propuesta quién cambió qué desde el chat.
  - Las dos ven todas las sucursales del usuario, no solo las marcadas en el
    selector de empresas (las líneas son de la matriz).
- *Arma la compra de esta semana* parte de la propuesta guardada: si no hay
  una calculada esta semana (lunes 00:00 hora local), la calcula antes de
  preguntar. Con la propuesta real de `dromax_ai_inventario` (1412 líneas):
  una llamada a `get_proposal`, ~0,17 USD y ~30 s con Claude Opus.
- Bajo la última respuesta que leyó o ajustó una propuesta aparecen los
  botones de borradores (con permiso de Compras; pide confirmación y usa las
  líneas de la matriz, las mismas que leyó el agente) y
  **Abrir propuesta**. Si la respuesta solo trató líneas de un laboratorio
  (p. ej. *Revisa un laboratorio*), el botón es **Generar borradores de
  LETI** y pide solo ese laboratorio; si no, **Generar todos los
  borradores**. Si no encontró ninguna línea, no hay botones.
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
Agente IA** (o en el menú *Agente IA → Configuración*):

| Nivel | Modelo inicial | Uso |
|---|---|---|
| Básico | `deepseek/deepseek-v4-flash` | Consultas simples y rankings |
| Medio | `google/gemini-3.8-flash` | Análisis de ventas e inventario |
| Avanzado | `anthropic/claude-opus-5.5` | Propuesta de compra, ajustes y flujo dinámico |

`services/openrouter.py` elige el modelo por nivel; las tareas de compras
(`purchase_proposal`, `proposal_adjustment`, `dynamic_flow`) van siempre al
avanzado, las pida quien las pida. El botón *Probar llave* muestra el consumo y
el tope de crédito de la llave.

Cada llamada, buena o fallida, queda en `ai.usage.log` (*Agente IA →
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
