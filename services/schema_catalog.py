# -*- coding: utf-8 -*-
"""Esquema que se le explica al modelo en el system prompt.

No se vuelcan todas las columnas (sale_order_line tiene más de cien y gastaría
tokens en cada llamada): solo las útiles para compras, con lo que significan.
Antes de enviarlo se filtra contra las columnas que existen de verdad en la
base (`existing_schema`), así el prompt no promete columnas que no están: la
base multisucursal de producción puede diferir de la de desarrollo.
"""

# tabla -> (descripción, [(columna, descripción), ...])
CATALOG = {
    "ai_v_rotacion": (
        "Demanda semanal por producto y farmacia en las últimas semanas completas (lunes a "
        "domingo, sin la semana en curso): TPV + pedidos de venta, semanas sin venta = 0. Una "
        "fila por producto y farmacia con alguna venta en la ventana; si no hay fila, no se "
        "vendió. Úsala en vez de calcular la demanda a mano.",
        [
            ("company_id", "sucursal (farmacia)"),
            ("farmacia", "nombre de la farmacia"),
            ("product_id", "product_product.id"),
            ("product_tmpl_id", "product_template.id"),
            ("producto", "nombre del producto"),
            ("codigo", "código interno"),
            ("semanas", "semanas de la ventana"),
            ("desde", "lunes de la primera semana de la ventana"),
            ("hasta", "domingo de la última semana completa"),
            ("semanas_con_venta", "semanas con venta distinta de 0"),
            ("venta_total", "unidades vendidas en la ventana (TPV + pedidos)"),
            ("venta_tpv", "unidades vendidas por TPV"),
            ("venta_pedidos", "unidades de pedidos de venta (clientes especiales)"),
            ("demanda_media", "media semanal con todas las semanas, atípicas incluidas"),
            ("demanda_mediana", "mediana semanal"),
            ("desviacion", "desviación estándar semanal"),
            ("mad", "desviación absoluta mediana"),
            ("semanas_atipicas", "semanas descartadas por alejarse de la mediana más de factor × MAD"),
            ("demanda_depurada", "media semanal sin las semanas atípicas: la demanda para la propuesta"),
        ],
    ),
    "ai_v_stock": (
        "Existencia actual por producto y farmacia (ubicaciones internas). Si no hay fila, "
        "el producto nunca tuvo stock en esa farmacia: cuenta como 0.",
        [
            ("company_id", "sucursal (farmacia)"),
            ("farmacia", "nombre de la farmacia"),
            ("product_id", "product_product.id"),
            ("product_tmpl_id", "product_template.id"),
            ("producto", "nombre del producto"),
            ("codigo", "código interno"),
            ("stock", "unidades en existencia (puede ser negativo)"),
            ("reservado", "unidades reservadas para salidas"),
            ("disponible", "stock menos reservado"),
        ],
    ),
    "ai_v_pendiente": (
        "Compras pendientes de recibir por producto y farmacia (órdenes confirmadas que aún no "
        "han llegado). Si no hay fila, no hay nada pendiente. Para el detalle por orden, usa "
        "purchase_order_line.",
        [
            ("company_id", "sucursal (farmacia)"),
            ("farmacia", "nombre de la farmacia"),
            ("product_id", "product_product.id"),
            ("product_tmpl_id", "product_template.id"),
            ("producto", "nombre del producto"),
            ("codigo", "código interno"),
            ("pendiente", "unidades confirmadas que faltan por llegar: se restan en la propuesta"),
            ("atrasado", "parte de lo pendiente cuya fecha prevista ya pasó"),
            ("ordenes", "órdenes confirmadas con algo pendiente"),
            ("proxima_llegada", "fecha prevista más cercana de lo pendiente (UTC)"),
            ("en_solicitud", "unidades en solicitudes de presupuesto sin confirmar: no se restan, "
                             "pero indican que ya se está pidiendo"),
        ],
    ),
    "ai_v_faltantes": (
        "Lo que hace falta comprar: una fila por producto y farmacia con faltante > 0 "
        "(demanda depurada × semanas de cobertura − stock − pendiente). Si no hay fila, no "
        "hace falta pedirlo. Trae la droguería sugerida: la más barata que tiene toda la "
        "cantidad (o la más barata si ninguna alcanza), prefiriendo precios coherentes con el "
        "costo. Es la base de la propuesta de compra.",
        [
            ("company_id", "sucursal (farmacia) que necesita el producto"),
            ("farmacia", "nombre de la farmacia"),
            ("product_id", "product_product.id"),
            ("product_tmpl_id", "product_template.id"),
            ("producto", "nombre del producto"),
            ("codigo", "código interno"),
            ("demanda_semanal", "demanda depurada (ai_v_rotacion)"),
            ("semanas_cobertura", "semanas de cobertura configuradas"),
            ("necesidad", "demanda_semanal × semanas_cobertura"),
            ("stock", "existencia actual; la negativa cuenta como 0"),
            ("pendiente", "pendiente de recibir de órdenes confirmadas (ya restado)"),
            ("atrasado", "parte de lo pendiente con la fecha prevista vencida"),
            ("en_solicitud", "unidades en solicitudes sin confirmar (no restadas): avisar"),
            ("cobertura_actual", "semanas que cubren stock + pendiente: menor = más urgente"),
            ("faltante", "necesidad − stock − pendiente, con decimales"),
            ("cantidad_sugerida", "faltante redondeado hacia arriba: la cantidad a pedir"),
            ("excedente_otras", "stock de otras farmacias que pasa de varias semanas de su propia "
                                "demanda: se podría trasladar en vez de comprar"),
            ("farmacias_con_excedente", "qué farmacias tienen ese excedente y cuánto"),
            ("drogueria_id", "droguería sugerida (id del contacto); NULL si ninguna lo ofrece"),
            ("drogueria", "nombre de la droguería sugerida"),
            ("precio", "precio final de la droguería sugerida"),
            ("mejor_precio", "precio final más bajo entre droguerías con stock"),
            ("monto", "cantidad_sugerida × precio"),
            ("stock_drogueria", "stock de la droguería sugerida"),
            ("stock_drogueria_suficiente", "TRUE si la droguería sugerida tiene toda la cantidad"),
            ("droguerias_con_oferta", "cuántas droguerías tienen el producto disponible"),
            ("fecha_vencimiento", "vencimiento del lote ofrecido"),
            ("laboratorio", "laboratorio según la droguería sugerida (texto)"),
            ("costo", "costo actual del producto en la farmacia"),
            ("revisar_presentacion", "TRUE si el precio de la droguería pasa del triple del costo: "
                                     "posible empaque distinto con el mismo código; no comprar sin revisar"),
        ],
    ),
    "ai_v_oferta": (
        "Oferta vigente de las droguerías (inventarios que publican por FTP), una fila por "
        "producto y droguería, ya cruzada con los productos propios por código de barras "
        "normalizado. Úsala para comparar droguerías: el costo que cuenta es precio_final. "
        "Las columnas de comparación solo consideran ofertas disponibles (con stock y precio).",
        [
            ("partner_id", "droguería (id del contacto)"),
            ("drogueria", "nombre de la droguería"),
            ("product_id", "product_product.id del producto propio; NULL si no lo tenemos o el código no casa"),
            ("product_tmpl_id", "product_template.id"),
            ("producto", "nombre del producto propio"),
            ("codigo_barras", "código de barras normalizado (EAN-13): clave para comparar droguerías"),
            ("codigo_drogueria", "código del producto en la droguería"),
            ("descripcion", "descripción en el archivo de la droguería"),
            ("laboratorio", "laboratorio según la droguería (texto; no todas lo informan)"),
            ("fecha_vencimiento", "vencimiento del lote ofrecido"),
            ("precio_lista", "precio antes de descuentos"),
            ("descuento_pct", "descuento total en %, con los descuentos en cascada ya aplicados"),
            ("precio_final", "precio con todos los descuentos: el costo de compra"),
            ("stock_drogueria", "unidades disponibles en la droguería"),
            ("disponible", "TRUE si tiene stock y precio final > 0"),
            ("orden_precio", "puesto de la droguería para ese código (1 = la más barata); solo en la "
                             "fila de su mejor oferta disponible, NULL en las demás"),
            ("mejor_precio", "precio final más bajo entre las ofertas disponibles del código"),
            ("segundo_precio", "mejor precio de la segunda droguería más barata del código; NULL si solo hay una"),
            ("droguerias_con_stock", "cuántas droguerías tienen ese código disponible"),
            ("fecha_carga", "cuándo se cargó el inventario de la droguería (UTC)"),
        ],
    ),
    "ai_barcode_mismatch": (
        "Reporte de incoherencias de código de barras entre productos propios y droguerías "
        "(se genera con un botón; puede estar desactualizado o vacío).",
        [
            ("tipo", "codigo_distinto (nombre parecido con otro código), sin_oferta, sin_codigo, "
                     "sin_producto (código de droguería que no tenemos), presentacion (mismo código "
                     "con precio más del triple: otra presentación), duplicado"),
            ("product_id", "product_product.id del producto propio"),
            ("codigo_propio", "código de barras en la ficha del producto"),
            ("codigo_barras", "código normalizado (EAN-13) de la droguería o del producto"),
            ("descripcion", "descripción en la droguería"),
            ("droguerias", "droguerías que ofrecen ese código"),
            ("similitud", "parecido de nombres de 0 a 1 (solo codigo_distinto)"),
            ("precio_min", "precio final mínimo entre droguerías"),
            ("precio_max", "precio final máximo (presentacion)"),
            ("detalle", "explicación de la incoherencia"),
            ("create_date", "cuándo se generó el reporte (UTC)"),
        ],
    ),
    "res_company": (
        "Empresas. Cada farmacia es una sucursal: una empresa hija (parent_id) de la principal.",
        [
            ("id", ""),
            ("name", "nombre de la empresa o sucursal"),
            ("parent_id", "empresa principal (NULL en la principal)"),
            ("active", ""),
        ],
    ),
    "pos_order": (
        "Ventas del punto de venta (TPV): ~90 % de las ventas, a clientes particulares.",
        [
            ("id", ""),
            ("name", "número de ticket"),
            ("date_order", "fecha y hora de la venta (UTC)"),
            ("state", "paid, done o invoiced = venta efectiva; draft y cancel no cuentan"),
            ("company_id", "sucursal"),
            ("config_id", "caja (punto de venta)"),
            ("amount_total", "total con impuestos"),
        ],
    ),
    "pos_order_line": (
        "Líneas de las ventas del TPV.",
        [
            ("order_id", "pos_order.id"),
            ("product_id", "product_product.id"),
            ("qty", "unidades vendidas (negativas en devoluciones)"),
            ("price_unit", "precio unitario"),
            ("discount", "descuento en %"),
            ("price_subtotal", "subtotal sin impuestos"),
            ("company_id", "sucursal"),
        ],
    ),
    "sale_order": (
        "Pedidos de venta: clientes especiales (alcaldía, personal de la empresa...), "
        "a menudo con descuentos o volúmenes atípicos.",
        [
            ("id", ""),
            ("name", "número de pedido"),
            ("date_order", "fecha del pedido (UTC)"),
            ("state", "sale = confirmado; draft, sent y cancel no son ventas"),
            ("company_id", "sucursal"),
            ("warehouse_id", "almacén que despacha"),
            ("amount_total", "total con impuestos"),
        ],
    ),
    "sale_order_line": (
        "Líneas de los pedidos de venta.",
        [
            ("order_id", "sale_order.id"),
            ("product_id", "product_product.id"),
            ("product_uom_qty", "cantidad pedida"),
            ("qty_delivered", "cantidad entregada"),
            ("price_unit", "precio unitario"),
            ("discount", "descuento en %"),
            ("price_subtotal", "subtotal sin impuestos"),
            ("state", "estado del pedido (copiado de sale_order)"),
            ("company_id", "sucursal"),
        ],
    ),
    "purchase_order": (
        "Órdenes de compra a droguerías.",
        [
            ("id", ""),
            ("name", "número de orden"),
            ("partner_id", "droguería (id del contacto)"),
            ("date_order", "fecha de la orden (UTC)"),
            ("date_planned", "fecha prevista de llegada"),
            ("state", "purchase o done = confirmada; draft, sent, to approve y cancel no comprometen compra"),
            ("company_id", "sucursal"),
            ("amount_total", "total con impuestos"),
        ],
    ),
    "purchase_order_line": (
        "Líneas de las órdenes de compra. Pendiente de recibir = product_qty - qty_received "
        "en líneas con state IN ('purchase', 'done').",
        [
            ("order_id", "purchase_order.id"),
            ("product_id", "product_product.id"),
            ("partner_id", "droguería"),
            ("product_qty", "cantidad pedida"),
            ("qty_received", "cantidad ya recibida"),
            ("price_unit", "precio unitario de compra"),
            ("discount", "descuento en %"),
            ("date_planned", "fecha prevista de llegada"),
            ("state", "estado de la orden (copiado de purchase_order)"),
            ("company_id", "sucursal"),
        ],
    ),
    "stock_quant": (
        "Existencias por producto y ubicación. Solo cuentan las ubicaciones internas "
        "(stock_location.usage = 'internal').",
        [
            ("product_id", "product_product.id"),
            ("location_id", "stock_location.id"),
            ("quantity", "unidades en existencia"),
            ("reserved_quantity", "unidades reservadas para salidas"),
            ("company_id", "sucursal"),
        ],
    ),
    "stock_location": (
        "Ubicaciones de inventario.",
        [
            ("id", ""),
            ("complete_name", "ruta completa, p. ej. FARM1/Existencias"),
            ("usage", "internal = stock propio; customer, supplier, inventory... no son stock"),
            ("warehouse_id", "almacén al que pertenece"),
            ("company_id", "sucursal"),
        ],
    ),
    "stock_warehouse": (
        "Almacenes.",
        [
            ("id", ""),
            ("name", ""),
            ("code", "código corto"),
            ("company_id", "sucursal"),
            ("lot_stock_id", "ubicación principal de existencias"),
        ],
    ),
    "product_product": (
        "Variantes de producto: lo que se vende, se compra y se almacena (product_id en el resto de tablas).",
        [
            ("id", ""),
            ("product_tmpl_id", "product_template.id"),
            ("default_code", "código interno"),
            ("barcode", "código de barras tal como se escribió"),
            ("ai_barcode_norm", "código de barras normalizado (EAN-13), el que casa con ai_v_oferta.codigo_barras"),
            ("active", "FALSE = archivado"),
        ],
    ),
    "product_template": (
        "Fichas de producto.",
        [
            ("id", ""),
            ("name", "JSONB con traducciones: usa name->>'es_VE' con respaldo en name->>'en_US'"),
            ("default_code", "código interno"),
            ("categ_id", "product_category.id"),
            ("detailed_type", "product = almacenable; consu y service no llevan stock"),
            ("list_price", "precio de venta"),
            ("sale_ok", "se puede vender"),
            ("purchase_ok", "se puede comprar"),
            ("uom_id", "unidad de venta (uom_uom.id)"),
            ("uom_po_id", "unidad de compra (uom_uom.id)"),
            ("active", "FALSE = archivado"),
        ],
    ),
    "product_category": (
        "Categorías de producto.",
        [
            ("id", ""),
            ("complete_name", "ruta completa de la categoría"),
            ("parent_id", "categoría padre"),
        ],
    ),
    "uom_uom": (
        "Unidades de medida.",
        [
            ("id", ""),
            ("name", "JSONB con traducciones, como product_template.name"),
            ("factor", "factor respecto a la unidad de referencia de su categoría"),
        ],
    ),
}


def existing_schema(cr, tables):
    """Catálogo recortado a las tablas de `tables` y a las columnas que existen.

    Devuelve [(tabla, descripción, [(columna, descripción), ...]), ...].
    Las tablas permitidas que no están en el catálogo (p. ej. las vistas ai_v_*
    cuando existan) se listan con todas sus columnas reales.
    """
    tables = sorted(tables)
    cr.execute("""
        SELECT table_name, array_agg(column_name::text ORDER BY ordinal_position)
          FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = ANY(%s)
         GROUP BY table_name
    """, (tables,))
    real = dict(cr.fetchall())
    result = []
    for table in tables:
        if table not in real:
            continue  # aún no existe en esta base (vistas futuras, TPV sin instalar...)
        columns = set(real[table])
        if table in CATALOG:
            desc, cols = CATALOG[table]
            cols = [(c, d) for c, d in cols if c in columns]
        else:
            desc, cols = "", [(c, "") for c in real[table]]
        result.append((table, desc, cols))
    return result
