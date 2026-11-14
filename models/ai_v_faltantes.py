# -*- coding: utf-8 -*-
from odoo import fields, models, tools

from ..services.network_sql import PURCHASE_NAME_SQL, network_ctes, target_sql
from .ai_v_oferta import grant_readonly
from .ai_v_rotacion import network_params_sql

# Un precio de droguería a más de este múltiplo del costo (o por debajo de su
# fracción) puede ser otro empaque con el mismo código de barras.
PRESENTATION_RATIO = 3


def needs_presentation_review(price, cost, min_price, max_price):
    """Lo mismo que la columna revisar de la vista, para una oferta suelta."""
    if cost:
        return price > PRESENTATION_RATIO * cost or price * PRESENTATION_RATIO < cost
    return bool(min_price) and max_price > PRESENTATION_RATIO * min_price


class AiVFaltantes(models.Model):
    """Lo que hay que comprar esta semana: una fila por producto y sucursal que recibe.

    La sucursal matriz (Dromax) es una droguería y no vende: guarda inventario
    para reabastecer a las farmacias (ai_v_reabastecimiento). Por eso hay dos
    clases de filas:

    - Farmacias (para_matriz = falso): a una farmacia que llegó a su punto de
      pedido le falta objetivo − stock − compras pendientes − en camino; la
      matriz le manda lo que puede (envios, el mismo reparto del
      reabastecimiento) y el resto se compra a nombre de la farmacia, que lo
      recibe.
    - Inventario de la matriz (para_matriz = verdadero): su demanda es lo que
      traslada a las farmacias (ai_v_rotacion). Después de mandar lo de esta
      semana, si lo que le queda (libre − envíos + compras pendientes + en
      camino) baja a su punto de pedido, se repone hasta su objetivo. Lo
      recibe la matriz, pero la orden sale a nombre de la farmacia del ajuste
      «Compras de Dromax a nombre de» (a_nombre_de_id): la droguería no compra
      a su nombre.

    Objetivo = demanda semanal × semanas de cobertura más el margen de
    seguridad; punto de pedido = un % de esa demanda (los dos en Ajustes, ver
    network_sql). Las solicitudes sin confirmar no se restan (en_solicitud
    solo avisa).

    Droguería sugerida: entre las ofertas disponibles del producto, primero
    las de precio coherente con el costo (el de la sucursal que recibe o, si no
    tiene, el de la matriz; entre un tercio y el triple: algunas droguerías
    usan el mismo código de barras para otro empaque, p. ej. un dispensador de
    50 blísteres o una ampolla suelta en vez de la caja), luego las que tienen
    stock para toda la cantidad y, entre ellas, la más barata. Sin costo, se
    compara con la oferta más barata: si alguna pasa del triple, todas quedan
    para revisar. Si la elegida no es coherente, revisar_presentacion lo
    avisa. Los parámetros se leen de Ajustes en cada consulta.

    id = product_id × 1000 + company_id, como en el reabastecimiento.
    """
    _name = "ai.v.faltantes"
    _description = "Necesidad de compra"
    _auto = False
    _order = "cobertura_actual, demanda_semanal desc, product_id, company_id"
    _rec_name = "product_id"

    company_id = fields.Many2one("res.company", "Recibe", readonly=True,
                                 help="Sucursal que recibe la mercancía: la farmacia o la matriz.")
    farmacia = fields.Char("Nombre de la sucursal que recibe", readonly=True)
    a_nombre_de_id = fields.Many2one(
        "res.company", "A nombre de", readonly=True,
        help="A nombre de quién sale la orden: la propia farmacia o, para el inventario de la "
             "matriz, la farmacia de Ajustes (vacío si no está configurada).")
    para_matriz = fields.Boolean("Inventario de Dromax", readonly=True,
                                 help="Compra para el inventario de la matriz, a nombre de una farmacia.")
    product_id = fields.Many2one("product.product", "Producto", readonly=True)
    product_tmpl_id = fields.Many2one("product.template", "Plantilla", readonly=True)
    producto = fields.Char("Nombre del producto", readonly=True)
    codigo = fields.Char("Código interno", readonly=True)
    demanda_semanal = fields.Float("Demanda semanal", readonly=True,
                                   help="Farmacia: su venta semanal depurada. Matriz: lo que traslada a las "
                                        "farmacias por semana.")
    semanas_cobertura = fields.Integer("Semanas de cobertura", readonly=True)
    objetivo = fields.Integer("Objetivo", readonly=True,
                              help="Demanda × semanas de cobertura más el margen de seguridad.")
    stock = fields.Float("Stock", readonly=True,
                         help="Farmacia: su stock (el negativo cuenta como 0). Matriz: su stock libre (menos "
                              "lo que ya sale hacia las farmacias).")
    pendiente = fields.Float("Compras pendientes", readonly=True, help="De la sucursal que recibe, por recibir.")
    atrasado = fields.Float("Atrasado", readonly=True, help="Parte de lo pendiente con la fecha prevista vencida.")
    en_solicitud = fields.Float("En solicitudes sin confirmar", readonly=True,
                                help="No se resta: avisa de que ya se está pidiendo.")
    en_camino = fields.Float("En camino por traslado", readonly=True,
                             help="Traslados sin terminar que llegan a la sucursal.")
    traslado = fields.Integer("Traslado de Dromax", readonly=True,
                              help="Farmacia: lo que la matriz le manda esta semana (se descuenta de la compra). "
                                   "Matriz: lo que manda esta semana a las farmacias (sale de su stock).")
    cobertura_actual = fields.Float("Cobertura actual (semanas)", readonly=True,
                                    help="Lo que tiene (con lo pendiente, en camino y el traslado) / demanda semanal.")
    faltante = fields.Float("Faltante", readonly=True)
    cantidad_sugerida = fields.Integer("Cantidad sugerida", readonly=True,
                                       help="Faltante redondeado hacia arriba.")
    drogueria_id = fields.Many2one("res.partner", "Droguería sugerida", readonly=True)
    drogueria = fields.Char("Nombre de la droguería", readonly=True)
    precio = fields.Float("Precio final", readonly=True, help="Precio con descuentos de la droguería sugerida.")
    descuento = fields.Float("% Descuento", readonly=True,
                             help="Descuento de la droguería sugerida, ya incluido en el precio final.")
    mejor_precio = fields.Float("Mejor precio", readonly=True,
                                help="Precio final más bajo entre las droguerías con stock.")
    monto = fields.Float("Monto", readonly=True, help="Cantidad sugerida × precio final.")
    stock_drogueria = fields.Integer("Stock en la droguería", readonly=True)
    stock_drogueria_suficiente = fields.Boolean("Alcanza el stock de la droguería", readonly=True)
    droguerias_con_oferta = fields.Integer("Droguerías con oferta", readonly=True)
    fecha_vencimiento = fields.Date("Vencimiento", readonly=True, help="Vencimiento del lote ofrecido.")
    laboratorio = fields.Char("Laboratorio (droguería)", readonly=True,
                              help="Laboratorio que informa la droguería, si lo informa.")
    costo = fields.Float("Costo actual", readonly=True,
                         help="Costo del producto en la sucursal que recibe (o en la matriz).")
    revisar_presentacion = fields.Boolean(
        "Revisar presentación", readonly=True,
        help="El precio de la droguería pasa del triple del costo o no llega a un tercio (sin "
             "costo: las ofertas pasan del triple de la más barata): puede ser otro empaque con el "
             "mismo código de barras (p. ej. un dispensador en vez de un blíster, o una ampolla "
             "suelta en vez de la caja).")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE VIEW ai_v_faltantes AS
            WITH %(red)s
            enviado AS (
                SELECT product_id, sum(cantidad) AS cantidad FROM envios GROUP BY 1
            ),
            compra_farmacias AS (
                -- Lo que a una farmacia en su punto de pedido le falta y la matriz no le
                -- puede mandar: lo compra la farmacia, a su nombre.
                SELECT f.company_id, f.product_id, f.company_id AS a_nombre_de, false AS para_matriz,
                       f.demanda, %(objetivo_f)s AS objetivo, f.stock, f.pendiente, f.en_camino,
                       COALESCE(e.cantidad, 0) AS traslado,
                       (f.stock + f.pendiente + f.en_camino + COALESCE(e.cantidad, 0)) / f.demanda AS cobertura,
                       (f.falta - COALESCE(e.cantidad, 0))::float AS faltante
                  FROM faltas f
                 CROSS JOIN params
                  LEFT JOIN envios e ON e.company_id = f.company_id AND e.product_id = f.product_id
                 WHERE f.falta - COALESCE(e.cantidad, 0) > 0
            ),
            nivel_matriz AS (
                -- Lo que le queda a la matriz después de mandar lo de esta semana.
                SELECT m.*, COALESCE(e.cantidad, 0) AS traslado,
                       greatest(m.libre - COALESCE(e.cantidad, 0), 0) AS queda
                  FROM matriz m
                  LEFT JOIN enviado e ON e.product_id = m.product_id
                 WHERE m.demanda > 0
            ),
            compra_matriz AS (
                -- Inventario de la matriz: su demanda es lo que traslada. Al llegar a su punto
                -- de pedido se repone hasta el objetivo, a nombre de la farmacia de Ajustes.
                SELECT params.matriz_id AS company_id, n.product_id, %(a_nombre)s AS a_nombre_de,
                       true AS para_matriz, n.demanda, %(objetivo_m)s AS objetivo, n.queda AS stock,
                       n.pendiente, n.en_camino, n.traslado,
                       (n.queda + n.pendiente + n.en_camino) / n.demanda AS cobertura,
                       %(objetivo_m)s - n.queda - n.pendiente - n.en_camino AS faltante
                  FROM nivel_matriz n
                 CROSS JOIN params
                 WHERE n.queda + n.pendiente + n.en_camino <= n.demanda * params.semanas * params.punto
                   AND %(objetivo_m)s - n.queda - n.pendiente - n.en_camino > 0
            ),
            faltantes AS (
                SELECT c.*, ceil(c.faltante)::int AS cantidad
                  FROM (SELECT * FROM compra_farmacias UNION ALL SELECT * FROM compra_matriz) c
            ),
            costos AS (
                -- standard_price depende de la empresa: está en ir_property (el de la
                -- sucursal que recibe, el de la matriz o el valor por defecto sin empresa;
                -- el primero que no sea 0).
                SELECT DISTINCT ON (f.company_id, f.product_id) f.company_id, f.product_id,
                       ip.value_float AS costo
                  FROM faltantes f
                 CROSS JOIN params
                  JOIN ir_property ip ON ip.name = 'standard_price'
                                     AND ip.res_id = 'product.product,' || f.product_id
                                     AND ip.value_float <> 0
                                     AND (ip.company_id IN (f.company_id, params.matriz_id) OR ip.company_id IS NULL)
                 ORDER BY f.company_id, f.product_id,
                          CASE WHEN ip.company_id = f.company_id THEN 0
                               WHEN ip.company_id = params.matriz_id THEN 1 ELSE 2 END
            ),
            candidatas AS (
                SELECT f.company_id, f.product_id, f.cantidad, k.costo, o.id AS oferta_id, o.partner_id,
                       o.drogueria, o.precio_final, o.descuento_pct, o.stock_drogueria, o.fecha_vencimiento,
                       o.laboratorio, o.mejor_precio, o.droguerias_con_stock AS droguerias,
                       min(o.precio_final) OVER (PARTITION BY f.company_id, f.product_id) AS precio_min,
                       max(o.precio_final) OVER (PARTITION BY f.company_id, f.product_id) AS precio_max
                  FROM faltantes f
                  JOIN ai_v_oferta o ON o.product_id = f.product_id AND o.disponible
                  LEFT JOIN costos k ON k.company_id = f.company_id AND k.product_id = f.product_id
            ),
            precios AS (
                -- Otra presentación con el mismo código: con costo, un precio de más del
                -- triple o de menos de un tercio del costo; sin costo, ofertas de más del
                -- triple de la más barata.
                SELECT c.*, c.stock_drogueria >= c.cantidad AS suficiente,
                       (CASE WHEN c.costo IS NOT NULL
                             THEN c.precio_final > %(ratio)s * c.costo OR c.precio_final * %(ratio)s < c.costo
                             ELSE c.precio_max > %(ratio)s * c.precio_min END) IS TRUE AS revisar
                  FROM candidatas c
            ),
            ofertas AS (
                SELECT p.*,
                       row_number() OVER (PARTITION BY p.company_id, p.product_id
                                          ORDER BY p.revisar, p.suficiente DESC,
                                                   p.precio_final, p.partner_id, p.oferta_id) AS puesto
                  FROM precios p
            )
            SELECT (f.product_id::bigint * 1000 + f.company_id)::int AS id,
                   f.company_id,
                   c.name AS farmacia,
                   f.a_nombre_de AS a_nombre_de_id,
                   f.para_matriz,
                   f.product_id,
                   pp.product_tmpl_id,
                   COALESCE(pt.name->>'es_VE', pt.name->>'en_US') AS producto,
                   pp.default_code AS codigo,
                   round(f.demanda::numeric, 2) AS demanda_semanal,
                   params.semanas AS semanas_cobertura,
                   f.objetivo::int AS objetivo,
                   round(f.stock::numeric, 2) AS stock,
                   round(f.pendiente::numeric, 2) AS pendiente,
                   round(COALESCE(pd.atrasado, 0)::numeric, 2) AS atrasado,
                   round(COALESCE(pd.en_solicitud, 0)::numeric, 2) AS en_solicitud,
                   round(f.en_camino::numeric, 2) AS en_camino,
                   f.traslado::int AS traslado,
                   round(f.cobertura::numeric, 2) AS cobertura_actual,
                   round(f.faltante::numeric, 2) AS faltante,
                   f.cantidad AS cantidad_sugerida,
                   o.partner_id AS drogueria_id,
                   o.drogueria,
                   o.precio_final AS precio,
                   o.descuento_pct AS descuento,
                   o.mejor_precio,
                   round((f.cantidad * o.precio_final)::numeric, 2) AS monto,
                   o.stock_drogueria,
                   o.suficiente AS stock_drogueria_suficiente,
                   COALESCE(o.droguerias, 0) AS droguerias_con_oferta,
                   o.fecha_vencimiento,
                   o.laboratorio,
                   round(k.costo::numeric, 2) AS costo,
                   COALESCE(o.revisar, false) AS revisar_presentacion
              FROM faltantes f
             CROSS JOIN params
              JOIN res_company c ON c.id = f.company_id
              JOIN product_product pp ON pp.id = f.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
              LEFT JOIN pend pd ON pd.company_id = f.company_id AND pd.product_id = f.product_id
              LEFT JOIN costos k ON k.company_id = f.company_id AND k.product_id = f.product_id
              LEFT JOIN ofertas o ON o.company_id = f.company_id AND o.product_id = f.product_id AND o.puesto = 1
             WHERE pp.active AND pt.purchase_ok
        """ % {"red": network_ctes(*network_params_sql()), "objetivo_f": target_sql("f.demanda"),
               "objetivo_m": target_sql("n.demanda"), "a_nombre": PURCHASE_NAME_SQL,
               "ratio": PRESENTATION_RATIO})
        grant_readonly(self.env, self._table)
