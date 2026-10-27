# -*- coding: utf-8 -*-
from odoo import fields, models, tools

from ..services.config import PARAM_PREFIX
from ..services.prompt import DEFAULT_OUTLIER_MAD, DEFAULT_WINDOW_WEEKS
from .ai_v_oferta import grant_readonly

# Hora de Venezuela para cortar las semanas si la sucursal no tiene zona horaria.
DEFAULT_TZ = "America/Caracas"


def _int_param(name, default, maximum):
    """SQL que lee un parámetro entero de Ajustes, con los mismos límites que
    get_int_setting (mínimo 1, máximo `maximum`, `default` si falta o no es un número)."""
    return """COALESCE((SELECT least(greatest(trim(value)::int, 1), %d)
                          FROM ir_config_parameter
                         WHERE key = '%s%s' AND trim(value) ~ '^[0-9]{1,4}$'), %d)""" % (
        maximum, PARAM_PREFIX, name, default)


class AiVRotacion(models.Model):
    """Demanda semanal por producto y farmacia en las últimas N semanas completas.

    Demanda = ventas del TPV (pedidos pagados, sin los que liquidan un pedido de
    venta, que ya cuenta el propio pedido) + pedidos de venta confirmados, en la
    unidad del producto. Las semanas van de lunes a domingo en la hora de la
    sucursal y la semana en curso no entra. Las semanas sin ventas cuentan como 0.

    Una fila por producto y farmacia con alguna venta en la ventana. La demanda
    depurada es la media sin las semanas atípicas: las que se alejan de la
    mediana más de «factor × MAD» (MAD = desviación absoluta mediana). Si el MAD
    es menor que 1 se usa 1: con ventas esporádicas el MAD es 0 y cualquier
    semana con ventas saldría atípica. La ventana N y el factor se leen de
    Ajustes en cada consulta (por defecto 8 semanas y 3).
    """
    _name = "ai.v.rotacion"
    _description = "Rotación semanal por producto y farmacia"
    _auto = False
    _order = "company_id, demanda_depurada desc, product_id"
    _rec_name = "product_id"

    company_id = fields.Many2one("res.company", "Farmacia", readonly=True)
    farmacia = fields.Char("Nombre de la farmacia", readonly=True)
    product_id = fields.Many2one("product.product", "Producto", readonly=True)
    product_tmpl_id = fields.Many2one("product.template", "Plantilla", readonly=True)
    producto = fields.Char("Nombre del producto", readonly=True)
    codigo = fields.Char("Código interno", readonly=True)
    semanas = fields.Integer("Semanas", readonly=True, help="Semanas completas de la ventana.")
    desde = fields.Date("Desde", readonly=True, help="Lunes de la primera semana de la ventana.")
    hasta = fields.Date("Hasta", readonly=True, help="Domingo de la última semana completa.")
    semanas_con_venta = fields.Integer("Semanas con venta", readonly=True)
    venta_total = fields.Float("Venta total", readonly=True, help="Unidades vendidas en la ventana.")
    venta_tpv = fields.Float("Venta TPV", readonly=True)
    venta_pedidos = fields.Float("Venta por pedidos", readonly=True,
                                 help="Pedidos de venta: clientes especiales (alcaldía, personal...).")
    demanda_media = fields.Float("Demanda media", readonly=True,
                                 help="Media semanal con todas las semanas, atípicas incluidas.")
    demanda_mediana = fields.Float("Mediana semanal", readonly=True)
    desviacion = fields.Float("Desviación estándar", readonly=True)
    mad = fields.Float("MAD", readonly=True, help="Desviación absoluta mediana de las semanas.")
    semanas_atipicas = fields.Integer("Semanas atípicas", readonly=True)
    demanda_depurada = fields.Float("Demanda depurada", readonly=True,
                                    help="Media semanal sin las semanas atípicas: la base de la propuesta.")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE VIEW ai_v_rotacion AS
            WITH params AS (
                SELECT %(semanas)s AS semanas, %(factor)s AS factor_mad
            ),
            farmacias AS (
                SELECT c.id AS company_id, c.name AS farmacia, tz.tz,
                       date_trunc('week', now() AT TIME ZONE tz.tz)::date AS semana_actual
                  FROM res_company c
                  JOIN res_partner p ON p.id = c.partner_id
                 CROSS JOIN LATERAL (SELECT COALESCE(NULLIF(p.tz, ''), '%(tz)s') AS tz) tz
            ),
            ventana AS (
                SELECT f.company_id, (f.semana_actual - 7 * n)::date AS semana
                  FROM farmacias f
                 CROSS JOIN params
                 CROSS JOIN generate_series(1, params.semanas) AS n
            ),
            ventas AS (
                -- Por semana local; el filtro por fecha solo recorta, la ventana exacta la pone el JOIN.
                SELECT po.company_id, pol.product_id,
                       date_trunc('week', po.date_order AT TIME ZONE 'UTC' AT TIME ZONE f.tz)::date AS semana,
                       sum(pol.qty) AS tpv, 0 AS pedidos
                  FROM pos_order po
                  JOIN farmacias f ON f.company_id = po.company_id
                  JOIN pos_order_line pol ON pol.order_id = po.id
                 CROSS JOIN params
                 WHERE po.state IN ('paid', 'done', 'invoiced')
                   AND po.date_order >= (now() AT TIME ZONE 'UTC') - (params.semanas + 2) * interval '1 week'
                   AND pol.sale_order_origin_id IS NULL
                 GROUP BY 1, 2, 3
                UNION ALL
                SELECT so.company_id, sol.product_id,
                       date_trunc('week', so.date_order AT TIME ZONE 'UTC' AT TIME ZONE f.tz)::date,
                       0, sum(sol.product_uom_qty / lu.factor * pu.factor)
                  FROM sale_order so
                  JOIN farmacias f ON f.company_id = so.company_id
                  JOIN sale_order_line sol ON sol.order_id = so.id
                  JOIN product_product pp ON pp.id = sol.product_id
                  JOIN product_template pt ON pt.id = pp.product_tmpl_id
                  JOIN uom_uom lu ON lu.id = sol.product_uom
                  JOIN uom_uom pu ON pu.id = pt.uom_id
                 CROSS JOIN params
                 WHERE so.state IN ('sale', 'done')
                   AND sol.display_type IS NULL
                   AND so.date_order >= (now() AT TIME ZONE 'UTC') - (params.semanas + 2) * interval '1 week'
                 GROUP BY 1, 2, 3
            ),
            semanal AS (
                -- Todas las semanas de la ventana para cada producto vendido: sin venta = 0.
                SELECT p.company_id, p.product_id, w.semana,
                       COALESCE(sum(v.tpv), 0) AS tpv,
                       COALESCE(sum(v.pedidos), 0) AS pedidos,
                       COALESCE(sum(v.tpv + v.pedidos), 0) AS cantidad
                  FROM (SELECT DISTINCT v.company_id, v.product_id
                          FROM ventas v
                          JOIN ventana w ON w.company_id = v.company_id AND w.semana = v.semana) p
                  JOIN ventana w ON w.company_id = p.company_id
                  LEFT JOIN ventas v ON v.company_id = p.company_id AND v.product_id = p.product_id
                                    AND v.semana = w.semana
                 GROUP BY 1, 2, 3
            ),
            estadisticas AS (
                SELECT company_id, product_id,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY cantidad) AS mediana,
                       avg(cantidad) AS media,
                       stddev_pop(cantidad) AS desviacion,
                       count(*) FILTER (WHERE cantidad <> 0) AS semanas_con_venta,
                       sum(tpv) AS venta_tpv,
                       sum(pedidos) AS venta_pedidos
                  FROM semanal
                 GROUP BY 1, 2
            ),
            dispersion AS (
                SELECT s.company_id, s.product_id,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(s.cantidad - e.mediana)) AS mad
                  FROM semanal s
                  JOIN estadisticas e ON e.company_id = s.company_id AND e.product_id = s.product_id
                 GROUP BY 1, 2
            ),
            depurada AS (
                -- Con factor >= 1 siempre queda al menos la mitad de las semanas.
                SELECT s.company_id, s.product_id,
                       count(*) FILTER (WHERE abs(s.cantidad - e.mediana) > u.umbral) AS semanas_atipicas,
                       avg(s.cantidad) FILTER (WHERE abs(s.cantidad - e.mediana) <= u.umbral) AS demanda
                  FROM semanal s
                  JOIN estadisticas e ON e.company_id = s.company_id AND e.product_id = s.product_id
                  JOIN dispersion d ON d.company_id = s.company_id AND d.product_id = s.product_id
                 CROSS JOIN params
                 CROSS JOIN LATERAL (SELECT params.factor_mad * greatest(d.mad, 1) AS umbral) u
                 GROUP BY 1, 2
            )
            SELECT row_number() OVER (ORDER BY e.company_id, e.product_id) AS id,
                   e.company_id,
                   f.farmacia,
                   e.product_id,
                   pp.product_tmpl_id,
                   COALESCE(pt.name->>'es_VE', pt.name->>'en_US') AS producto,
                   pp.default_code AS codigo,
                   params.semanas,
                   (f.semana_actual - 7 * params.semanas)::date AS desde,
                   (f.semana_actual - 1)::date AS hasta,
                   e.semanas_con_venta,
                   round((e.venta_tpv + e.venta_pedidos)::numeric, 2) AS venta_total,
                   round(e.venta_tpv::numeric, 2) AS venta_tpv,
                   round(e.venta_pedidos::numeric, 2) AS venta_pedidos,
                   round(e.media::numeric, 2) AS demanda_media,
                   round(e.mediana::numeric, 2) AS demanda_mediana,
                   round(e.desviacion::numeric, 2) AS desviacion,
                   round(d.mad::numeric, 2) AS mad,
                   dp.semanas_atipicas,
                   round(dp.demanda::numeric, 2) AS demanda_depurada
              FROM estadisticas e
              JOIN dispersion d ON d.company_id = e.company_id AND d.product_id = e.product_id
              JOIN depurada dp ON dp.company_id = e.company_id AND dp.product_id = e.product_id
              JOIN farmacias f ON f.company_id = e.company_id
              JOIN product_product pp ON pp.id = e.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
             CROSS JOIN params
        """ % {
            "semanas": _int_param("rotation_window_weeks", DEFAULT_WINDOW_WEEKS, 104),
            "factor": _int_param("outlier_mad_factor", DEFAULT_OUTLIER_MAD, 20),
            "tz": DEFAULT_TZ,
        })
        grant_readonly(self.env, self._table)
