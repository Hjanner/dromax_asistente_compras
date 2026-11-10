# -*- coding: utf-8 -*-
from odoo import fields, models, tools

from ..services.network_sql import network_ctes, target_sql
from .ai_v_oferta import grant_readonly
from .ai_v_rotacion import network_params_sql


class AiVFaltantes(models.Model):
    """Lo que la sucursal matriz (Dromax) tiene que comprar para toda la red.

    Toda compra entra por la matriz, que después reparte a las farmacias con
    traslados (ai_v_reabastecimiento); entre farmacias no se traslada. Por
    eso hay una fila por producto, con company_id = la matriz:

    necesidad = Σ faltas de las farmacias debajo del objetivo (objetivo −
                stock − compras pendientes − en camino, por farmacia; lo que
                le sobra a una no cubre a otra)
              + objetivo de la venta propia de la matriz;
    faltante  = necesidad − stock libre de la matriz (su stock menos lo que ya
                sale hacia las farmacias) − sus compras pendientes.

    Objetivo = venta semanal × semanas de cobertura más el margen de
    seguridad; punto de pedido = un % de esa venta (los dos en Ajustes, ver
    network_sql). Se compra solo si, después de mandar lo que les falta a las
    farmacias que llegaron a su punto de pedido, a la matriz le queda su
    propio punto de pedido o menos: así no se compra una unidad cuando la
    matriz todavía tiene, ni se compra solo la unidad de una farmacia cuando
    la matriz está vacía (se repone a todas las que están debajo del
    objetivo). La cantidad sugerida es el faltante redondeado hacia arriba.
    Las solicitudes sin confirmar de la matriz no se restan (en_solicitud solo
    avisa). detalle_farmacias dice cuánto le falta a cada farmacia.

    Droguería sugerida: entre las ofertas disponibles del producto, primero
    las de precio coherente con el costo de la matriz (hasta el triple:
    algunas droguerías usan el mismo código de barras para un empaque mayor,
    p. ej. un dispensador de 50 blísteres), luego las que tienen stock para
    toda la cantidad y, entre ellas, la más barata. Si la elegida pasa del
    triple del costo, revisar_presentacion lo avisa. Los parámetros se leen de
    Ajustes en cada consulta.
    """
    _name = "ai.v.faltantes"
    _description = "Necesidad de compra de la red"
    _auto = False
    _order = "cobertura_actual, demanda_semanal desc, product_id"
    _rec_name = "product_id"

    company_id = fields.Many2one("res.company", "Compra", readonly=True,
                                 help="Sucursal matriz, que compra para toda la red.")
    farmacia = fields.Char("Nombre de la sucursal que compra", readonly=True)
    product_id = fields.Many2one("product.product", "Producto", readonly=True)
    product_tmpl_id = fields.Many2one("product.template", "Plantilla", readonly=True)
    producto = fields.Char("Nombre del producto", readonly=True)
    codigo = fields.Char("Código interno", readonly=True)
    demanda_semanal = fields.Float("Venta semanal de la red", readonly=True,
                                   help="Demanda depurada de todas las farmacias más la de la matriz.")
    demanda_propia = fields.Float("Venta semanal de la matriz", readonly=True)
    semanas_cobertura = fields.Integer("Semanas de cobertura", readonly=True)
    faltas_farmacias = fields.Integer("Falta en las farmacias", readonly=True,
                                      help="Suma de lo que le falta a cada farmacia para llegar al objetivo "
                                           "(cobertura + margen de seguridad).")
    farmacias_con_falta = fields.Integer("Farmacias con falta", readonly=True)
    detalle_farmacias = fields.Char("Falta por farmacia", readonly=True,
                                    help="Cuánto le falta a cada farmacia, de mayor a menor.")
    necesidad = fields.Float("Necesidad", readonly=True,
                             help="Falta en las farmacias + venta de la matriz × semanas de cobertura, "
                                  "más el margen de seguridad.")
    stock = fields.Float("Stock libre en la matriz", readonly=True,
                         help="Stock de la matriz menos lo que ya sale hacia las farmacias; el negativo cuenta como 0.")
    pendiente = fields.Float("Compras pendientes", readonly=True, help="De la matriz, por recibir.")
    atrasado = fields.Float("Atrasado", readonly=True, help="Parte de lo pendiente con la fecha prevista vencida.")
    en_solicitud = fields.Float("En solicitudes sin confirmar", readonly=True,
                                help="No se resta: avisa de que ya se está pidiendo.")
    cobertura_actual = fields.Float("Cobertura actual (semanas)", readonly=True,
                                    help="(Stock libre + compras pendientes de la matriz) / venta semanal de la red.")
    faltante = fields.Float("Faltante", readonly=True, help="Necesidad − stock libre − compras pendientes.")
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
    costo = fields.Float("Costo actual", readonly=True, help="Costo del producto en la matriz.")
    revisar_presentacion = fields.Boolean(
        "Revisar presentación", readonly=True,
        help="El precio de la droguería pasa del triple del costo: puede ser otro empaque "
             "con el mismo código de barras (p. ej. un dispensador en vez de un blíster).")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE VIEW ai_v_faltantes AS
            WITH %(red)s
            red AS (
                SELECT f.product_id, sum(f.falta) AS faltas, count(*) AS farmacias,
                       COALESCE(sum(f.falta) FILTER (WHERE f.en_punto), 0) AS urgentes,
                       string_agg(c.name || ' (' || f.falta || ')', ', '
                                  ORDER BY f.falta DESC, c.name) AS detalle
                  FROM reponer f
                  JOIN res_company c ON c.id = f.company_id
                 GROUP BY f.product_id
            ),
            demanda_farmacias AS (
                SELECT product_id, sum(demanda) AS demanda FROM farmacias WHERE demanda > 0 GROUP BY 1
            ),
            propia AS (
                SELECT r.product_id, r.demanda_depurada AS demanda
                  FROM rot r
                 CROSS JOIN params
                 WHERE r.company_id = params.matriz_id AND r.demanda_depurada > 0
            ),
            compra AS (
                SELECT p.product_id,
                       COALESCE(red.faltas, 0) AS faltas,
                       COALESCE(red.farmacias, 0) AS farmacias,
                       COALESCE(red.urgentes, 0) AS urgentes,
                       red.detalle,
                       COALESCE(pr.demanda, 0) AS demanda_propia,
                       COALESCE(df.demanda, 0) + COALESCE(pr.demanda, 0) AS demanda,
                       COALESCE(red.faltas, 0) + COALESCE(%(objetivo_propio)s, 0) AS necesidad,
                       COALESCE(pr.demanda, 0) * params.semanas * params.punto AS punto_propio,
                       greatest(COALESCE(m.libre, 0), 0) AS stock,
                       COALESCE(pd.pendiente, 0) AS pendiente,
                       COALESCE(pd.atrasado, 0) AS atrasado,
                       COALESCE(pd.en_solicitud, 0) AS en_solicitud
                  FROM (SELECT product_id FROM red UNION SELECT product_id FROM propia) p
                 CROSS JOIN params
                  LEFT JOIN red ON red.product_id = p.product_id
                  LEFT JOIN demanda_farmacias df ON df.product_id = p.product_id
                  LEFT JOIN propia pr ON pr.product_id = p.product_id
                  LEFT JOIN matriz m ON m.product_id = p.product_id
                  LEFT JOIN pend pd ON pd.company_id = params.matriz_id AND pd.product_id = p.product_id
            ),
            faltantes AS (
                SELECT c.*, c.necesidad - c.stock - c.pendiente AS faltante,
                       ceil(c.necesidad - c.stock - c.pendiente)::int AS cantidad
                  FROM compra c
                 WHERE c.necesidad - c.stock - c.pendiente > 0
                   -- Se compra cuando, después de mandar a las farmacias que llegaron a su
                   -- punto de pedido, a la matriz le queda su propio punto de pedido o menos;
                   -- y entonces se repone a todas las que están debajo del objetivo.
                   AND (c.urgentes > 0 OR c.punto_propio > 0)
                   AND c.stock + c.pendiente - c.urgentes <= c.punto_propio
            ),
            costos AS (
                -- standard_price depende de la empresa: está en ir_property (el de
                -- la matriz o, si no tiene, el valor por defecto sin empresa).
                SELECT DISTINCT ON (f.product_id) f.product_id, nullif(ip.value_float, 0) AS costo
                  FROM faltantes f
                 CROSS JOIN params
                  JOIN ir_property ip ON ip.name = 'standard_price'
                                     AND ip.res_id = 'product.product,' || f.product_id
                                     AND (ip.company_id = params.matriz_id OR ip.company_id IS NULL)
                 ORDER BY f.product_id, ip.company_id NULLS LAST
            ),
            ofertas AS (
                SELECT f.product_id, o.partner_id, o.drogueria, o.precio_final,
                       o.descuento_pct, o.stock_drogueria, o.fecha_vencimiento, o.laboratorio,
                       o.stock_drogueria >= f.cantidad AS suficiente,
                       o.precio_final > 3 * k.costo AS revisar,
                       o.mejor_precio, o.droguerias_con_stock AS droguerias,
                       row_number() OVER (PARTITION BY f.product_id
                                          ORDER BY o.precio_final > 3 * k.costo IS TRUE,
                                                   o.stock_drogueria >= f.cantidad DESC,
                                                   o.precio_final, o.partner_id, o.id) AS puesto
                  FROM faltantes f
                  JOIN ai_v_oferta o ON o.product_id = f.product_id AND o.disponible
                  LEFT JOIN costos k ON k.product_id = f.product_id
            )
            SELECT f.product_id AS id,
                   params.matriz_id AS company_id,
                   c.name AS farmacia,
                   f.product_id,
                   pp.product_tmpl_id,
                   COALESCE(pt.name->>'es_VE', pt.name->>'en_US') AS producto,
                   pp.default_code AS codigo,
                   round(f.demanda::numeric, 2) AS demanda_semanal,
                   round(f.demanda_propia::numeric, 2) AS demanda_propia,
                   params.semanas AS semanas_cobertura,
                   f.faltas AS faltas_farmacias,
                   f.farmacias AS farmacias_con_falta,
                   f.detalle AS detalle_farmacias,
                   round(f.necesidad::numeric, 2) AS necesidad,
                   round(f.stock::numeric, 2) AS stock,
                   round(f.pendiente::numeric, 2) AS pendiente,
                   round(f.atrasado::numeric, 2) AS atrasado,
                   round(f.en_solicitud::numeric, 2) AS en_solicitud,
                   round(COALESCE((f.stock + f.pendiente) / nullif(f.demanda, 0), 0)::numeric, 2)
                       AS cobertura_actual,
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
              JOIN res_company c ON c.id = params.matriz_id
              JOIN product_product pp ON pp.id = f.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
              LEFT JOIN costos k ON k.product_id = f.product_id
              LEFT JOIN ofertas o ON o.product_id = f.product_id AND o.puesto = 1
             WHERE pp.active AND pt.purchase_ok
        """ % {"red": network_ctes(*network_params_sql()), "objetivo_propio": target_sql("pr.demanda")})
        grant_readonly(self.env, self._table)
