# -*- coding: utf-8 -*-
import logging

from psycopg2 import sql

from odoo import fields, models, tools

from ..services.config import get_setting

_logger = logging.getLogger(__name__)


def grant_readonly(env, view):
    """Da SELECT sobre `view` al rol de solo lectura, si está configurado y existe.

    Hace falta cada vez que se recrea la vista: DROP VIEW se lleva los permisos
    que dio scripts/create_readonly_role.sql.
    """
    role = get_setting(env, "ro_user")
    if not role:
        return
    env.cr.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    if not env.cr.fetchone():
        _logger.warning("El rol de solo lectura %s no existe; no se da acceso a %s.", role, view)
        return
    env.cr.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(view), sql.Identifier(role)))


class AiVOferta(models.Model):
    """Oferta vigente de cada droguería, unida a los productos propios por
    código de barras normalizado.

    Una fila por línea de inventario de droguería (supplier.product.staging);
    product_id queda vacío si no hay producto propio con ese código.

    La comparación es entre droguerías, no entre filas: cada droguería entra
    con su mejor oferta disponible (con stock y precio final > 0) de cada
    código, aunque traiga varios lotes. orden_precio es el puesto de la
    droguería (1 = la más barata; en empate decide el id) y solo lo lleva la
    fila de su mejor oferta; segundo_precio es el de la droguería en el puesto 2.
    """
    _name = "ai.v.oferta"
    _description = "Oferta de droguerías por producto"
    _auto = False
    _order = "codigo_barras, orden_precio, precio_final"
    _rec_name = "descripcion"

    partner_id = fields.Many2one("res.partner", "Droguería", readonly=True)
    drogueria = fields.Char("Nombre de la droguería", readonly=True)
    product_id = fields.Many2one("product.product", "Producto propio", readonly=True)
    product_tmpl_id = fields.Many2one("product.template", "Plantilla", readonly=True)
    producto = fields.Char("Nombre del producto propio", readonly=True)
    codigo_barras = fields.Char("Código de barras", readonly=True,
                                help="Normalizado a EAN-13: es la clave del cruce.")
    codigo_barras_drogueria = fields.Char("Código de barras en el archivo", readonly=True)
    codigo_drogueria = fields.Char("Código en la droguería", readonly=True)
    descripcion = fields.Char("Descripción en la droguería", readonly=True)
    laboratorio = fields.Char("Laboratorio", readonly=True)
    fecha_vencimiento = fields.Date("Vencimiento", readonly=True)
    precio_lista = fields.Float("Precio de lista", readonly=True)
    descuento_pct = fields.Float("% Descuento", readonly=True)
    precio_final = fields.Float("Precio final", readonly=True)
    stock_drogueria = fields.Integer("Stock en la droguería", readonly=True)
    disponible = fields.Boolean("Disponible", readonly=True,
                                help="Con stock y precio final mayor que cero.")
    orden_precio = fields.Integer("Puesto por precio", readonly=True,
                                  help="Puesto de la droguería para ese código: 1 = la más barata. "
                                       "Vacío si la oferta no está disponible o no es la mejor de su droguería.")
    mejor_precio = fields.Float("Mejor precio", readonly=True,
                                help="Precio final más bajo entre las ofertas disponibles de ese código.")
    segundo_precio = fields.Float("Segundo precio", readonly=True,
                                  help="Mejor precio de la segunda droguería más barata para ese código.")
    droguerias_con_stock = fields.Integer("Droguerías con stock", readonly=True)
    fecha_carga = fields.Datetime("Cargado el", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE VIEW ai_v_oferta AS
            WITH productos AS (
                -- Un solo producto propio por código: si hay duplicados, el más antiguo.
                SELECT DISTINCT ON (pp.ai_barcode_norm)
                       pp.ai_barcode_norm, pp.id AS product_id, pp.product_tmpl_id,
                       COALESCE(pt.name->>'es_VE', pt.name->>'en_US') AS producto
                  FROM product_product pp
                  JOIN product_template pt ON pt.id = pp.product_tmpl_id
                 WHERE pp.active AND pp.ai_barcode_norm IS NOT NULL
                 ORDER BY pp.ai_barcode_norm, pp.id
            ),
            oferta AS (
                SELECT s.*,
                       (s.stock_disponible > 0 AND s.precio_unitario_final > 0) AS disponible
                  FROM supplier_product_staging s
            ),
            puestos AS (
                -- Mejor oferta disponible de cada droguería y su puesto por código.
                SELECT ai_barcode_norm, partner_id, precio,
                       row_number() OVER (PARTITION BY ai_barcode_norm
                                          ORDER BY precio, partner_id) AS puesto
                  FROM (SELECT ai_barcode_norm, partner_id, min(precio_unitario_final) AS precio
                          FROM oferta
                         WHERE disponible AND ai_barcode_norm IS NOT NULL
                         GROUP BY ai_barcode_norm, partner_id) mejor_por_drogueria
            ),
            resumen AS (
                SELECT ai_barcode_norm,
                       min(precio) AS mejor_precio,
                       min(precio) FILTER (WHERE puesto = 2) AS segundo_precio,
                       count(*) AS droguerias_con_stock
                  FROM puestos
                 GROUP BY ai_barcode_norm
            )
            SELECT o.id,
                   o.partner_id,
                   rp.name AS drogueria,
                   p.product_id,
                   p.product_tmpl_id,
                   p.producto,
                   o.ai_barcode_norm AS codigo_barras,
                   o.codigo_barras AS codigo_barras_drogueria,
                   o.codigo_producto AS codigo_drogueria,
                   o.descripcion_producto AS descripcion,
                   NULLIF(o.laboratorio, '') AS laboratorio,
                   o.ai_fecha_vencimiento AS fecha_vencimiento,
                   o.precio_unitario AS precio_lista,
                   o.porcentaje_oferta_vigente AS descuento_pct,
                   o.precio_unitario_final AS precio_final,
                   o.stock_disponible AS stock_drogueria,
                   o.disponible,
                   CASE WHEN o.disponible AND o.precio_unitario_final = pu.precio
                        THEN pu.puesto END AS orden_precio,
                   r.mejor_precio,
                   r.segundo_precio,
                   COALESCE(r.droguerias_con_stock, 0) AS droguerias_con_stock,
                   o.create_date AS fecha_carga
              FROM oferta o
              JOIN res_partner rp ON rp.id = o.partner_id
              LEFT JOIN productos p ON p.ai_barcode_norm = o.ai_barcode_norm
              LEFT JOIN puestos pu ON pu.ai_barcode_norm = o.ai_barcode_norm
                                  AND pu.partner_id = o.partner_id
              LEFT JOIN resumen r ON r.ai_barcode_norm = o.ai_barcode_norm
        """)
        grant_readonly(self.env, self._table)
