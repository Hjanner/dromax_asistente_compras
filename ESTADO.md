# Estado del proyecto

Última actualización: 2026-10-23. Qué está hecho, qué decidimos y qué falta.
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

## Decisiones

- Módulo nuevo, separado del antiguo `ai_data_chat`.
- **Ventas = TPV + pedidos de venta.** El ~90 % es TPV; los pedidos de venta
  son de clientes especiales (alcaldía, personal) y pueden distorsionar la
  demanda.
- **Cada farmacia es una sucursal** (`res.company`), no un almacén. Se agrupa
  por `company_id`.
- Compra semanal: las órdenes se hacen lunes y martes.
- `res_partner` queda fuera de la lista blanca SQL (mezcla clientes).
- Acceso al asistente = personal de compras + administradores, sin casilla
  aparte (decisión del 2026-10-23; antes era una casilla en el usuario).
- Credenciales FTP en la base, cifradas, restringidas a administradores. La
  llave va por ahora en la variable de entorno `FTP_SECRET_KEY` (acordado
  2026-10-23; se puede cambiar a `odoo.conf` u otra fuente si hace falta).
  En local está en `env/bin/activate`.
- En los inventarios de las droguerías, el costo que cuenta es el **precio
  final** (ya trae todos los descuentos).

## Formatos de los inventarios FTP

Todos los archivos usan `;` como separador y no traen cabecera. Las columnas
1 a 8 son: código, código de barras, descripción, fecha del lote, precio,
descuento, precio final y stock.

| Droguería | Codificación | Columnas | Descuento | Extras |
|---|---|---|---|---|
| INTERCONTINENTAL | Latin-1 | 9 | `15+0+0+7` | 9.ª = laboratorio; códigos de barras de 14 dígitos (GTIN-14) |
| INSUAMINCA | UTF-8 | 10 | `15+5` | 9.ª = laboratorio; 10.ª = `INDEXADO` / `MONTO FACTURA` |
| VITALCLINIC | UTF-8 con NUL | 11 | `10.0000` | 10.ª y 11.ª = descuentos extra en cascada con la 6.ª; trae «„» en lugar de «Ñ» en el propio archivo |

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

1. **Permisos de la oferta** en el módulo FTP: hoy cualquier usuario interno
   puede editar y borrar `supplier.product.staging`; dejarla de solo lectura.
   Quitar el código muerto (`ftp.connector`, wizard vacío, `views/views.xml`).
2. **Flujo de propuestas** con datos de prueba (no hace falta esperar a la
   base de Daniel para construirlo; sí para validarlo): vistas
   `ai_v_rotacion`, `ai_v_stock`, `ai_v_pendiente`, `ai_v_faltantes`,
   `ai.purchase.proposal`, herramientas y botón de solicitud de presupuesto.
3. **Con la base multisucursal de Daniel:** validar y calibrar las vistas y
   las propuestas por sucursal con ventas reales, conectar el campo
   laboratorio y revisar el reporte de códigos de barras con el catálogo real.

## Preguntas abiertas para Daniel

- Ventana de semanas de historial para la rotación (provisional: 8).
- Campo laboratorio: ¿many2one o many2many? ¿Coincide con el de los archivos
  de las droguerías?
- Significado de la 10.ª columna de INSUAMINCA (`INDEXADO` / `MONTO FACTURA`)
  y de las columnas 9 a 11 de VITALCLINIC.
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
