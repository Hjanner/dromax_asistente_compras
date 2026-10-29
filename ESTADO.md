# Estado del proyecto

Última actualización: 2026-10-29. Qué está hecho, qué decidimos y qué falta.
El README explica cómo funciona lo que ya existe.

## Hecho

- **Fase 1 (base del asistente)** — versión `17.0.0.4.0`, 44 tests en verde.
  - Validador SQL y ejecución con el rol de solo lectura `dromax_ai_ro`.
  - Cliente de OpenRouter con tres niveles de modelo y registro de consumo
    (`ai.usage.log`).
  - Ajustes: llave, modelos por nivel, parámetros del cálculo de faltantes y
    límites SQL.
- **Chat del agente** (menú *Compras IA → Chat*) con preguntas predefinidas,
  selector de nivel y traza de las consultas SQL de cada respuesta.
- **Módulo FTP** (`advanced_interbranch_transfer`, fuera de este repo):
  instalado y probado con tres droguerías reales (VITALCLINIC,
  INTERCONTINENTAL, INSUAMINCA). Parches locales hechos el 2026-10-20:
  - host con puerto (`host:58021`) y con `ftp://`, más un tiempo máximo de 30 s;
  - lectura de archivos en Latin-1 o UTF-8, con caracteres nulos y con
    comillas sin cerrar en la descripción (antes se perdían filas);
  - descuentos en cascada (`15+0+0+7`) convertidos a un porcentaje total;
  - host, usuario y clave FTP visibles solo para administradores. Los botones
    los leen con `sudo()`, así que cualquier usuario interno puede probar la
    conexión y descargar.
- **Carga de inventarios robusta** (módulo FTP, commit `60d4970`, 2026-10-22):
  - tarea programada *Inventarios FTP de droguerías* cada 30 min, **instalada
    desactivada** (se activa a mano, porque se conecta a los FTP reales);
  - no reemplaza la oferta si el archivo viene vacío o trae menos del 50 % de
    los productos cargados (`advanced_interbranch_transfer.ftp_min_rows_ratio`);
    botón *Cargar de todas formas* para un recorte real;
  - resultado de la última carga en el contacto; las líneas malas se
    descartan y se informan, sin abortar la carga;
  - formato por droguería: archivo, codificación, separador y mapa de columnas;
  - oferta con laboratorio, descuento original (`15+0+0+7`) y datos extra;
  - lista *Inventario → Productos → Oferta de droguerías* (reemplaza la
    acción manual 706, ya borrada) y botón inteligente en el contacto;
  - 17 tests en el módulo FTP (2 de navegador).

- **Oferta en el agente** (versión `17.0.0.5.0`, 58 tests, 2026-10-22):
  - `services/barcode.py`: código normalizado a EAN-13 (UPC-A y EAN-8 con
    ceros, GTIN-14 → artículo con nuevo dígito de control), guardado en
    `ai_barcode_norm` de `product.product` y de la oferta, más la fecha de
    vencimiento del lote como fecha;
  - vista `ai_v_oferta` (menú *Oferta por producto*): oferta × producto propio
    con la comparación entre droguerías (cada una con su mejor oferta
    disponible aunque traiga varios lotes): puesto, mejor precio, segundo
    precio y droguerías con stock; `init()` da SELECT al rol `dromax_ai_ro`
    cada vez que se recrea la vista;
  - la herramienta solo anuncia al modelo las tablas de la lista blanca que
    existen (antes listaba también las vistas `ai_v_*` futuras);
  - en el prompt (catálogo, reglas y consejos de SQL) y la pregunta
    predefinida *Compara precios entre droguerías*;
  - el módulo ahora depende de `advanced_interbranch_transfer`.
  - Con la oferta real: la normalización sube de 3251 a 3626 los códigos
    comunes entre INSUAMINCA y VITALCLINIC; 2012 códigos están en las tres
    droguerías y 2626 en dos. En `DROMAX_AI_DEV` ningún producto propio casa
    (solo hay 11, sin esos códigos): el cruce real se verá con la base de Daniel.

- **Agente sin respuesta al agotar las consultas** (2026-10-22): Gemini vía
  OpenRouter ignora `tool_choice: "none"` y seguía pidiendo consultas, así que
  la respuesta final salía vacía. Ahora la última llamada va sin herramientas,
  con las consultas del turno en texto; el prompt dice el tope de consultas y
  la penúltima avisa que queda una. Probado en vivo con `comprador.prueba`
  (*Compara precios entre droguerías*: ~7 consultas, ~0,04 USD, < 1 min).

- **Claves FTP cifradas** (módulo FTP 1.2, commit `177255b`, 2026-10-23):
  Fernet con la llave en la variable de entorno `FTP_SECRET_KEY` (varias
  separadas por coma para rotarla). Sin llave no se guardan claves; con otra
  llave no se conecta. Migración que cifra las claves en claro; las tres
  locales ya están cifradas. README del módulo con la configuración en el
  servidor. 24 tests en el módulo FTP.

- **Reporte de códigos de barras** (versión `17.0.0.6.0`, 70 tests,
  2026-10-23): `ai.barcode.mismatch`, menú *Códigos de barras*, seis tipos
  (nombre parecido con otro código, sin oferta, sin código, código sin
  producto propio, posible otra presentación, duplicado). Regla de nombres
  calibrada con los ~4600 códigos comunes entre droguerías y revisando
  candidatos de códigos distintos (descarta otra dosis, otro empaque, otra
  molécula y el mismo genérico de otro laboratorio). Genera en ~11 s; con un
  catálogo simulado de 6600 productos, la parte de nombres tarda ~21 s.
  Lección: en REPEATABLE READ, un índice creado sobre filas actualizadas en la
  misma transacción no se usa (`indcheckxmin`); las temporales se crean de
  una vez con `CREATE TABLE AS`. En la base local salen 154 posibles
  presentaciones distintas (p. ej. «PINZA UMBILICAL X 1» a 0,30 frente a
  «X 100» a 31,50 con el mismo código).

- **Acceso** (2026-10-23, pedido del equipo): el módulo lo ven solo el
  personal de compras y los administradores, automáticamente (el grupo del
  asistente lo implican Compras / Usuario y Ajustes, y ya no es una casilla).
  Las acciones de ventana tienen grupos (no se abren por URL) y el chat
  rechaza con un mensaje claro a quien no tenga acceso. En el módulo FTP
  (commit `98c0bee`), la pestaña *Configuración FTP* y sus botones son solo
  de administradores; el botón *En oferta* sigue para todos. 76 tests en el
  asistente y 25 en el módulo FTP.

- **Base multisucursal local** (2026-10-26): `dromax_ai_multi`, copia de la
  base de Daniel (8 sucursales) con datos simulados: 881 productos, 1248
  ventas POS semanales (723.098 líneas, nov 2023 a oct 2026), 3744 compras
  (43 pendientes de recibir, 5 en borrador), 67 ventas B2B, 292 productos
  agotados, 2271 reglas de reabastecimiento, 26 productos sin código y 6
  códigos duplicados. Precios de droguerías en `product.supplierinfo`; **la
  oferta FTP (`supplier_product_staging`) está vacía** en esa base, así que
  *Oferta por producto*, la comparación de droguerías y la parte de oferta
  del reporte de códigos de barras no muestran nada allí. Configuración del
  asistente copiada de `DROMAX_AI_DEV`; acceso dado a `hanner.dev@gmail.com`.

- **Import del inventario unificado** (2026-10-26):
  `scripts/import_inventory.py` (ver README). Probado en `dromax_ai_inventario`
  (copia de `dromax_ai_multi_base`): 5008 productos, 4304 con código de
  barras, 12.619 ajustes de inventario en 7 sucursales (PROMEDICA no tiene
  bloque), costos por sucursal. Segunda pasada: 0 cambios. Incidencias del
  Excel: 704 productos sin código, 281 con códigos adicionales (Odoo admite uno
  por producto), 35 cantidades negativas (se cargan en 0), 34 códigos no
  válidos, 15 filas con varias referencias, 2 códigos repetidos. SOLIDARIA =
  FARMARCIA FSC (confirmado por el usuario). Impuestos y unidades, respondidos
  por Daniel el 2026-10-29 (ver Decisiones): se importa sin impuestos y todo en
  Unidades. Sigue pendiente el precio de venta por sucursal (hoy uno solo, la
  mediana).

- **Vistas de rotación, stock y pendiente** (versión `17.0.0.7.0`, 88 tests,
  2026-10-26): `ai_v_rotacion`, `ai_v_stock` y `ai_v_pendiente` (ver
  README), menú *Compras IA → Análisis*, regla por empresa en las pantallas,
  catálogo y prompt del agente (usa la demanda depurada ya calculada). El
  módulo ahora depende de `purchase_stock` y `pos_sale`. Decisiones propias,
  a validar con compras: semanas completas en hora local; atípicas por ambos
  lados (también las semanas bajas, p. ej. por quiebre de stock); MAD mínimo
  de 1 unidad, así que la venta esporádica de una sola semana queda con
  demanda depurada 0; pendiente por movimientos de stock y, sin ellos, pedido
  − recibido.

- **Historial simulado sobre el inventario real** (2026-10-26):
  `scripts/simulate_history.py` (ver README) en `dromax_ai_inventario`, con
  la oferta FTP copiada de `dromax_ai_multi` (15.305 filas; 2349 de los 3642
  productos con stock tienen oferta). 26 semanas: 1274 tickets de TPV
  (364.278 líneas), 8 pedidos de venta, 550 compras (21 pendientes, 4
  solicitudes). Resultado: 11.905 filas de rotación, 1166 de pendientes y
  ~1100 faltantes a una semana de cobertura, 772 con oferta. Copia sin
  simular: `dromax_ai_inventario_base`. En `dromax_ai_multi` los productos
  simulados no coinciden con la oferta: para propuestas, usar esta base.

- **Vista de faltantes** (versión `17.0.0.8.0`, 94 tests, 2026-10-27):
  `ai_v_faltantes` (ver README), menú *Compras IA → Análisis → Faltantes*,
  catálogo y prompt (el agente parte de ahí para armar compras). Cantidad
  sugerida = ⌈demanda depurada × cobertura − stock − pendiente⌉; stock
  negativo = 0; droguería sugerida con precio coherente con el costo,
  stock suficiente y menor precio; aviso `revisar_presentacion` si el
  precio pasa del triple del costo (dispensadores con el mismo código);
  excedente en otras farmacias por encima de 4 semanas de su demanda
  (ajuste nuevo `excess_weeks`), informativo. En `dromax_ai_inventario`:
  1412 faltantes, 1004 con droguería, 41 a revisar por presentación, ~2 s.

- **Propuesta de compra** (versión `17.0.0.9.0`, 102 tests, 2026-10-27):
  `ai.purchase.proposal` y sus líneas (ver README), menú *Compras IA →
  Propuestas*, tarea programada del lunes 03:00 y botón *Calcular
  propuesta*. **Una propuesta semanal agrupada por laboratorio**, no una por
  laboratorio como decía el diseño (decidido con el usuario: ~190
  laboratorios con mediana de 3 líneas). Laboratorio: campo configurable →
  corchetes del nombre → droguería. Cantidad y droguería sugeridas fijas;
  métrica de ajuste del comprador. En `dromax_ai_inventario`: 1412 líneas,
  187 laboratorios, ~12 s.

- **Botón de borradores** (versión `17.0.0.10.0`, 106 tests, 2026-10-27):
  *Generar borradores* en la propuesta y como acción sobre líneas
  seleccionadas: una orden en borrador por farmacia y droguería, por partes
  (suma al borrador de la misma propuesta, nunca a uno hecho a mano), sin las
  líneas a revisar por presentación que el comprador no tocó. Precio = precio
  final de la oferta; impuestos y descripción como Odoo, pero sin su onchange
  por línea (43 s → 12 s para 963 líneas en `dromax_ai_inventario`).
  Corregido de paso: los totales de la propuesta (líneas, ajustadas, montos)
  se calculaban solo con las farmacias seleccionadas por el usuario que
  ajustaba una línea (caché de `line_ids` filtrado por la regla); ahora
  cuentan todas las líneas, y la propuesta solo se cierra cuando no queda nada
  por pedir en ninguna farmacia.

- **Propuesta en el chat** (versión `17.0.0.11.0`, 116 tests, 2026-10-29):
  herramientas `get_proposal` y `update_proposal_lines` (ver README; el diseño
  decía `update_proposal_line`, en plural para ajustar varias líneas en una
  llamada), botones *Generar borradores* y *Abrir propuesta* bajo la
  respuesta, y *Arma la compra de esta semana* que parte de la propuesta
  guardada (la calcula si no hay una de esta semana). El agente solo ajusta
  lo que pide el comprador y lo deja anotado en el motivo y en el historial de
  la propuesta. Probado en vivo con Claude Opus en `dromax_ai_inventario`
  (con rollback): el resumen pasó de 7 consultas, 0,95 USD y 92 s a una
  consulta, 0,17 USD y 29 s al incluir en el resumen las líneas de mayor monto
  a revisar y los traslados posibles; «del DIGLET en DROMAX pide 3» ubicó la
  línea y la cambió (0,20 USD, 19 s).

- **Módulo FTP 1.3** (commit `928509d` del repo local, 2026-10-29): la oferta
  (`supplier.product.staging`) es de solo lectura para los usuarios internos;
  la cambian las cargas (con `sudo()`) y los administradores. Fuera el código
  muerto: `ftp.connector` (no se cargaba), el asistente vacío de traslados
  (`interbranch.transfer.wizard`, con migración que borra su tabla), el
  controlador y las vistas de plantilla. 27 tests en el módulo FTP; 143 entre
  los dos módulos.

## Decisiones

- Módulo nuevo, separado del antiguo `ai_data_chat`.
- **Ventas = TPV + pedidos de venta.** El ~90 % es TPV; los pedidos de venta
  son de clientes especiales (alcaldía, personal) y pueden distorsionar la
  demanda.
- **Cada farmacia es una sucursal** (`res.company`), no un almacén. Se agrupa
  por `company_id`.
- Compra semanal: las órdenes se hacen lunes y martes.
- **La compra no es estricta por farmacia** (2026-10-27): un comprador de
  cualquier farmacia puede comprar para otra. Por eso el chat no se filtra
  por farmacia y los faltantes informan el excedente de las demás.
- `res_partner` queda fuera de la lista blanca SQL (mezcla clientes).
- Acceso al asistente = personal de compras + administradores, sin casilla
  aparte (decisión del 2026-10-23; antes era una casilla en el usuario).
- Credenciales FTP en la base, cifradas, restringidas a administradores. La
  llave va por ahora en la variable de entorno `FTP_SECRET_KEY` (acordado
  2026-10-23; se puede cambiar a `odoo.conf` u otra fuente si hace falta).
  En local está en `env/bin/activate`.
- En los inventarios de las droguerías, el costo que cuenta es el **precio
  final** (ya trae todos los descuentos).
- **Todo se trabaja por unidad** (Daniel, 2026-10-29): el paréntesis de
  unidades como «UNIDAD (10 AMP)» solo avisa a quien administra el sistema que
  existe una caja de 10; inventario, ventas y compras van por unidad. El
  import los deja en Unidades y el módulo no redondea a cajas.
- **IVA 16 %** (Daniel, 2026-10-29), pero **~90 % de los productos son
  exentos** (sector farmacéutico): el 16 % solo va en los gravados. La base
  trae un 22 % por defecto que no corresponde. Los impuestos se configuran en
  producción, no en este repo: el import carga los productos sin impuestos y
  los borradores toman los impuestos de compra del producto.
- La 9.ª columna de VITALCLINIC (siempre 0 o vacía) **se ignora**.

## Formatos de los inventarios FTP

Todos los archivos usan `;` como separador y no traen cabecera. Las columnas
1 a 8 son: código, código de barras, descripción, fecha del lote, precio,
descuento, precio final y stock.

| Droguería | Codificación | Columnas | Descuento | Extras |
|---|---|---|---|---|
| INTERCONTINENTAL | Latin-1 | 9 | `15+0+0+7` | 9.ª = laboratorio; códigos de barras de 14 dígitos (GTIN-14) |
| INSUAMINCA | UTF-8 | 10 | `15+5` | 9.ª = laboratorio; 10.ª = `INDEXADO` / `MONTO FACTURA` |
| VITALCLINIC | UTF-8 con NUL | 11 | `10.0000` | 9.ª siempre 0 o vacía (se ignora); 10.ª y 11.ª = descuentos extra en cascada con la 6.ª; trae «„» en lugar de «Ñ» en el propio archivo |

En los tres casos, precio final = precio × Π(1 − descuento). En VITALCLINIC
entran en el producto los descuentos de las columnas 6, 10 y 11.

Mapa de columnas configurado en `DROMAX_AI_DEV` (pestaña *Configuración FTP*;
`BASE` = `codigo,barras,descripcion,lote,precio,descuento,precio_final,stock`):

| Droguería | Codificación | Columnas |
|---|---|---|
| INTERCONTINENTAL (70) | Latin-1 | `BASE,laboratorio` |
| INSUAMINCA (71) | UTF-8 | `BASE,laboratorio,extra` |
| VITALCLINIC (69) | UTF-8 | `BASE,extra,descuento,descuento` |

Comprobado con los archivos descargados: cero líneas descartadas y precio ×
descuentos encadenados = precio final en todas las filas.

## Siguiente

1. **Validar el chat con el comprador**: probar con el encargado de compras el
   flujo completo (resumen → ajustes en el chat → borradores) y medir costo y
   tiempo con uso real.
2. **Guía de instalación** para el servidor de Daniel: rol `dromax_ai_ro`,
   `FTP_SECRET_KEY`, OpenRouter, crons e IVA en los productos gravados.
3. **Con la base multisucursal de Daniel:** validar y calibrar las vistas y
   las propuestas por sucursal con ventas reales, conectar el campo
   laboratorio y revisar el reporte de códigos de barras con el catálogo real.

## Preguntas abiertas para Daniel

- Respuestas sugeridas a estas preguntas, para que Daniel solo diga cuál no
  le sirve: `~/odoo-dev/respuestas_sugeridas_daniel.txt` (fuera del repo).
- Ventana de semanas de historial para la rotación (provisional: 8).
- Campo laboratorio: ¿many2one o many2many? ¿Coincide con el de los archivos
  de las droguerías?
- 10.ª columna de INSUAMINCA (`INDEXADO` en 3675 productos / `MONTO FACTURA`
  en 1352): creemos que es la condición de pago (deuda ajustada a la tasa del
  día de pago o monto en bolívares de la factura). Hoy se guarda como dato
  extra y no cambia el precio ni la droguería elegida; si INDEXADO encarece,
  habría que tenerlo en cuenta. Lo puede confirmar el vendedor de INSUAMINCA o
  el encargado de compras.
- ¿El precio final de las droguerías viene con o sin IVA? Daniel no lo sabe.
  Solo afecta a los productos gravados (~10 %): los borradores suman el IVA de
  compra del producto sobre ese precio, así que si ya lo trae se cobraría dos
  veces, y si una droguería lo incluye y otra no, la comparación de precios de
  esos productos no es pareja. Se resuelve comparando una factura real de cada
  droguería con su archivo FTP del mismo día (encargado de compras).
- Datos FTP de COBECA (vacíos en la planilla) y de las demás droguerías.
- Frecuencia deseada del cron de inventarios.

## Pendiente fuera del repo

- Entregar a Daniel el `.zip` del módulo FTP cuando lo pida (ver CLAUDE.md).
- Usuario de prueba sin permisos de administración en `DROMAX_AI_DEV`:
  `comprador.prueba` (id 83; Compras, Inventario y Asistente de Compras IA).
- Usuario de prueba sin compras ni administración: `empleado.prueba` (id 207;
  Inventario, Ventas y contactos). No debe ver Compras IA ni la pestaña FTP.
- Contactos de droguerías en `DROMAX_AI_DEV`: VITALCLINIC (69),
  INTERCONTINENTAL (70) e INSUAMINCA (71).
