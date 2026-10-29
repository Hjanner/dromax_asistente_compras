# Instalación en el servidor de Dromax

Guía para instalar el **Asistente de Compras IA** (`dromax_asistente_compras`)
y el módulo de inventarios FTP de las droguerías (`advanced_interbranch_transfer`)
en la base de producción. Se hace una sola vez. Al final hay una lista para
comprobar que todo funciona y cómo actualizar después.

Hace falta acceso de administrador a Odoo, a la consola del servidor y a
Postgres como superusuario.

## 0. Antes de empezar

- **Copia de seguridad** de la base y del filestore.
- **Odoo 17 Enterprise** con estos módulos disponibles: Compras, Inventario,
  Ventas y Punto de venta (el asistente instala `purchase_stock` y `pos_sale`).
- **Salida a internet** desde el servidor:
  - HTTPS a `openrouter.ai` (el modelo de IA);
  - FTP a cada droguería, con el puerto que use (algunas no usan el 21,
    p. ej. `58021`).
- **Postgres** con la extensión `pg_trgm` (la usa el reporte de códigos de
  barras). Comprobarlo conectado a la base con `\dx`; si no aparece, crearla
  como superusuario: `CREATE EXTENSION pg_trgm;`.
- La librería de Python `cryptography` (ya es requisito de Odoo 17).

## 1. Copiar los módulos

1. `advanced_interbranch_transfer`: descomprimir el `.zip` en la carpeta de
   addons donde ya está el módulo (por ejemplo `server-addons/`), reemplazando
   la carpeta anterior completa.
2. `dromax_asistente_compras`: clonar el repositorio en una carpeta del
   `addons_path` (la raíz del repo es el módulo):

   ```bash
   cd <carpeta de addons>
   git clone https://github.com/Hjanner/dromax_asistente_compras.git
   ```

3. Comprobar que las dos carpetas están en el `addons_path` de `odoo.conf`.

## 2. Llave de las claves FTP

Las claves FTP de las droguerías se guardan cifradas. La llave va en la
variable de entorno `FTP_SECRET_KEY` del proceso de Odoo, **no** en la base.
Hay que definirla **antes** de actualizar el módulo FTP, para que la
actualización cifre las claves que estén en texto plano.

1. Generarla (una sola vez):

   ```bash
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. Definirla en el servicio, por ejemplo con systemd:

   ```ini
   # systemctl edit odoo
   [Service]
   Environment=FTP_SECRET_KEY=<llave>
   ```

3. Guardar la llave en un lugar seguro fuera del servidor. Si se pierde, hay
   que volver a escribir las claves FTP en cada droguería.

Más detalles (cómo cambiar la llave sin perder las claves) en el README del
módulo FTP.

## 3. Instalar y actualizar

Reiniciar Odoo y, con el servicio detenido o desde la consola, en este orden:

```bash
# Módulo FTP (si ya estaba instalado; si no, usar -i):
odoo-bin -c <odoo.conf> -d <base> -u advanced_interbranch_transfer --stop-after-init
# Asistente:
odoo-bin -c <odoo.conf> -d <base> -i dromax_asistente_compras --stop-after-init
```

También se puede hacer desde *Aplicaciones* (actualizar la lista de
aplicaciones primero). Después, iniciar Odoo de nuevo.

Qué hace la instalación:

- El módulo FTP pasa a la versión 1.3: cifra las claves FTP, deja la oferta de
  las droguerías de solo lectura para los usuarios internos y quita código que
  no se usaba.
- El asistente crea el menú **Compras IA**, sus vistas de análisis
  (`ai_v_*`) y dos tareas programadas (ver el paso 7).

## 4. Rol de solo lectura de Postgres

El agente consulta la base con un usuario propio de Postgres que solo puede
leer una lista de tablas. Crearlo **después** de instalar el asistente (así
recibe permiso sobre sus vistas), como superusuario y conectado a la base de
Odoo:

```bash
psql -d <base> -v ro_password='<clave segura>' -f dromax_asistente_compras/scripts/create_readonly_role.sql
```

- Decirle al asistente qué usuario usar: en **Ajustes → Técnico → Parámetros
  del sistema** (con el modo desarrollador activado), crear
  `dromax_asistente_compras.ro_user` = `dromax_ai_ro` y
  `dromax_asistente_compras.ro_password` = la clave anterior. También se
  pueden poner en `odoo.conf` (`ai_purchase_ro_user`, `ai_purchase_ro_password`)
  o en las variables de entorno `AI_PURCHASE_RO_USER` y
  `AI_PURCHASE_RO_PASSWORD` del servicio.
- El script se puede repetir sin problema (por ejemplo, para cambiar la clave;
  en ese caso, actualizar también el parámetro).
- El asistente se conecta con este rol al mismo host, puerto y base que Odoo.
  Si Odoo usa el socket local, `pg_hba.conf` debe permitir al usuario
  `dromax_ai_ro` entrar **con clave** (`scram-sha-256` o `md5`), no con `peer`.
  Si se cambia `pg_hba.conf`, recargar Postgres.

## 5. Ajustes del asistente

En **Ajustes → Asistente de Compras IA** (o *Compras IA → Configuración*):

| Ajuste | Valor |
|---|---|
| Llave de OpenRouter | La llave de la cuenta de Dromax. Pulsar **Probar llave**: muestra el consumo y el tope de crédito. |
| Modelos por nivel | Dejar los iniciales: básico `deepseek/deepseek-v4-flash`, medio `google/gemini-3.8-flash`, avanzado `anthropic/claude-opus-5.5`. |
| Semanas de historial | 8 (provisional, a calibrar con el encargado de compras). |
| Semanas de cobertura | 1 (la compra es semanal). |
| Excedente a partir de (semanas) | 4. |
| Campo de laboratorio | El nombre técnico del campo de laboratorio en el producto (el del módulo de laboratorios de Dromax). Vacío: se toma del texto entre corchetes del nombre («… [LETI]») o del que informa la droguería. |

La llave de OpenRouter y la clave del rol quedan en los parámetros del
sistema, solo visibles para administradores; no se guardan en el código.

## 6. Droguerías (inventarios FTP)

En cada droguería (contacto), pestaña **Configuración FTP** (solo la ven los
administradores):

1. Marcar **¿Tiene conexión FTP?** y completar host (con el puerto si no es el
   21, p. ej. `host:58021`), usuario, contraseña y directorio.
2. Formato del archivo: **Archivo** (patrón del nombre), **Codificación**,
   **Separador** y **Columnas**.
   Los que ya se probaron con los archivos reales:

   | Droguería | Codificación | Columnas |
   |---|---|---|
   | INTERCONTINENTAL | Latin-1 | `BASE,laboratorio` |
   | INSUAMINCA | UTF-8 | `BASE,laboratorio,extra` |
   | VITALCLINIC | UTF-8 | `BASE,extra,descuento,descuento` |

   `BASE` = `codigo,barras,descripcion,lote,precio,descuento,precio_final,stock`.
   El separador es `;` en las tres.
3. Pulsar **Probar Conexión FTP** y después **Descargar y Leer Datos**. El
   resultado de la carga queda en la misma pestaña (*Última carga*,
   *Resultado*, *Detalle*), y el botón **En oferta** del contacto
   muestra los productos cargados.

## 7. Tareas programadas

En **Ajustes → Técnico → Acciones planificadas**:

- **Inventarios FTP de droguerías**: se instala **desactivada**. Activarla
  cuando las droguerías carguen bien a mano (paso 6). Corre cada 30 minutos;
  cambiar la frecuencia si se prefiere. Si un archivo llega vacío o con menos
  de la mitad de los productos, se conserva la oferta anterior.
- **Compras IA: propuesta de compra semanal**: activa, los lunes a las 03:00
  de Caracas (07:00 UTC). Calcula la propuesta de la semana a partir de los
  faltantes; no llama a la IA ni crea órdenes. Para cambiar el día o la hora,
  editar la fecha de la próxima ejecución de la tarea.

## 8. Usuarios

- El asistente lo ven automáticamente los usuarios con **Compras / Usuario**
  y los administradores; no hay que marcar nada más.
- Generar borradores de órdenes de compra requiere **Compras / Usuario**.
- Como cualquier comprador puede comprar para cualquier farmacia, darles
  **acceso a todas las farmacias** (empresas permitidas en su ficha). Cada uno
  solo ve y pide las líneas de las farmacias a las que tiene acceso.
- Zona horaria de los usuarios y de las farmacias: `America/Caracas` (las
  semanas de venta se cuentan en hora local).
- La configuración y el consumo de IA (*Compras IA → Configuración → Consumo
  de IA*) solo los ven los administradores.

## 9. Impuestos

Dromax trabaja con **IVA 16 %**, pero cerca del 90 % de los productos (sector
farmacéutico) son **exentos**:

- Poner el IVA de compra del 16 % solo en los productos gravados (impuestos de
  proveedor del producto); los exentos, sin impuesto. Los borradores toman el
  impuesto de compra de cada producto.
- Revisar los impuestos por defecto de cada farmacia (*Contabilidad →
  Ajustes*): la base trae un 22 % que no corresponde y se aplicaría a los
  productos nuevos.
- **Pendiente:** confirmar si el precio final de los archivos de las
  droguerías viene con o sin IVA (comparando una factura con el archivo del
  mismo día). Si viene con IVA, los borradores de los productos gravados lo
  sumarían dos veces.

## 10. Comprobar que funciona

| Dónde | Qué debería verse |
|---|---|
| *Inventario → Productos → Oferta de droguerías* | Las filas de cada droguería. Un usuario sin permisos de administración puede verlas pero no editarlas ni borrarlas. |
| *Compras IA → Oferta por producto* | Productos propios con oferta, cruzados por código de barras. Si sale vacío, revisar los códigos de barras de los productos. |
| *Compras IA → Códigos de barras* → **Actualizar reporte** | Las incoherencias entre los códigos propios y los de las droguerías, para corregir. |
| *Compras IA → Análisis → Faltantes* | Productos por pedir con su droguería sugerida. |
| *Compras IA → Propuestas* → **Calcular propuesta** | La propuesta de la semana, agrupada por laboratorio. |
| *Compras IA → Chat* → **Arma la compra de esta semana** | El resumen de la propuesta, con los botones **Generar borradores** y **Abrir propuesta** debajo. |
| **Generar borradores** sobre las líneas de un laboratorio | Una solicitud de presupuesto en borrador por farmacia y droguería, en *Compras*. |
| *Compras IA → Configuración → Consumo de IA* | Una línea por cada llamada al modelo, con su costo. |

## 11. Actualizar más adelante

- **Asistente**: `git pull` en la carpeta del módulo y actualizarlo
  (`-u dromax_asistente_compras`).
- **Módulo FTP**: reemplazar la carpeta por la del `.zip` nuevo y actualizarlo
  (`-u advanced_interbranch_transfer`).
- Si cambia la lista de tablas que puede leer el agente, volver a ejecutar el
  script del rol (paso 4).

## Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| «No se pudo descifrar la clave FTP» | La llave `FTP_SECRET_KEY` no es la que se usó al guardar la clave, o falta. Revisar la variable del servicio o volver a escribir la clave en el contacto. |
| El chat responde que no puede consultar la base | Faltan los parámetros `ro_user` / `ro_password`, la clave no coincide, o `pg_hba.conf` no permite al rol entrar con clave (paso 4). |
| «El Asistente de Compras IA es solo para el personal de compras…» | El usuario no tiene *Compras / Usuario*. |
| «Para generar órdenes de compra hace falta el permiso de Compras» | Igual que el anterior: falta *Compras / Usuario*. |
| La propuesta no trae droguería en muchos productos | Sus códigos de barras no coinciden con los de las droguerías: revisar el reporte de *Códigos de barras*. |
| «Probar llave» falla | Llave de OpenRouter errónea o sin crédito, o el servidor no tiene salida a `openrouter.ai`. |
