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

   Sin git en el servidor, basta con copiar la carpeta del módulo (por
   ejemplo, un `git archive` del repo) reemplazando la anterior completa.

3. Comprobar que las dos carpetas están en el `addons_path` de `odoo.conf`.

> **Todas las bases del servidor usan el mismo código.** Si un Odoo atiende
> varias bases, al reemplazar la carpeta de un módulo y reiniciar, todas las
> bases que lo tienen instalado pasan a usar el código nuevo, aunque solo se
> haya actualizado (`-u`) una. Cuando una versión agrega campos (p. ej. la
> 0.13 del asistente), las que no se actualizaron dan error en esas pantallas.
> Hay que actualizar a la vez todas las bases que tienen el módulo, o probar
> antes en una sola con una instancia aparte (ver *Probar en otra base antes
> de producción*, al final).

## 2. Llave de las claves FTP

Las claves FTP de las droguerías se guardan cifradas. La llave va en el
servidor, en el archivo de configuración de Odoo (`odoo.conf`), **no** en la
base. Hay que ponerla **antes** de actualizar el módulo FTP, para que la
actualización cifre las claves que estén en texto plano.

1. Generarla (una sola vez):

   ```bash
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. Abrir el `odoo.conf` con el que arranca Odoo (el que se pasa con `-c`) y
   agregar esta línea en la sección `[options]`:

   ```ini
   ftp_secret_key = <llave>
   ```

   Comprobar que el archivo solo lo puede leer el usuario del servicio de
   Odoo (`chmod 640` o `600`; ya contiene la clave de Postgres).

3. Reiniciar Odoo para que lea el archivo de nuevo.

4. Guardar la llave en un lugar seguro fuera del servidor. Si se pierde, hay
   que volver a escribir las claves FTP en cada droguería.

También se puede usar la variable de entorno `FTP_SECRET_KEY` del servicio
de Odoo en lugar de `odoo.conf` (si están las dos, manda la variable). Más
detalles (cómo cambiar la llave sin perder las claves) en el README del
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

- El módulo FTP va en la versión 1.5 (la misma que mandó Daniel el
  2026-11-04, sin los archivos que no se usan): cifra las claves FTP, deja la
  oferta de las droguerías de solo lectura para los usuarios internos y trae
  los traslados entre sucursales de Daniel. Para ver la tarjeta *Traslados
  internos* hay que activar *Ajustes → Inventario → Ubicaciones de
  almacenamiento* (y, si alguna sucursal no la muestra, activar su tipo de
  operación).
- El asistente crea el menú **Compras IA**, sus vistas de análisis
  (`ai_v_*`) y dos tareas programadas (ver el paso 7).

## 4. Rol de solo lectura de Postgres

El agente consulta la base con un usuario propio de Postgres
(`dromax_ai_ro`) que solo puede leer una lista de tablas. Crearlo **después**
de instalar el asistente (así recibe permiso sobre sus vistas).

1. Crear el rol y sus permisos, como superusuario y conectado a la base de
   Odoo. `read -s` pide la clave sin mostrarla ni dejarla en el historial:

   ```bash
   read -s RO_PASS
   sudo -u postgres psql -d <base> -v ro_password="$RO_PASS" \
       < <carpeta de addons>/dromax_asistente_compras/scripts/create_readonly_role.sql
   ```

   El rol es del servidor de Postgres, pero los permisos son por base: para
   usarlo en otra base, ejecutar el script también con `-d <otra base>`.
   El script se puede repetir (por ejemplo, para añadir tablas nuevas de la
   lista), pero **cambia la clave**: actualizarla en todas las bases que
   usen el rol.

2. Permitirle entrar con clave. El asistente se conecta como Odoo (mismo
   host, puerto y base). Si `odoo.conf` no tiene `db_host`, Odoo usa el
   socket local, y en Ubuntu/Debian `pg_hba.conf` deja entrar por ahí solo a
   usuarios que existen en Linux (`peer`). Comprobarlo:

   ```bash
   psql -U dromax_ai_ro -d <base> -c "select count(*) from res_company"
   ```

   Si responde «Peer authentication failed», agregar en `pg_hba.conf`
   (`sudo -u postgres psql -Atc "show hba_file"`), **antes** de la línea
   `local all all peer`, una línea por base:

   ```
   local   <base>          dromax_ai_ro                            scram-sha-256
   ```

   y recargar Postgres sin cortar conexiones:
   `sudo -u postgres psql -c "select pg_reload_conf()"`. Repetir la prueba:
   debe pedir la clave y devolver un número. Con `db_host` en `odoo.conf`, la
   prueba lleva `-h <ese host>` y normalmente no hace falta tocar `pg_hba.conf`.

3. Decirle al asistente qué usuario usar, en una de estas formas:
   - en la base: **Ajustes → Técnico → Parámetros del sistema** (modo
     desarrollador), `dromax_asistente_compras.ro_user` = `dromax_ai_ro` y
     `dromax_asistente_compras.ro_password` = la clave. No hace falta
     reiniciar;
   - en `odoo.conf`, para todas las bases: `ai_purchase_ro_user` y
     `ai_purchase_ro_password`, y reiniciar Odoo (los parámetros de la base
     mandan sobre `odoo.conf`);
   - variables de entorno `AI_PURCHASE_RO_USER` y `AI_PURCHASE_RO_PASSWORD`
     del servicio.

4. Comprobar las barreras con la misma conexión: leer `res_partner` debe dar
   *permission denied* (está fuera de la lista) y un `UPDATE` debe dar
   *read-only transaction*.

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
   | INTERCONTINENTAL | Latin-1 | `codigo,barras,descripcion,lote,precio,descuento,precio_final,stock,laboratorio` |
   | INSUAMINCA | UTF-8 | `codigo,barras,descripcion,lote,precio,descuento,precio_final,stock,laboratorio,extra` |
   | VITALCLINIC | UTF-8 | `codigo,barras,descripcion,lote,precio,descuento,precio_final,stock,extra,descuento,descuento` |

   Copiar el texto de *Columnas* completo, tal cual. Con *Columnas* vacío se
   leen solo las 8 primeras (sin el laboratorio ni los descuentos extra de
   VITALCLINIC).
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
| *Compras IA → Reabastecimiento* | Lo que la sucursal matriz (Ajustes → *Sucursal matriz*; por defecto la que tenga DROMAX en el nombre) puede mandar a cada farmacia. Al elegir líneas, **Generar borradores de traslado** crea los *Traslados internos* en borrador; en cada uno, **Generar Traslados Definitivos**. Requiere Inventario / Usuario y el tipo *Traslados internos* activo en el almacén de la matriz. |
| *Compras IA → Propuestas* → **Calcular propuesta** | La propuesta de la semana, agrupada por laboratorio. |
| *Compras IA → Chat* → **Arma la compra de esta semana** | El resumen de la propuesta, con los botones **Generar todos los borradores** y **Abrir propuesta** debajo. |
| *Compras IA → Chat* → **Revisa un laboratorio** → **Generar borradores de …** | Una solicitud de presupuesto en borrador por farmacia y droguería, en *Compras*, solo con ese laboratorio. |
| *Compras IA → Configuración → Consumo de IA* | Una línea por cada llamada al modelo, con su costo. |

## 11. Actualizar más adelante

Antes de cada actualización: copia de seguridad de las bases y de las
carpetas de los módulos.

- **Asistente**: `git pull` en la carpeta del módulo (o reemplazar la
  carpeta) y actualizarlo (`-u dromax_asistente_compras`).
- **Módulo FTP**: reemplazar la carpeta por la del `.zip` nuevo y actualizarlo
  (`-u advanced_interbranch_transfer`).
- Actualizar **todas las bases** que tienen el módulo instalado, con el
  servicio detenido, y después arrancarlo:

  ```bash
  systemctl stop <servicio>
  <python> <odoo-bin> -c <odoo.conf> -d <base> \
      -u advanced_interbranch_transfer,dromax_asistente_compras --stop-after-init --no-http
  # (repetir -d para cada base)
  systemctl start <servicio>
  ```

  Usar el mismo Python y `odoo-bin` que el servicio (`systemctl cat
  <servicio>`, línea `ExecStart`). Después, entrar al navegador con
  Ctrl+Shift+R.
- Si cambia la lista de tablas que puede leer el agente, volver a ejecutar el
  script del rol (paso 4) en cada base y actualizar la clave.

### Probar en otra base antes de producción

Para probar una versión nueva en una base de prueba sin tocar las demás ni
reiniciar el servicio:

1. Dejar el módulo nuevo en una carpeta aparte (p. ej. `<odoo>/test-addons`),
   sin tocar la del `addons_path`.
2. Actualizar solo la base de prueba poniendo esa carpeta primero (gana la
   primera carpeta que tenga el módulo):

   ```bash
   <python> <odoo-bin> -c <odoo.conf> -d <base de prueba> \
       --addons-path=<odoo>/test-addons,<addons_path de odoo.conf> \
       -u dromax_asistente_compras --stop-after-init --no-http --max-cron-threads=0
   ```

3. Levantar una instancia temporal solo para esa base, en un puerto local:

   ```bash
   systemd-run --unit=odoo-prueba <python> <odoo-bin> -c <odoo.conf> \
       --addons-path=<odoo>/test-addons,<addons_path de odoo.conf> \
       -d <base de prueba> --db-filter='^<base de prueba>$' \
       --http-interface=127.0.0.1 --http-port=8099 --max-cron-threads=0 --workers=0
   ```

   y abrirla desde la máquina propia con un túnel:
   `ssh -N -L 8099:localhost:8099 <servidor>` → `http://localhost:8099`.
4. Mientras dure la prueba, usar esa base solo por el 8099: por el puerto
   normal, el servicio sigue con el código anterior y sus pantallas pueden
   fallar.
5. Al terminar: `systemctl stop odoo-prueba` y actualizar todas las bases
   como arriba (o restaurar la base de prueba si algo falló).

## Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| «Falta la llave de cifrado de las claves FTP…» al guardar la clave FTP, o el aviso en la pestaña *Configuración FTP* | Odoo no tiene la llave (paso 2). Agregar `ftp_secret_key` en `odoo.conf`, reiniciar Odoo y volver a escribir la clave. Con una versión del módulo FTP anterior a este cambio el mensaje es «Falta la variable de entorno FTP_SECRET_KEY…» y solo sirve la variable de entorno del servicio. |
| «No se pudo descifrar la clave FTP» | La llave no es la que se usó al guardar la clave. Revisar `ftp_secret_key` en `odoo.conf` (o la variable `FTP_SECRET_KEY`) o volver a escribir la clave en el contacto. |
| Tras actualizar, las pantallas del asistente o del módulo FTP fallan en una base (columna o campo que no existe) | Esa base no se actualizó y el servicio ya usa el código nuevo: actualizarla con `-u` (ver el paso 11). |
| El chat responde que no puede consultar la base | Faltan los parámetros `ro_user` / `ro_password`, la clave no coincide (el script del rol la cambia cada vez que se ejecuta), o `pg_hba.conf` no tiene la línea de esa base para `dromax_ai_ro` (paso 4). |
| «El Asistente de Compras IA es solo para el personal de compras…» | El usuario no tiene *Compras / Usuario*. |
| «Para generar órdenes de compra hace falta el permiso de Compras» | Igual que el anterior: falta *Compras / Usuario*. |
| La propuesta no trae droguería en muchos productos | Sus códigos de barras no coinciden con los de las droguerías: revisar el reporte de *Códigos de barras*. |
| «Probar llave» falla | Llave de OpenRouter errónea o sin crédito, o el servidor no tiene salida a `openrouter.ai`. |
