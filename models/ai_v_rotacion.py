# -*- coding: utf-8 -*-
import logging
import time

from odoo import api, fields, models, tools

from ..services.config import PARAM_PREFIX, get_int_setting
from ..services.network_sql import MATRIX_SQL
from ..services.prompt import (DEFAULT_COVERAGE_WEEKS, DEFAULT_OUTLIER_MAD, DEFAULT_REORDER_POINT_PCT,
                               DEFAULT_SAFETY_MARGIN_PCT, DEFAULT_WINDOW_WEEKS)
from .ai_v_oferta import grant_readonly

# Hora de Venezuela para cortar las semanas si la sucursal no tiene zona horaria.
DEFAULT_TZ = "America/Caracas"


def _int_param(name, default, maximum, minimum=1):
    """SQL que lee un parámetro entero de Ajustes, con los mismos límites que
    get_int_setting (`minimum`, `maximum`, `default` si falta o no es un número)."""
    return """COALESCE((SELECT least(greatest(trim(value)::int, %d), %d)
                          FROM ir_config_parameter
                         WHERE key = '%s%s' AND trim(value) ~ '^[0-9]{1,4}$'), %d)""" % (
        minimum, maximum, PARAM_PREFIX, name, default)


def network_params_sql():
    """SQL de los parámetros de network_ctes: semanas de cobertura, punto de
    pedido (% de la venta de esas semanas) y margen de seguridad (%)."""
    return (_int_param("coverage_weeks", DEFAULT_COVERAGE_WEEKS, 52),
            _int_param("reorder_point_pct", DEFAULT_REORDER_POINT_PCT, 100),
            _int_param("safety_margin_pct", DEFAULT_SAFETY_MARGIN_PCT, 200, minimum=0))


# --- Demanda guardada --------------------------------------------------------
#
# Calcular la rotación es la parte cara de la necesidad de compra y del
# reabastecimiento (~1,5 s con 26 semanas de TPV, y Odoo consulta la vista
# varias veces por pantalla). Como solo usa semanas completas, no cambia con lo
# que pasa en la semana: se guarda en una vista materializada (ai_mv_rotacion)
# y ai_v_rotacion la lee. Stock, pendientes, traslados en camino y oferta
# siguen en vivo. Se recalcula cuando:
# - cambia la huella: empezó otra semana o cambiaron los ajustes del cálculo
#   (semanas de historial, factor de atípicas, sucursal matriz);
# - pasaron más de rotation_cache_minutes minutos (ventas de semanas pasadas
#   que llegan tarde); 0 = recalcular en cada lectura;
# - lo pide la tarea programada (cada 30 minutos).
_logger = logging.getLogger(__name__)

CACHE_VIEW = "ai_mv_rotacion"
CALC_VIEW = "ai_v_rotacion_calculo"
STATE_TABLE = "ai_rotacion_estado"
CACHE_MINUTES = "rotation_cache_minutes"
DEFAULT_CACHE_MINUTES = 60
# Candado de Postgres para que dos peticiones no recalculen a la vez.
LOCK_KEY = 0x41495254      # «AIRT»


def fingerprint_sql():
    """SQL que da la huella de lo que define la rotación, además de los datos."""
    return """
        SELECT md5(concat_ws('|', %(semanas)s, %(factor)s, %(matriz)s,
               (SELECT string_agg(c.id || ':' || date_trunc(
                           'week', now() AT TIME ZONE COALESCE(NULLIF(p.tz, ''), '%(tz)s'))::date,
                           ',' ORDER BY c.id)
                  FROM res_company c
                  JOIN res_partner p ON p.id = c.partner_id)))
    """ % {
        "semanas": _int_param("rotation_window_weeks", DEFAULT_WINDOW_WEEKS, 104),
        "factor": _int_param("outlier_mad_factor", DEFAULT_OUTLIER_MAD, 20),
        "matriz": MATRIX_SQL,
        "tz": DEFAULT_TZ,
    }


def ensure_rotation_fresh(env, force=False):
    """Recalcula la rotación guardada si hace falta. Devuelve True si la recalculó.

    Va en la misma transacción que la lectura (en REPEATABLE READ no se vería
    lo que otra transacción guarde después). REFRESH ... CONCURRENTLY no
    bloquea a quien la esté leyendo; el candado evita dos cálculos a la vez.
    `force` recalcula aunque esté al día: las acciones que crean documentos
    (propuesta, borradores de traslado) usan siempre la venta exacta.
    """
    cr = env.cr
    cr.execute(fingerprint_sql())
    huella = cr.fetchone()[0]
    minutes = get_int_setting(env, CACHE_MINUTES, DEFAULT_CACHE_MINUTES, 1440, minimum=0)
    if not force and minutes:
        cr.execute("SELECT 1 FROM " + STATE_TABLE
                   + " WHERE huella = %s AND actualizado > now() - %s * interval '1 minute'",
                   (huella, minutes))
        if cr.fetchone():
            return False
    cr.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
    start = time.monotonic()
    cr.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY " + CACHE_VIEW)
    cr.execute("DELETE FROM " + STATE_TABLE)
    cr.execute("INSERT INTO " + STATE_TABLE + " (huella, actualizado) VALUES (%s, now())", (huella,))
    _logger.info("Rotación recalculada en %.1f s.", time.monotonic() - start)
    return True


class AiRotationCacheMixin(models.AbstractModel):
    """Pantallas que leen la rotación: antes de buscar, la ponen al día."""
    _name = "ai.rotation.cache.mixin"
    _description = "Lectura de la rotación guardada"

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        ensure_rotation_fresh(self.env)
        return super()._search(domain, offset=offset, limit=limit, order=order,
                               access_rights_uid=access_rights_uid)


class AiVRotacion(models.Model):
    """Demanda semanal por producto y sucursal en las últimas N semanas completas.

    Farmacias: demanda = ventas del TPV (pedidos pagados, sin los que liquidan
    un pedido de venta, que ya cuenta el propio pedido) + pedidos de venta
    confirmados. Sucursal matriz (Dromax, una droguería que no vende): demanda
    = lo que traslada a las farmacias (movimientos hechos de sus ubicaciones
    internas hacia tránsito o hacia otra sucursal); sus ventas no cuentan.
    Todo en la unidad del producto. Las semanas van de lunes a domingo en la
    hora de la sucursal y la semana en curso no entra. Las semanas sin
    movimiento cuentan como 0.

    Una fila por producto y farmacia con alguna venta en la ventana. La demanda
    depurada es la media sin las semanas atípicas: las que se alejan de la
    mediana más de «factor × MAD» (MAD = desviación absoluta mediana). Si el MAD
    es menor que 1 se usa 1: con ventas esporádicas el MAD es 0 y cualquier
    semana con ventas saldría atípica. La ventana N y el factor se leen de
    Ajustes en cada consulta (por defecto 8 semanas y 3).
    """
    _name = "ai.v.rotacion"
    _inherit = ["ai.rotation.cache.mixin"]
    _description = "Rotación semanal por producto y farmacia"
    _auto = False
    _order = "company_id, demanda_depurada desc, product_id"
    _rec_name = "product_id"

    company_id = fields.Many2one("res.company", "Sucursal", readonly=True)
    farmacia = fields.Char("Nombre de la sucursal", readonly=True)
    product_id = fields.Many2one("product.product", "Producto", readonly=True)
    product_tmpl_id = fields.Many2one("product.template", "Plantilla", readonly=True)
    producto = fields.Char("Nombre del producto", readonly=True)
    codigo = fields.Char("Código interno", readonly=True)
    semanas = fields.Integer("Semanas", readonly=True, help="Semanas completas de la ventana.")
    desde = fields.Date("Desde", readonly=True, help="Lunes de la primera semana de la ventana.")
    hasta = fields.Date("Hasta", readonly=True, help="Domingo de la última semana completa.")
    semanas_con_venta = fields.Integer("Semanas con venta", readonly=True)
    venta_total = fields.Float("Venta total", readonly=True,
                               help="Unidades vendidas en la ventana; en la matriz, las trasladadas a las farmacias.")
    venta_tpv = fields.Float("Venta TPV", readonly=True)
    venta_pedidos = fields.Float("Venta por pedidos", readonly=True,
                                 help="Pedidos de venta: clientes especiales (alcaldía, personal...).")
    venta_traslados = fields.Float("Traslados a farmacias", readonly=True,
                                   help="Solo la matriz: unidades que mandó a las farmacias (su demanda).")
    demanda_media = fields.Float("Demanda media", readonly=True,
                                 help="Media semanal con todas las semanas, atípicas incluidas.")
    demanda_mediana = fields.Float("Mediana semanal", readonly=True)
    desviacion = fields.Float("Desviación estándar", readonly=True)
    mad = fields.Float("MAD", readonly=True, help="Desviación absoluta mediana de las semanas.")
    semanas_atipicas = fields.Integer("Semanas atípicas", readonly=True)
    demanda_depurada = fields.Float("Demanda depurada", readonly=True,
                                    help="Media semanal sin las semanas atípicas: la base de la propuesta.")

    def init(self):
        # Tres capas: el cálculo (vista), lo guardado (vista materializada) y
        # ai_v_rotacion, que lee lo guardado (la usan las demás vistas y el chat).
        cr = self.env.cr
        tools.drop_view_if_exists(cr, self._table)
        cr.execute("DROP MATERIALIZED VIEW IF EXISTS %s CASCADE" % CACHE_VIEW)
        tools.drop_view_if_exists(cr, CALC_VIEW)
        cr.execute("""
            CREATE VIEW ai_v_rotacion_calculo AS
            WITH params AS (
                SELECT %(semanas)s AS semanas, %(factor)s AS factor_mad, %(matriz)s AS matriz_id
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
                       sum(pol.qty) AS tpv, 0 AS pedidos, 0 AS traslados
                  FROM pos_order po
                  JOIN farmacias f ON f.company_id = po.company_id
                  JOIN pos_order_line pol ON pol.order_id = po.id
                 CROSS JOIN params
                 WHERE po.state IN ('paid', 'done', 'invoiced')
                   AND po.company_id IS DISTINCT FROM params.matriz_id
                   AND po.date_order >= (now() AT TIME ZONE 'UTC') - (params.semanas + 2) * interval '1 week'
                   AND pol.sale_order_origin_id IS NULL
                 GROUP BY 1, 2, 3
                UNION ALL
                SELECT so.company_id, sol.product_id,
                       date_trunc('week', so.date_order AT TIME ZONE 'UTC' AT TIME ZONE f.tz)::date,
                       0, sum(sol.product_uom_qty / lu.factor * pu.factor), 0
                  FROM sale_order so
                  JOIN farmacias f ON f.company_id = so.company_id
                  JOIN sale_order_line sol ON sol.order_id = so.id
                  JOIN product_product pp ON pp.id = sol.product_id
                  JOIN product_template pt ON pt.id = pp.product_tmpl_id
                  JOIN uom_uom lu ON lu.id = sol.product_uom
                  JOIN uom_uom pu ON pu.id = pt.uom_id
                 CROSS JOIN params
                 WHERE so.state IN ('sale', 'done')
                   AND so.company_id IS DISTINCT FROM params.matriz_id
                   AND sol.display_type IS NULL
                   AND so.date_order >= (now() AT TIME ZONE 'UTC') - (params.semanas + 2) * interval '1 week'
                 GROUP BY 1, 2, 3
                UNION ALL
                -- La matriz no vende: su demanda es lo que traslada a las farmacias (hacia
                -- tránsito o hacia otra sucursal; los pasos dentro de su almacén no cuentan).
                SELECT ls.company_id, m.product_id,
                       date_trunc('week', m.date AT TIME ZONE 'UTC' AT TIME ZONE f.tz)::date,
                       0, 0, sum(m.product_qty)
                  FROM stock_move m
                 CROSS JOIN params
                  JOIN stock_location ls ON ls.id = m.location_id AND ls.usage = 'internal'
                                        AND ls.company_id = params.matriz_id
                  JOIN stock_location ld ON ld.id = m.location_dest_id
                  JOIN farmacias f ON f.company_id = ls.company_id
                 WHERE m.state = 'done'
                   AND (ld.usage = 'transit' OR (ld.usage = 'internal' AND ld.company_id <> ls.company_id))
                   AND m.date >= (now() AT TIME ZONE 'UTC') - (params.semanas + 2) * interval '1 week'
                 GROUP BY 1, 2, 3
            ),
            semanal AS (
                -- Todas las semanas de la ventana para cada producto vendido: sin venta = 0.
                SELECT p.company_id, p.product_id, w.semana,
                       COALESCE(sum(v.tpv), 0) AS tpv,
                       COALESCE(sum(v.pedidos), 0) AS pedidos,
                       COALESCE(sum(v.traslados), 0) AS traslados,
                       COALESCE(sum(v.tpv + v.pedidos + v.traslados), 0) AS cantidad
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
                       sum(pedidos) AS venta_pedidos,
                       sum(traslados) AS venta_traslados
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
                   round((e.venta_tpv + e.venta_pedidos + e.venta_traslados)::numeric, 2) AS venta_total,
                   round(e.venta_tpv::numeric, 2) AS venta_tpv,
                   round(e.venta_pedidos::numeric, 2) AS venta_pedidos,
                   round(e.venta_traslados::numeric, 2) AS venta_traslados,
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
            "matriz": MATRIX_SQL,
            "tz": DEFAULT_TZ,
        })
        cr.execute("CREATE MATERIALIZED VIEW %s AS SELECT * FROM %s" % (CACHE_VIEW, CALC_VIEW))
        # Índice único: lo pide REFRESH ... CONCURRENTLY (se sigue leyendo mientras se recalcula).
        cr.execute("CREATE UNIQUE INDEX %s_key ON %s (company_id, product_id)" % (CACHE_VIEW, CACHE_VIEW))
        cr.execute("CREATE VIEW ai_v_rotacion AS SELECT * FROM %s" % CACHE_VIEW)
        cr.execute("CREATE TABLE IF NOT EXISTS %s (huella varchar, actualizado timestamptz)" % STATE_TABLE)
        cr.execute("DELETE FROM %s" % STATE_TABLE)
        cr.execute("INSERT INTO %s (huella, actualizado) SELECT (%s), now()" % (STATE_TABLE, fingerprint_sql()))
        grant_readonly(self.env, self._table)

    @api.model
    def _cron_refresh_cache(self):
        """Tarea programada: recalcula la rotación guardada (ver ensure_rotation_fresh)."""
        ensure_rotation_fresh(self.env, force=True)
