# -*- coding: utf-8 -*-
from odoo import fields, models, tools

from .ai_v_oferta import grant_readonly


class AiVPendiente(models.Model):
    """Compras pedidas y aún no recibidas, por producto y farmacia.

    Pendiente = órdenes confirmadas (purchase / done) cuya recepción no está
    completa. Por línea: si tiene movimientos de stock, lo que falta por
    entrar en ellos (así no cuenta lo que se cerró sin pendiente al recibir);
    si no tiene (compras importadas o creadas sin recepción), lo pedido menos
    lo recibido. Todo en la unidad del producto.

    Aparte, en_solicitud suma las solicitudes de presupuesto sin confirmar
    (draft / sent / to approve): no descuentan de la propuesta, pero avisan de
    que ya hay algo en camino de pedirse.
    """
    _name = "ai.v.pendiente"
    _description = "Compras pendientes por producto y farmacia"
    _auto = False
    _order = "company_id, proxima_llegada, product_id"
    _rec_name = "product_id"

    company_id = fields.Many2one("res.company", "Farmacia", readonly=True)
    farmacia = fields.Char("Nombre de la farmacia", readonly=True)
    product_id = fields.Many2one("product.product", "Producto", readonly=True)
    product_tmpl_id = fields.Many2one("product.template", "Plantilla", readonly=True)
    producto = fields.Char("Nombre del producto", readonly=True)
    codigo = fields.Char("Código interno", readonly=True)
    pendiente = fields.Float("Pendiente de recibir", readonly=True,
                             help="Unidades de órdenes de compra confirmadas que aún no han llegado.")
    atrasado = fields.Float("Atrasado", readonly=True,
                            help="Parte de lo pendiente cuya fecha prevista ya pasó.")
    ordenes = fields.Integer("Órdenes", readonly=True, help="Órdenes confirmadas con algo pendiente.")
    proxima_llegada = fields.Datetime("Próxima llegada", readonly=True,
                                      help="Fecha prevista más cercana de lo pendiente.")
    en_solicitud = fields.Float("En solicitudes sin confirmar", readonly=True,
                                help="Unidades en solicitudes de presupuesto aún no confirmadas.")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE VIEW ai_v_pendiente AS
            WITH lineas AS (
                SELECT pol.id, po.company_id, pol.product_id, po.id AS order_id, pol.date_planned,
                       po.state IN ('purchase', 'done') AS confirmada,
                       lu.factor AS factor_linea, pu.factor AS factor_producto,
                       pol.product_qty, pol.qty_received
                  FROM purchase_order po
                  JOIN purchase_order_line pol ON pol.order_id = po.id
                  JOIN product_product pp ON pp.id = pol.product_id
                  JOIN product_template pt ON pt.id = pp.product_tmpl_id
                  JOIN uom_uom lu ON lu.id = pol.product_uom
                  JOIN uom_uom pu ON pu.id = pt.uom_id
                 WHERE pol.display_type IS NULL
                   AND (po.state IN ('draft', 'sent', 'to approve')
                        OR (po.state IN ('purchase', 'done')
                            AND po.receipt_status IS DISTINCT FROM 'full'
                            AND pol.product_qty > pol.qty_received))
            ),
            cantidades AS (
                SELECT l.*,
                       CASE WHEN NOT l.confirmada THEN 0
                            WHEN m.movimientos > 0 THEN COALESCE(m.por_entrar, 0)
                            ELSE (l.product_qty - l.qty_received) / l.factor_linea * l.factor_producto
                       END AS por_recibir,
                       CASE WHEN l.confirmada THEN 0
                            ELSE l.product_qty / l.factor_linea * l.factor_producto
                       END AS solicitado
                  FROM lineas l
                  LEFT JOIN LATERAL (
                      -- product_qty del movimiento ya va en la unidad del producto.
                      SELECT count(*) AS movimientos,
                             sum(sm.product_qty) FILTER (WHERE sm.state NOT IN ('done', 'cancel')
                                                           AND dest.usage = 'internal') AS por_entrar
                        FROM stock_move sm
                        JOIN stock_location dest ON dest.id = sm.location_dest_id
                       WHERE sm.purchase_line_id = l.id
                  ) m ON l.confirmada
            ),
            resumen AS (
                SELECT company_id, product_id,
                       sum(por_recibir) AS pendiente,
                       sum(por_recibir) FILTER (WHERE date_planned < now() AT TIME ZONE 'UTC') AS atrasado,
                       count(DISTINCT order_id) FILTER (WHERE por_recibir > 0) AS ordenes,
                       min(date_planned) FILTER (WHERE por_recibir > 0) AS proxima_llegada,
                       sum(solicitado) AS en_solicitud
                  FROM cantidades
                 GROUP BY 1, 2
                HAVING sum(por_recibir) > 0 OR sum(solicitado) > 0
            )
            SELECT row_number() OVER (ORDER BY r.company_id, r.product_id) AS id,
                   r.company_id,
                   c.name AS farmacia,
                   r.product_id,
                   pp.product_tmpl_id,
                   COALESCE(pt.name->>'es_VE', pt.name->>'en_US') AS producto,
                   pp.default_code AS codigo,
                   round(r.pendiente::numeric, 2) AS pendiente,
                   round(COALESCE(r.atrasado, 0)::numeric, 2) AS atrasado,
                   r.ordenes,
                   r.proxima_llegada,
                   round(r.en_solicitud::numeric, 2) AS en_solicitud
              FROM resumen r
              JOIN res_company c ON c.id = r.company_id
              JOIN product_product pp ON pp.id = r.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
        """)
        grant_readonly(self.env, self._table)
