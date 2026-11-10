# -*- coding: utf-8 -*-
"""SQL común de la red: la matriz compra y distribuye a las farmacias.

Toda compra entra por la sucursal matriz (Dromax), que reparte a las demás
sucursales (las farmacias) con traslados. Entre farmacias no se traslada.
Las dos vistas que dependen de esto usan las mismas piezas, para que lo que
se compra y lo que se reparte salgan de la misma cuenta:

- ai_v_faltantes: lo que la matriz tiene que comprar para toda la red;
- ai_v_reabastecimiento: lo que la matriz manda a cada farmacia.
"""
from .config import PARAM_PREFIX

MATRIX_PARAM = PARAM_PREFIX + "matrix_company_id"

# Sucursal matriz: la del ajuste si existe; si no, la primera empresa con
# «DROMAX» en el nombre.
MATRIX_SQL = """COALESCE(
    (SELECT c.id FROM res_company c
      WHERE c.id::text = (SELECT trim(value) FROM ir_config_parameter WHERE key = '%s')),
    (SELECT c.id FROM res_company c WHERE c.name ILIKE '%%DROMAX%%' ORDER BY c.id LIMIT 1))""" % MATRIX_PARAM


def matrix_company(env):
    """La sucursal matriz (res.company) según el ajuste o el nombre; vacía si no hay."""
    env.cr.execute("SELECT " + MATRIX_SQL)
    row = env.cr.fetchone()
    return env["res.company"].browse(row[0] if row and row[0] else [])


def network_ctes(coverage_sql):
    """CTE comunes (sin el WITH inicial; terminan en coma).

    - params: semanas de cobertura y matriz_id.
    - rot, stk, pend: rotación, stock y compras pendientes, materializadas
      (se usan varias veces y ai_v_rotacion es la parte cara).
    - en_camino: lo que llega a una ubicación interna de una sucursal desde
      otra o desde tránsito (borradores y traslados sin terminar); no cuenta
      los pasos internos de la propia sucursal ni las recepciones de compra.
    - sale: lo que sale de una ubicación interna hacia tránsito u otra
      sucursal (ya comprometido: no está libre aunque siga en el stock).
    - farmacias: por producto y farmacia (toda sucursal menos la matriz),
      demanda semanal (la depurada; con stock en cero y venta esporádica, la
      media), stock (negativo = 0), compras pendientes y en camino.
    - faltas: las farmacias a las que no les alcanza para la cobertura, con
      la falta en unidades enteras y su cobertura actual en semanas.
    - matriz: stock de la matriz, lo libre (stock − lo que ya sale) y su
      propia demanda semanal.
    """
    return """
            params AS MATERIALIZED (
                SELECT %(cobertura)s AS semanas, %(matriz)s AS matriz_id
            ),
            rot AS MATERIALIZED (
                SELECT company_id, product_id, demanda_depurada, demanda_media FROM ai_v_rotacion
            ),
            stk AS MATERIALIZED (
                SELECT company_id, product_id, stock FROM ai_v_stock
            ),
            pend AS MATERIALIZED (
                SELECT company_id, product_id, pendiente, atrasado, en_solicitud FROM ai_v_pendiente
            ),
            en_camino AS (
                SELECT ld.company_id, m.product_id, sum(m.product_qty) AS cantidad
                  FROM stock_move m
                  JOIN stock_location ld ON ld.id = m.location_dest_id AND ld.usage = 'internal'
                  JOIN stock_location ls ON ls.id = m.location_id
                 WHERE m.state NOT IN ('done', 'cancel')
                   AND (ls.usage = 'transit' OR (ls.usage = 'internal' AND ls.company_id <> ld.company_id))
                 GROUP BY 1, 2
            ),
            sale AS (
                SELECT ls.company_id, m.product_id, sum(m.product_qty) AS cantidad
                  FROM stock_move m
                  JOIN stock_location ls ON ls.id = m.location_id AND ls.usage = 'internal'
                  JOIN stock_location ld ON ld.id = m.location_dest_id
                 WHERE m.state NOT IN ('done', 'cancel')
                   AND (ld.usage = 'transit' OR (ld.usage = 'internal' AND ld.company_id <> ls.company_id))
                 GROUP BY 1, 2
            ),
            farmacias AS (
                SELECT r.company_id, r.product_id,
                       greatest(COALESCE(s.stock, 0), 0) AS stock,
                       COALESCE(p.pendiente, 0) AS pendiente,
                       COALESCE(c.cantidad, 0) AS en_camino,
                       CASE WHEN r.demanda_depurada > 0 THEN r.demanda_depurada
                            WHEN COALESCE(s.stock, 0) <= 0 THEN r.demanda_media
                            ELSE 0 END AS demanda
                  FROM rot r
                 CROSS JOIN params
                  LEFT JOIN stk s ON s.company_id = r.company_id AND s.product_id = r.product_id
                  LEFT JOIN pend p ON p.company_id = r.company_id AND p.product_id = r.product_id
                  LEFT JOIN en_camino c ON c.company_id = r.company_id AND c.product_id = r.product_id
                 WHERE r.company_id <> params.matriz_id
            ),
            faltas AS (
                SELECT f.*,
                       ceil(f.demanda * params.semanas - f.stock - f.pendiente - f.en_camino)::int AS falta,
                       (f.stock + f.pendiente + f.en_camino) / f.demanda AS cobertura
                  FROM farmacias f
                 CROSS JOIN params
                 WHERE f.demanda > 0 AND f.demanda * params.semanas - f.stock - f.pendiente - f.en_camino > 0
            ),
            matriz AS (
                SELECT s.product_id, s.stock,
                       greatest(s.stock, 0) - COALESCE(x.cantidad, 0) AS libre,
                       COALESCE(r.demanda_depurada, 0) AS demanda
                  FROM stk s
                 CROSS JOIN params
                  LEFT JOIN rot r ON r.company_id = s.company_id AND r.product_id = s.product_id
                  LEFT JOIN sale x ON x.company_id = s.company_id AND x.product_id = s.product_id
                 WHERE s.company_id = params.matriz_id
            ),""" % {"cobertura": coverage_sql, "matriz": MATRIX_SQL}
