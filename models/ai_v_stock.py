# -*- coding: utf-8 -*-
from odoo import fields, models, tools

from .ai_v_oferta import grant_readonly


class AiVStock(models.Model):
    """Existencia actual por producto y farmacia.

    Suma los quants de las ubicaciones internas de cada sucursal (todas las de
    sus almacenes), en la unidad del producto. Incluye las filas en 0 y las
    negativas (venta en el TPV sin stock registrado), que también importan
    para comprar.
    """
    _name = "ai.v.stock"
    _description = "Stock por producto y farmacia"
    _auto = False
    _order = "company_id, product_id"
    _rec_name = "product_id"

    company_id = fields.Many2one("res.company", "Farmacia", readonly=True)
    farmacia = fields.Char("Nombre de la farmacia", readonly=True)
    product_id = fields.Many2one("product.product", "Producto", readonly=True)
    product_tmpl_id = fields.Many2one("product.template", "Plantilla", readonly=True)
    producto = fields.Char("Nombre del producto", readonly=True)
    codigo = fields.Char("Código interno", readonly=True)
    stock = fields.Float("Stock", readonly=True, help="Unidades en las ubicaciones internas.")
    reservado = fields.Float("Reservado", readonly=True, help="Unidades reservadas para salidas.")
    disponible = fields.Float("Disponible", readonly=True, help="Stock menos reservado.")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE VIEW ai_v_stock AS
            SELECT min(q.id) AS id,
                   l.company_id,
                   c.name AS farmacia,
                   q.product_id,
                   pp.product_tmpl_id,
                   COALESCE(pt.name->>'es_VE', pt.name->>'en_US') AS producto,
                   pp.default_code AS codigo,
                   sum(q.quantity) AS stock,
                   sum(q.reserved_quantity) AS reservado,
                   sum(q.quantity - q.reserved_quantity) AS disponible
              FROM stock_quant q
              JOIN stock_location l ON l.id = q.location_id
              JOIN res_company c ON c.id = l.company_id
              JOIN product_product pp ON pp.id = q.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
             WHERE l.usage = 'internal'
             GROUP BY l.company_id, c.name, q.product_id, pp.product_tmpl_id, pt.name, pp.default_code
        """)
        grant_readonly(self.env, self._table)
