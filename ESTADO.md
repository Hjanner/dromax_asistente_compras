# Estado del proyecto

Última actualización: 2026-10-21. Qué está hecho, qué decidimos y qué falta.
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

## Decisiones

- Módulo nuevo, separado del antiguo `ai_data_chat`.
- **Ventas = TPV + pedidos de venta.** El ~90 % es TPV; los pedidos de venta
  son de clientes especiales (alcaldía, personal) y pueden distorsionar la
  demanda.
- **Cada farmacia es una sucursal** (`res.company`), no un almacén. Se agrupa
  por `company_id`.
- Compra semanal: las órdenes se hacen lunes y martes.
- `res_partner` queda fuera de la lista blanca SQL (mezcla clientes).
- Credenciales FTP en la base (no en `.env`), restringidas a administradores.
  Próximo paso: cifrarlas con una llave guardada fuera de la base.
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

## Siguiente

1. **Carga de inventarios robusta** (módulo FTP):
   - cron automático (el diseño habla de cada 30 min);
   - cifrar las claves FTP;
   - guardar el laboratorio y el descuento original en texto;
   - formato configurable por droguería (codificación y mapa de columnas);
   - no borrar el inventario anterior si el archivo nuevo viene vacío o
     muy recortado.
2. **Inventarios en el agente:**
   - normalizar los códigos de barras (GTIN-14 → EAN-13, ceros a la
     izquierda);
   - vista `ai_v_oferta` (oferta de droguerías × productos) y añadirla a la
     lista blanca y al script del rol;
   - reporte `ai.barcode.mismatch` (`pg_trgm` 1.6 disponible).
3. **Con la base multisucursal de Daniel:**
   - vistas `ai_v_rotacion`, `ai_v_stock`, `ai_v_pendiente` y `ai_v_faltantes`
     por sucursal;
   - `ai.purchase.proposal` y sus líneas, cron del lunes, herramientas
     `get_proposal` / `update_proposal_line` y botón para crear la solicitud de
     presupuesto.

## Preguntas abiertas para Daniel

- Ventana de semanas de historial para la rotación (provisional: 8).
- Campo laboratorio: ¿many2one o many2many? ¿Coincide con el de los archivos
  de las droguerías?
- Significado de la 10.ª columna de INSUAMINCA (`INDEXADO` / `MONTO FACTURA`)
  y de las columnas 9 a 11 de VITALCLINIC.
- Datos FTP de COBECA (vacíos en la planilla) y de las demás droguerías.
- Frecuencia deseada del cron de inventarios.

## Pendiente fuera del repo

- En la base local hay una acción manual (`action=706`) con la lista de
  `supplier.product.staging`. Hay que reemplazarla por una vista del módulo.
- Contactos de droguerías en `DROMAX_AI_DEV`: VITALCLINIC (69),
  INTERCONTINENTAL (70) e INSUAMINCA (71).
