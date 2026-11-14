# -*- coding: utf-8 -*-
"""SQL común de la red: la matriz reabastece a las farmacias y cada una compra lo demás.

La sucursal matriz (Dromax) es una droguería: no vende. Guarda inventario para
reabastecer a las farmacias con traslados cuando llegan a su punto de pedido;
lo que no alcanza a mandar lo compra cada farmacia a su nombre. Su propio
inventario se repone con compras de la matriz que salen a nombre de una
farmacia (ajuste matrix_purchase_company_id), porque la droguería no compra a
su nombre. Entre farmacias no se traslada. Las dos vistas que dependen de esto
usan las mismas piezas, para que lo que se compra y lo que se reparte salgan de
la misma cuenta:

- ai_v_faltantes: lo que compra cada farmacia y lo que compra la matriz;
- ai_v_reabastecimiento: lo que la matriz manda a cada farmacia.
"""
from .config import PARAM_PREFIX

MATRIX_PARAM = PARAM_PREFIX + "matrix_company_id"
PURCHASE_NAME_PARAM = PARAM_PREFIX + "matrix_purchase_company_id"

# Sucursal matriz: la del ajuste si existe; si no, la primera empresa con
# «DROMAX» en el nombre.
MATRIX_SQL = """COALESCE(
    (SELECT c.id FROM res_company c
      WHERE c.id::text = (SELECT trim(value) FROM ir_config_parameter WHERE key = '%s')),
    (SELECT c.id FROM res_company c WHERE c.name ILIKE '%%DROMAX%%' ORDER BY c.id LIMIT 1))""" % MATRIX_PARAM


# Farmacia a cuyo nombre salen las compras del inventario de la matriz (vacío si no hay).
PURCHASE_NAME_SQL = """(SELECT c.id FROM res_company c
      WHERE c.id::text = (SELECT trim(value) FROM ir_config_parameter WHERE key = '%s'))""" % PURCHASE_NAME_PARAM


def matrix_company(env):
    """La sucursal matriz (res.company) según el ajuste o el nombre; vacía si no hay."""
    env.cr.execute("SELECT " + MATRIX_SQL)
    row = env.cr.fetchone()
    return env["res.company"].browse(row[0] if row and row[0] else [])


def target_sql(demand, params="params"):
    """SQL del objetivo de stock para una demanda semanal: la venta de las
    semanas de cobertura más el margen de seguridad, en unidades enteras.

    Al menos la venta de las semanas redondeada hacia arriba; el margen se
    redondea al entero más cercano para que en productos de poca venta no
    duplique el pedido (1/sem con 20 % → 1, no 2).
    """
    return ("greatest(ceil(%(d)s * %(p)s.semanas), round(%(d)s * %(p)s.semanas * (1 + %(p)s.margen)))"
            % {"d": demand, "p": params})


def network_ctes(coverage_sql, reorder_sql, margin_sql):
    """CTE comunes (sin el WITH inicial; terminan en coma).

    - params: semanas de cobertura, punto de pedido y margen de seguridad
      (como fracción: 50 % → 0,5) y matriz_id.
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
    - reponer: las farmacias por debajo del objetivo (target_sql), con lo que
      les falta para llegar en unidades enteras, su cobertura actual en
      semanas y si ya llegaron al punto de pedido (stock + pendiente + en
      camino ≤ venta de las semanas × punto).
    - faltas: las de reponer que llegaron al punto de pedido: son las que se
      reabastecen y las que disparan la compra. Así no se mueve una o dos
      unidades debajo de la venta, y lo que se manda deja un margen para
      semanas altas.
    - matriz: por producto, stock de la matriz, lo libre (stock − lo que ya
      sale), sus compras pendientes, lo que le llega en camino y su demanda
      semanal: lo que traslada a las farmacias (ai_v_rotacion; con la misma
      regla de venta esporádica que las farmacias).
    - envios: lo que la matriz manda a cada farmacia de faltas, repartiendo
      todo lo libre (no vende: su inventario es para las farmacias), primero a
      la más urgente (menor cobertura; a igualdad, la que más vende).
    """
    return """
            params AS MATERIALIZED (
                SELECT %(cobertura)s AS semanas, %(punto)s / 100.0 AS punto,
                       %(margen)s / 100.0 AS margen, %(matriz)s AS matriz_id
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
            reponer AS (
                SELECT f.*,
                       ceil(%(objetivo)s - f.stock - f.pendiente - f.en_camino)::int AS falta,
                       (f.stock + f.pendiente + f.en_camino) / f.demanda AS cobertura,
                       f.stock + f.pendiente + f.en_camino <= f.demanda * params.semanas * params.punto
                           AS en_punto
                  FROM farmacias f
                 CROSS JOIN params
                 WHERE f.demanda > 0 AND %(objetivo)s - f.stock - f.pendiente - f.en_camino > 0
            ),
            faltas AS (
                SELECT * FROM reponer WHERE en_punto
            ),
            matriz AS (
                SELECT p.product_id, COALESCE(s.stock, 0) AS stock,
                       greatest(COALESCE(s.stock, 0), 0) - COALESCE(x.cantidad, 0) AS libre,
                       COALESCE(pd.pendiente, 0) AS pendiente,
                       COALESCE(c.cantidad, 0) AS en_camino,
                       CASE WHEN r.demanda_depurada > 0 THEN r.demanda_depurada
                            WHEN COALESCE(s.stock, 0) <= 0 THEN COALESCE(r.demanda_media, 0)
                            ELSE 0 END AS demanda
                  FROM (SELECT product_id FROM stk, params WHERE company_id = params.matriz_id
                        UNION
                        SELECT product_id FROM rot, params WHERE company_id = params.matriz_id) p
                 CROSS JOIN params
                  LEFT JOIN stk s ON s.company_id = params.matriz_id AND s.product_id = p.product_id
                  LEFT JOIN rot r ON r.company_id = params.matriz_id AND r.product_id = p.product_id
                  LEFT JOIN sale x ON x.company_id = params.matriz_id AND x.product_id = p.product_id
                  LEFT JOIN pend pd ON pd.company_id = params.matriz_id AND pd.product_id = p.product_id
                  LEFT JOIN en_camino c ON c.company_id = params.matriz_id AND c.product_id = p.product_id
            ),
            reparto AS (
                SELECT f.*, m.stock AS stock_matriz, floor(m.libre) AS disponible,
                       sum(f.falta) OVER (PARTITION BY f.product_id
                                          ORDER BY f.cobertura, f.demanda DESC, f.company_id
                                          ROWS UNBOUNDED PRECEDING) AS acumulado
                  FROM faltas f
                  JOIN matriz m ON m.product_id = f.product_id AND m.libre >= 1
            ),
            envios AS (
                SELECT r.*, least(r.falta, greatest(r.disponible - (r.acumulado - r.falta), 0))::int AS cantidad
                  FROM reparto r
            ),""" % {"cobertura": coverage_sql, "punto": reorder_sql, "margen": margin_sql,
                     "matriz": MATRIX_SQL, "objetivo": target_sql("f.demanda")}
