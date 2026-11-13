# Estado del proyecto

Última actualización: 2026-11-13 (asistente `17.0.0.18.3`, 159 tests; módulo
FTP 1.5 con 38). Qué está hecho, qué decidimos y qué falta.
El README explica cómo funciona lo que ya existe.

## Hecho

- **Fase 1 (base del asistente)** — versión `17.0.0.4.0`, 44 tests en verde.
  - Validador SQL y ejecución con el rol de solo lectura `dromax_ai_ro`.
  - Cliente de OpenRouter con tres niveles de modelo y registro de consumo
    (`ai.usage.log`).
  - Ajustes: llave, modelos por nivel, parámetros del cálculo de faltantes y
    límites SQL.
- **Chat del agente** (menú *Agente IA → Chat*) con preguntas predefinidas,
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
  README), menú *Agente IA → Análisis*, regla por empresa en las pantallas,
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
  `ai_v_faltantes` (ver README), menú *Agente IA → Análisis → Faltantes*,
  catálogo y prompt (el agente parte de ahí para armar compras). Cantidad
  sugerida = ⌈demanda depurada × cobertura − stock − pendiente⌉; stock
  negativo = 0; droguería sugerida con precio coherente con el costo,
  stock suficiente y menor precio; aviso `revisar_presentacion` si el
  precio pasa del triple del costo (dispensadores con el mismo código);
  excedente en otras farmacias por encima de 4 semanas de su demanda
  (ajuste nuevo `excess_weeks`), informativo. En `dromax_ai_inventario`:
  1412 faltantes, 1004 con droguería, 41 a revisar por presentación, ~2 s.

- **Propuesta de compra** (versión `17.0.0.9.0`, 102 tests, 2026-10-27):
  `ai.purchase.proposal` y sus líneas (ver README), menú *Agente IA →
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

- **Dos preguntas predefinidas nuevas** (versión `17.0.0.12.0`, 2026-11-02):
  *Revisa un laboratorio* (pide el laboratorio en el cuadro de texto; si el
  nombre no coincide, sugiere los parecidos) y *¿Qué conviene trasladar?*
  (filtro `se_cubre_con_traslado` de `get_proposal`, con lo que se deja de
  comprar por farmacia de origen). En vivo sobre `dromax_ai_inventario`
  (rollback): Calox, 33 líneas, 0,31 USD y 67 s con Opus; traslados, 690
  líneas y 12.183 evitables, 0,04 USD y 29 s con Gemini. La primera versión
  de traslados agrupaba mal por origen (el modelo leía el texto «FARMAX (83),
  …»); ahora la herramienta da el origen ya calculado.
  El botón de borradores bajo una respuesta que trató un solo laboratorio
  pasa a ser *Generar borradores de CALOX* y pide solo ese laboratorio (antes
  pedía toda la propuesta); si trató varios o el resumen, *Generar todos los
  borradores*, y si no encontró líneas, no hay botones. En vivo: Calox, 8
  borradores con 28 líneas, todas de CALOX (0,47 USD, 92 s). 120 tests.
- **Botones tras «¿Qué conviene trasladar?»** (versión `17.0.0.13.0`,
  2026-11-03): sin botón de borradores (pedía todo, también lo que se puede
  trasladar), y *Ver traslados en la propuesta* abre las líneas con el filtro
  nuevo *Se cubre con traslado* (campo guardado `se_cubre_con_traslado`, que
  también usa `get_proposal`) y la columna de farmacias con excedente. En
  vivo: 690 líneas, las mismas en el chat y en la lista (0,03 USD, 19 s). 122
  tests. Poner en 0 de una vez lo que se cubre con traslado queda fuera hasta
  confirmarlo con el encargado de compras.
- **Traslados entre sucursales** (módulo FTP 1.4 y 1.5 de Daniel, commits
  `12bc787` a `f885e2d` del repo local, 2026-11-04): incorporados sin los
  archivos que sigue trayendo su `.zip` y el manifiesto no carga
  (controlador, `ftp_connector`, asistente, plantillas, `models.py`, etc.).
  Lo demás es idéntico a su 1.5. Flujo en el README del módulo FTP: borrador
  de *Traslado interno* → *Generar Traslados Definitivos* → cadena por la
  *Zona de Tránsito Inter-Empresas (Auto)*, con un traslado por paso si el
  almacén entrega o recibe en 2 o 3 pasos; costo del origen, lotes copiados al
  destino. La 1.5 solo agrega, en cada tarjeta del *Resumen de inventario*,
  una etiqueta con «Empresa - Almacén». 29 tests en el módulo FTP (151 entre
  los dos). Probado en vivo en `dromax_ai_inventario` con rollback: FARMAX →
  LA 45 (27 u. de [6515] LOPERAM, costo 31,05 en ambas valoraciones) y
  DROMAX en 3 pasos → FARMAX en 2 pasos (5 traslados, [2629] TUBO
  RECOLECTOR).
  - Los traslados internos solo aparecen con *Ajustes → Inventario →
    Ubicaciones de almacenamiento*; al activarlo, Odoo solo reactiva el tipo
    de la empresa actual. En `dromax_ai_inventario` quedó activado (guardado)
    para las 8 sucursales.
  - Los códigos de almacén los pone Odoo (`company.name[:5]`): en nuestras
    bases hay 6 almacenes «FARMA», así que el destino se elige mirando la
    columna *Empresa*. En el servidor de Daniel tienen códigos propios (se ven
    en su video «Traslado inter sucursales.mp4», fuera del repo).

- **Error de Daniel al guardar la clave FTP** (2026-11-05): su servidor no
  tiene `FTP_SECRET_KEY` en el entorno del proceso de Odoo. Además le salió
  el traceback completo en lugar del aviso, porque `FtpKeyError` era una
  subclase de `UserError` y el cliente web solo reconoce las de
  `odoo.exceptions`. En el módulo FTP (commit `b1692f7` del repo local):
  - `FtpKeyError` pasa a ser `UserError` tal cual;
  - la pestaña *Configuración FTP* avisa si falta la llave (campo
    `ftp_key_missing`);
  - la llave también se lee de `ftp_secret_key` en `odoo.conf` (más simple
    que la variable de entorno del servicio; si están las dos, manda la
    variable). `INSTALACION.md` ahora indica `odoo.conf`.
  35 tests en el módulo FTP.

- **Servidor de Daniel: prueba en `ODOO_REPO`** (2026-11-05, por SSH). Los
  detalles del servidor (rutas, servicio, accesos) no van en este repo, que es
  público: están en el `CLAUDE.md` del espacio de trabajo.
  - Varias bases en el mismo Odoo: **producción = `ODOO2026`**; `ODOO_REPO`
    es la de prueba (casi sin datos: 13 productos con código, sin ventas TPV
    ni oferta FTP). Las dos tenían los módulos, con el asistente en 0.11.0;
    el módulo FTP ya era nuestro `b1692f7`.
  - Con respaldo previo, el asistente `f955360` (0.13.0) se probó en
    `ODOO_REPO` con una instancia temporal aparte (ver *Probar en otra base
    antes de producción* en `INSTALACION.md`), sin tocar el código que usan
    las demás bases ni reiniciar el servicio. Actualización sin errores.
    Mientras siga así, `ODOO_REPO` solo por la instancia de prueba.
  - Rol `dromax_ai_ro` creado (20 tablas en `ODOO_REPO`), con su línea en
    `pg_hba.conf` solo para `ODOO_REPO` y los parámetros `ro_user` /
    `ro_password` en esa base. Comprobado: lee la lista, rechaza
    `res_partner`, escrituras y otras bases. El chat funciona (probado por
    el usuario).
  - Pendiente de diagnóstico: desde el servidor, *Probar Conexión FTP* de
    INTERCONTINENTAL da «timed out» (desde local conecta con los mismos
    datos). Probar `nc -vz -w 10 <host> 58021` en el servidor (pedir
    confirmación antes: es su FTP real); si no responde, lo más probable es
    que no acepte la IP del servidor y haya que pedirle que la autorice.
  - Lección: el código de un módulo lo comparten todas las bases del
    servidor; reemplazar la carpeta y reiniciar sin actualizar todas las que
    lo tienen rompe las que no se actualizaron (0.13 agrega tres columnas).
    Documentado en `INSTALACION.md`.

- **Reabastecimiento desde la matriz** (versión `17.0.0.14.0`, 2026-11-07,
  pedido de Daniel en sus audios del 5 y 6 de noviembre, versión sencilla para
  su presentación del día 8): vista `ai_v_reabastecimiento`, menú *Agente IA →
  Reabastecimiento* (lista, tabla dinámica y gráfico con el valor por
  farmacia), botón *Generar borradores de traslado* que crea el borrador del
  módulo de traslados (Daniel pidió respetar las reglas de Odoo: el asistente
  solo crea el borrador interno; tránsito, costo, lotes y pasos los hace su
  *Generar Traslados Definitivos*), ajuste *Sucursal matriz* y pregunta *¿A
  quién abastezco desde Dromax?* (medio). Probado en `dromax_ai_inventario`
  con rollback: 305 líneas; borradores de DROMAX a FARMAX y LA 45 y cadena de 5
  pasos (Dromax en 3, Farmax recibe en 2) con el costo de Dromax. Chat en vivo
  (Gemini, rollback): la primera versión sumó mal el valor total (6533 en vez
  de 6243); la pregunta ahora pide los totales con ROLLUP en el propio SQL y
  copiarlos: 2 consultas, 0,02 USD, 21 s, totales exactos. 129 tests en el
  asistente (164 con el módulo FTP).
  Para una segunda etapa (en la propuesta a Daniel): período por consulta,
  umbral como % de la venta semanal, traslados entre farmacias, solicitud de
  la farmacia y validación de Dromax, y ajustar el plan desde el chat.

- **Reenfoque: toda compra pasa por Dromax** (versión `17.0.0.15.0`,
  2026-11-09): `ai_v_faltantes` pasa a ser la *Necesidad de compra* de la
  matriz para toda la red (una fila por producto: Σ faltas de las farmacias +
  venta propia − stock libre − pendiente, con `detalle_farmacias`); la
  propuesta queda por producto y sus borradores, una orden de la matriz por
  droguería. Fuera todo lo de traslados entre farmacias: excedente en otras
  farmacias, `se_cubre_con_traslado`, la pregunta *¿Qué conviene
  trasladar?*, el botón *Ver traslados en la propuesta* y el ajuste
  *Excedente a partir de*. Compra y reabastecimiento comparten el cálculo
  (`services/network_sql.py`). En `dromax_ai_inventario`: 708 productos a
  comprar (antes 1412 líneas por producto y farmacia), cuadra con el
  reabastecimiento (p. ej. MELOXICAM: a MANAL le faltan 31, Dromax manda 13 y
  compra 18). Propuesta en ~3 s y borradores de las 489 líneas por pedir (3
  órdenes de DROMAX) en ~5 s. Las pantallas de la matriz (necesidad de compra
  y líneas de la propuesta) se ven con la matriz marcada en el selector de
  empresas. 162 tests entre los dos módulos (127 del asistente).

- **Versión de Daniel del módulo FTP y comparador agrupado** (2026-11-09,
  tras la reunión con el supervisor):
  - Módulo FTP: su versión del servidor (07:11) incorporada al repo local
    (`4e3cdb7`, «de Daniel»: moneda del archivo con conversión a USD,
    descuento de cabecera, FTPS con respaldo FTP, archivos de ancho fijo de
    NENA). Arreglo nuestro (`a7be8c8`): un valor sin dígitos («N/D») vuelve a
    descartar la línea en vez de cargarla con precio 0; tests al día (38).
    Pendiente de comentarle: el botón *Descargar* ahora lanza el error y Odoo
    deshace la transacción, así que la carga fallida no queda en *Última
    carga* (la tarea programada sí la guarda).
  - Asistente `17.0.0.16.0`: *Comparador de precios* (`ai_v_comparador`, una
    fila por producto con visor por droguería) y oferta más estricta (lotes
    vencidos y códigos sin dígito de control válido fuera de la comparación).
    Además (pedido del usuario): clic en un producto abre una ventana con
    el precio de cada droguería, y *Añadir a borrador de compra* (productos
    marcados o desde esa ventana) los agrega a la orden en borrador de la
    matriz con cada droguería, sumando en varias búsquedas. 136 tests en el
    asistente.
  - Asistente `17.0.0.16.1` (pedido del usuario, 2026-11-10): el pedido desde
    el comparador no pasa del stock de la oferta de la droguería (contando lo
    que ya está en su borrador; se revisa otra vez al confirmar) y el aviso
    final trae el botón *Ver en Compras*, que abre esas órdenes en la misma
    pestaña. Antes los enlaces del aviso no se veían: `display_notification`
    solo los pone donde el mensaje tiene `%s`. 137 tests.
  - Revisión de precios contra el portal de compras de las droguerías
    (2026-11-10, solo lectura): con los archivos FTP del mismo día, el precio
    final cuadra al ±0,6 % en ~135 casos de las cinco droguerías. La cascada y
    el descuento de cabecera están bien (es el «descuento comercial» del
    portal: VITALCLINIC y ZAKIPHARMA 12 %; INSUAMINCA e INTERCONTINENTAL traen
    su 15 % en el archivo). Lo que se veía como error de descuentos era oferta
    de un día antes: las promociones cambian a diario. Hallazgos: INSUAMINCA
    publica el precio **con IVA** en los productos gravados (las demás, sin
    IVA; su archivo no tiene columna de IVA; sus facturas en el FTP sí traen
    el IVA por línea, pero cubren pocos productos); las droguerías en Bs se
    convierten con la última tasa cargada en Odoo (hay que cargarla a diario).
  - Asistente `17.0.0.17.0` (pedido del usuario, 2026-11-10): **punto de
    pedido y margen de seguridad** en Ajustes (50 % y 20 %, provisionales).
    Una farmacia entra en el reabastecimiento solo cuando lo que tiene (stock +
    pendiente + en camino) baja al punto (un % de la venta de las semanas de
    cobertura) y se repone hasta esa venta más el margen. La matriz compra
    solo si, después de mandar a las farmacias que llegaron a su punto, le
    queda su propio punto o menos, y entonces repone a todas las que están
    debajo del objetivo. En `dromax_ai_inventario`: traslados de 272 a 111
    líneas (las de 1–2 unidades, de 145 a 46) y compra de 709 a 371 productos
    (de 1–2 unidades, de 381 a 183: lo que queda es venta baja en la sucursal
    que lo necesita). Con 100 % y 0 % se vuelve al cálculo anterior. 143 tests.
  - Asistente `17.0.0.17.1` (orden del supervisor, 2026-11-11): en la
    interfaz el módulo pasa a llamarse **Agente IA** (antes «Compras IA» y
    «Asistente de Compras IA»): menú, app de Ajustes, grupo, tarea programada,
    título del chat y mensajes. El nombre técnico `dromax_asistente_compras`
    no cambia.
  - Asistente `17.0.0.18.0` (pedido del usuario, 2026-11-11):
    **automatización** de los borradores (ver README, *Automatización*). El
    lunes, la tarea de la propuesta además crea los borradores de compra por
    droguería (cancela los de semanas anteriores sin confirmar y deja fuera
    lo que ya está en otras solicitudes); cada día a las 06:00, una tarea
    nueva crea los borradores de traslado. Cada una le deja una actividad a
    su responsable (Ajustes) y tiene un botón *Ejecutar ahora*. Las dos
    vienen apagadas. 148 tests.
  - Asistente `17.0.0.18.1` (pedido del usuario, 2026-11-11), para que el
    personal de compras tenga menos que ver:
    - sin la vista de gráfico en *Reabastecimiento desde la matriz* (queda
      lista y tabla dinámica);
    - la propuesta muestra solo producto, falta por farmacia, cantidad final,
      droguería, precio, monto y «revisar» (lo demás, en el menú de columnas);
    - *Acciones → Cancelar* en la lista de operaciones de inventario: cancela
      varias a la vez tras un diálogo (las hechas o canceladas no se tocan).
    Descartado: que la tarea diaria deje los traslados ya en definitivo.
    Pendientes de validar con un compañero: menú reducido para compras (grupo
    avanzado para análisis, chat y comparador) y botón «Confirmar órdenes» en
    la propuesta con la lista por droguería para el vendedor.
  - Asistente `17.0.0.18.2` (pedido del usuario, 2026-11-12, por el
    ketorolaco [1225]: INTERCONTINENTAL vende la ampolla suelta a 1,44 y las
    otras la caja de 10 a 9,05 y 10,33, con costo 8,76; se sugería la ampolla
    sin marcarla):
    - *Revisar presentación* también marca los precios de menos de un tercio
      del costo (antes solo los de más del triple) y, sin costo, las ofertas
      que difieren más del triple entre sí; la sugerida prefiere las
      coherentes (en el ejemplo, INSUAMINCA);
    - columna *Stock en la droguería* en la propuesta (en rojo si la cantidad
      final la pasa);
    - la cantidad que pone el comprador no puede pasar de ese stock (se ajusta
      con aviso en pantalla y se rechaza al guardar);
    - al cambiar la droguería de una línea, precio, stock y alertas se ven sin
      guardar.
  - Asistente `17.0.0.18.3` (pedido del usuario, 2026-11-13): **menú corto**.
    El personal de compras ve solo *Propuestas*, *Reabastecimiento* y *Chat*,
    y al entrar a *Agente IA* se abre *Propuestas* (antes, el Chat). Solo para
    administradores (`base.group_system`, menú y acción): *Proveedores*
    (*Oferta de proveedores*, antes «Comparador de precios»; *Oferta completa
    (todos los lotes)*, antes «Oferta por droguería»; *Códigos de barras*),
    *Análisis* y *Configuración*. Los permisos sobre los modelos no cambian:
    el chat y la propuesta siguen leyendo esas vistas. Los borradores del
    comparador llevan ahora el origen «Oferta de proveedores». En la
    interfaz, las droguerías se empiezan a llamar «proveedores» (por ahora,
    solo en el menú y estas pantallas). Lección: al actualizar, `groups` de
    un `menuitem` solo **agrega** grupos; para quitar el de compras a los
    menús que ya lo tenían hace falta `-grupo` en el atributo.
  - Respuestas de Daniel (reunión): códigos de barras y ventas de `ODOO2026`
    los resuelve él; no restringir su módulo de traslados (se permiten
    traslados manuales entre farmacias); historial de 8 semanas; la
    instalación de la 0.15.0 queda para después. Compra al laboratorio o a
    droguerías: sin respuesta; en la base `DROMAX` (la operativa) hay 3068
    compras y 3472 proveedores por producto: ~2200 productos se compran solo
    a laboratorios o representantes, ~160 solo a droguerías y ~400 por las
    dos vías. Propuesta: usar el proveedor principal del producto
    (`product.supplierinfo`) para separar la compra directa.

## Decisiones

- Módulo nuevo, separado del antiguo `ai_data_chat`.
- **Ventas = TPV + pedidos de venta.** El ~90 % es TPV; los pedidos de venta
  son de clientes especiales (alcaldía, personal) y pueden distorsionar la
  demanda.
- **Cada farmacia es una sucursal** (`res.company`), no un almacén. Se agrupa
  por `company_id`.
- Compra semanal: las órdenes se hacen lunes y martes.
- Historial de **8 semanas** para la rotación (confirmado por Daniel el
  2026-11-09).
- **Punto de pedido y margen de seguridad** (propuesta del usuario,
  2026-11-10): no se mueve ni se compra por una o dos unidades debajo de la
  venta, y se repone con margen para semanas altas. Valores iniciales 50 % y
  20 %, a calibrar con el comprador.
- **Toda compra pasa por Dromax** (2026-11-09, reemplaza la decisión del
  2026-10-27 de que cualquier farmacia podía comprar para otra): la sucursal
  matriz recibe cada compra y la distribuye a las farmacias con traslados
  cuando lo necesitan. Las farmacias no compran a las droguerías (tampoco los
  ~2000 productos que, según el audio de Daniel del 6/11, compraban a otras
  droguerías) ni se trasladan mercancía entre ellas. La propuesta es una
  línea por producto para toda la red; el módulo de traslados de Daniel no se
  restringe (decisión del usuario: solo el asistente deja de sugerir
  traslados entre farmacias).
- `res_partner` queda fuera de la lista blanca SQL (mezcla clientes).
- Acceso al asistente = personal de compras + administradores, sin casilla
  aparte (decisión del 2026-10-23; antes era una casilla en el usuario).
- Credenciales FTP en la base, cifradas, restringidas a administradores. La
  llave va en `ftp_secret_key` de `odoo.conf` o en la variable de entorno
  `FTP_SECRET_KEY` (la variable, acordada el 2026-10-23; `odoo.conf` desde el
  2026-11-05, tras el error de Daniel). En local está en `env/bin/activate`.
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

1. **Producción (`ODOO2026`)**: tiene el asistente 0.13.0 (instalado el
   2026-11-06 con respaldo completo previo y el usuario de solo lectura
   funcionando). Las versiones 0.14.0 a 0.18.2 (reabastecimiento, toda compra
   por Dromax, comparador, umbrales, nombre «Agente IA», automatizaciones,
   simplificaciones de la propuesta) están **pendientes de instalar**: Daniel
   pidió dejarlo para después, y nada se toca en el servidor sin consultarlo antes
   con él. Al instalar: volver a bajar el módulo FTP del servidor y compararlo
   con el repo local (Daniel lo edita allí) antes de entregar el nuestro,
   actualizar las bases que tienen los módulos (`INSTALACION.md`, paso 11),
   elegir la *Sucursal matriz* en los ajustes y dar a los compradores acceso a
   la matriz. Siguen pendientes en el servidor: que las droguerías carguen (el
   FTP conecta, pero el listado en modo pasivo se agota; detalle en el
   `CLAUDE.md` local), los códigos de barras de los productos y las ventas de
   TPV en la base (los resuelve Daniel), IVA y usuarios.
2. **Validar con el comprador**: el flujo completo (necesidad de compra →
   propuesta o comparador → borradores → reabastecimiento) y el chat, midiendo
   costo y tiempo con uso real.
3. **Compra directa al laboratorio** (sin respuesta de Daniel): en la base
   operativa ~2200 productos se compran solo a laboratorios o representantes.
   Propuesta: si el proveedor principal del producto (`product.supplierinfo`)
   no es una droguería con FTP, la necesidad de compra lo marca como compra
   directa y no lo manda a una droguería. Depende de que Daniel migre los
   proveedores por producto a `ODOO2026`.
4. **Precios de la oferta**: los descuentos de los archivos se aplican bien
   (revisión del 2026-11-10). Falta: el IVA de INSUAMINCA (pedirle que
   publique sin IVA, o descontarlo en los productos gravados cuando Odoo
   tenga bien el impuesto de cada producto: hoy todos tienen 16 %), la
   descarga diaria con aviso si falla y la tasa del día. Los descuentos que no
   vienen en ningún archivo (pronto pago, escalas por volumen de INSUAMINCA)
   no aparecen ni en el portal: si se quieren, se cargan a mano por droguería.
5. **Automatización en producción**: decidir con el comprador y con Daniel
   quiénes son los responsables y activar los dos ajustes; activar también la
   descarga FTP (para que la compra del lunes use la oferta del día). La
   lista para el vendedor (pedida en la reunión) está en el punto 7.
6. **Con los datos de `ODOO2026`:** validar y calibrar la necesidad de compra,
   el reabastecimiento y la propuesta con ventas reales, conectar el campo
   laboratorio y revisar el reporte de códigos de barras con el catálogo real.
7. **Simplificar para el personal de compras** (tiene poca experiencia con
   Odoo). Escritas para validar con un compañero antes de implementar
   (`~/odoo-dev/propuesta_simplificacion_agente.txt`, fuera del repo):
   - ~~menú reducido~~: hecho en la 0.18.3, sin grupo nuevo (compras ve
     Propuestas, Reabastecimiento y Chat; lo demás, solo administradores);
   - botón «Confirmar órdenes» en la propuesta (confirma todos sus
     borradores) y un PDF por droguería para el vendedor (la lista de la
     reunión; falta decidir si va con precios y si separa por farmacia).
   Descartado: dejar los traslados de la tarea diaria ya en definitivo.
8. **Ideas sin pedir aún**: cantidad mínima de pedido para productos de baja
   rotación; reparto proporcional del reabastecimiento cuando la matriz no
   alcanza (hoy va primero la farmacia más urgente: menor cobertura, a
   igualdad la que más vende); aclarar si se factura por sucursal o todo a
   Dromax.

## Preguntas abiertas para Daniel

- Respuestas sugeridas a estas preguntas, para que Daniel solo diga cuál no
  le sirve: `~/odoo-dev/respuestas_sugeridas_daniel.txt` (fuera del repo).
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

## Observaciones para Daniel sobre los traslados

Comentadas al usuario el 2026-11-04; el código no se cambió:

- Al arrancar, el módulo reescribe reglas del núcleo de `stock`
  (`stock_location_comp_rule`, `stock_picking_rule`, `stock_move_rule`,
  `stock_move_line_rule`) para que cada sucursal vea ubicaciones y traslados
  de las demás.
- Usa `sudo()` en casi todo el flujo y `ALTER TABLE … ADD COLUMN IF NOT
  EXISTS` en `_register_hook` en lugar de campos normales.
- En varios pasos, la cadena ignora la sububicación del borrador (sale de las
  existencias principales del origen y llega a las del destino).
- Códigos de almacén repetidos («FARMA») en bases nuevas: conviene
  ponerlos a mano.

## Pendiente fuera del repo

- Entregar a Daniel el `.zip` del módulo FTP (lleva el arreglo `a7be8c8`
  sobre su versión del 2026-11-09) cuando lo pida o al instalar en
  producción; antes, volver a bajar su carpeta y comparar (ver CLAUDE.md).
- Comentarle a Daniel que su botón *Descargar* ahora deshace la carga
  fallida, así que el error no queda en *Última carga*.
- Usuario de prueba sin permisos de administración en `DROMAX_AI_DEV`:
  `comprador.prueba` (id 83; Compras, Inventario y Agente IA).
- Usuario de prueba sin compras ni administración: `empleado.prueba` (id 207;
  Inventario, Ventas y contactos). No debe ver Agente IA ni la pestaña FTP.
- Contactos de droguerías en `DROMAX_AI_DEV`: VITALCLINIC (69),
  INTERCONTINENTAL (70) e INSUAMINCA (71).
