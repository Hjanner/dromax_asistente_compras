# -*- coding: utf-8 -*-
"""Historial simulado de traslados de la matriz (Dromax) a las farmacias.

Dromax es una droguería: no vende; su demanda es lo que traslada a las
farmacias (ai_v_rotacion.venta_traslados). Este script inventa ese historial
sobre una base que ya tiene el inventario real y las ventas simuladas de las
farmacias (`import_inventory.py` + `simulate_history.py`), para probar la
compra del inventario de Dromax. Se ejecuta dentro de una consola de Odoo,
sobre una copia local desechable:

    SIM_DB=<base> python3 odoo/odoo-bin shell -c <odoo.conf> -d <base> --no-http \\
        < <ruta al módulo>/scripts/simulate_transfers.py

Variables de entorno:
    SIM_DB     obligatoria; tiene que ser igual al nombre de la base.
    SIM_SEED   semilla (por defecto 17): misma semilla, mismos datos.
    SIM_WEEKS  semanas completas de historial (por defecto 26).

Qué hace, por cada producto que las farmacias venden y que Dromax tiene en
existencia (y algunos que ya no tiene):
- Una demanda semanal de traslados: una parte de la venta de la red. Se
  deduce del stock de Dromax para que haya de todo: la mayoría con 1 a 6
  semanas de cobertura, algunos cortos (Dromax tiene que reponerlos), otros
  sobrados, y agotados (Dromax los mandaba y ya no le queda).
- Cada semana, una cantidad Poisson repartida entre las farmacias que lo
  venden (más a la que más vende), con semanas atípicas.
- Dos movimientos hechos por envío, como los traslados entre sucursales:
  stock de Dromax → tránsito y tránsito → stock de la farmacia.

No toca las existencias (stock_quant) ni la contabilidad: el stock sigue
siendo el del Excel. Para repetirlo hay que restaurar la base.
"""
import math
import os
import random
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from psycopg2.extras import execute_values

DB = env.cr.dbname
if os.environ.get('SIM_DB') != DB:
    raise SystemExit('Usa SIM_DB=%s para confirmar que es una base de pruebas desechable.' % DB)
ICP = env['ir.config_parameter'].sudo()
if ICP.get_param('dromax_sim.transfers'):
    raise SystemExit('Ya hay traslados simulados en %s; restaura la base para repetirlo.' % DB)

SEED = int(os.environ.get('SIM_SEED', 17))
WEEKS = int(os.environ.get('SIM_WEEKS', 26))
TODAY = date.today()
MONDAY = TODAY - timedelta(days=TODAY.weekday())       # lunes de la semana en curso
UTC_OFFSET = 4                                          # Caracas = UTC-4
ORIGIN = 'Simulación de traslados'

rng = random.Random(SEED)
cr = env.cr
ADMIN = env.ref('base.user_admin').id


def log(msg):
    print('[traslados] %s' % msg, flush=True)


def poisson(lam):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


from odoo.addons.dromax_asistente_compras.services.network_sql import matrix_company  # noqa: E402

matrix = matrix_company(env)
if not matrix:
    raise SystemExit('No hay sucursal matriz (ajuste o empresa con DROMAX en el nombre).')
warehouses = {w.company_id.id: w for w in env['stock.warehouse'].sudo().search([])}
if matrix.id not in warehouses:
    raise SystemExit('La matriz %s no tiene almacén.' % matrix.name)
src = warehouses[matrix.id].lot_stock_id.id
Location = env['stock.location'].sudo()
transit = Location.search([('usage', '=', 'transit'), ('company_id', '=', False),
                           ('name', 'ilike', 'Inter-Empresas')], limit=1) \
    or Location.search([('usage', '=', 'transit'), ('company_id', '=', False)], limit=1)
if not transit:
    raise SystemExit('No hay ubicación de tránsito entre empresas.')
log('Matriz: %s (stock %s). Tránsito: %s.' % (matrix.name, src, transit.complete_name))

# Venta semanal de cada farmacia (demanda depurada) y stock de Dromax por producto.
cr.execute("""
    SELECT r.product_id, r.company_id, r.demanda_depurada
      FROM ai_v_rotacion r
     WHERE r.company_id <> %s AND r.demanda_depurada > 0
""", (matrix.id,))
sales = defaultdict(dict)
for product_id, company_id, demand in cr.fetchall():
    if company_id in warehouses:
        sales[product_id][company_id] = float(demand)
cr.execute("""
    SELECT q.product_id, sum(q.quantity)
      FROM stock_quant q JOIN stock_location l ON l.id = q.location_id
     WHERE l.usage = 'internal' AND l.company_id = %s
     GROUP BY 1
""", (matrix.id,))
matrix_stock = {p: float(q) for p, q in cr.fetchall()}
cr.execute("SELECT id, uom_id FROM product_template")
tmpl_uom = dict(cr.fetchall())
cr.execute("SELECT id, product_tmpl_id FROM product_product WHERE id = ANY(%s)", (list(sales),))
product_uom = {p: tmpl_uom[t] for p, t in cr.fetchall()}

# Perfil de cada producto según el stock de Dromax: semanas de cobertura que le dan sus traslados.
profiles = defaultdict(int)
plans = {}
for product_id, by_company in sales.items():
    network = sum(by_company.values())
    stock = matrix_stock.get(product_id, 0.0)
    if stock > 0:
        draw = rng.random()
        if draw < 0.20:
            profile, cover = 'corto', rng.uniform(0.15, 0.5)        # tiene que reponer
        elif draw < 0.80:
            profile, cover = 'normal', rng.uniform(1.0, 6.0)
        elif draw < 0.92:
            profile, cover = 'sobrado', rng.uniform(8.0, 20.0)
        else:
            profile, cover = 'sin_traslados', None
        mean = stock / cover if cover else 0.0
        # Que no mande más que lo que vende la red.
        mean = min(mean, network * rng.uniform(0.6, 0.9))
    else:
        # Sin stock: la mayoría nunca la trasladó; algunos se agotaron.
        if rng.random() < 0.06:
            profile, mean = 'agotado', network * rng.uniform(0.2, 0.5)
        else:
            profile, mean = 'sin_traslados', 0.0
    profiles[profile] += 1
    if mean >= 0.2:
        plans[product_id] = (mean, profile, by_company)
log('Perfiles: %s.' % dict(profiles))

now = datetime.now()
moves = []
weeks_out = {}
for product_id, (mean, profile, by_company) in plans.items():
    companies = list(by_company)
    weights = [by_company[c] for c in companies]
    for w in range(WEEKS, 0, -1):
        monday = MONDAY - timedelta(weeks=w)
        lam = mean * (rng.choice([0, 2.5]) if rng.random() < 0.06 else 1)   # semanas atípicas
        if profile == 'agotado' and w <= 2:
            lam = 0                                                          # ya no le queda
        qty = poisson(lam)
        if not qty:
            continue
        shares = defaultdict(int)
        for _i in range(min(qty, 3)):
            shares[rng.choices(companies, weights)[0]] += 1
        # Reparte la cantidad entre 1 a 3 farmacias, proporcional a su venta.
        total_share = sum(shares.values())
        left = qty
        for i, (company_id, share) in enumerate(sorted(shares.items())):
            part = left if i == len(shares) - 1 else int(round(qty * share / total_share))
            part = min(part, left)
            left -= part
            if part <= 0:
                continue
            day = monday + timedelta(days=rng.randint(0, 5))
            when = datetime.combine(day, time(rng.randint(8, 16), rng.randint(0, 59))) + timedelta(hours=UTC_OFFSET)
            moves.append((product_id, company_id, part, when))

log('Envíos: %s (%s productos).' % (len(moves), len(plans)))

rows = []
for product_id, company_id, qty, when in moves:
    uom = product_uom[product_id]
    dest = warehouses[company_id].lot_stock_id.id
    rows.append((matrix.id, product_id, qty, uom, src, transit.id, when))
    rows.append((company_id, product_id, qty, uom, transit.id, dest, when))
execute_values(cr, """
    INSERT INTO stock_move (name, company_id, product_id, product_uom_qty, product_qty, quantity,
                            product_uom, location_id, location_dest_id, date, procure_method, state,
                            origin, reference, picked, is_inventory, scrapped, additional,
                            propagate_cancel, priority, sequence,
                            create_uid, write_uid, create_date, write_date)
    VALUES %s
""", [(ORIGIN, company_id, product_id, qty, qty, qty, uom, location_id, location_dest_id, when,
       'make_to_stock', 'done', ORIGIN, ORIGIN, True, False, False, False, True, '0', 10,
       ADMIN, ADMIN, now, now)
      for company_id, product_id, qty, uom, location_id, location_dest_id, when in rows],
    page_size=5000)

summary = {'envios': len(moves), 'movimientos': len(rows), 'productos': len(plans),
           'perfiles': dict(profiles), 'semilla': SEED, 'semanas': WEEKS,
           'generado': TODAY.isoformat()}
ICP.set_param('dromax_sim.transfers', str(summary))
env.cr.commit()
log('Listo: %s.' % summary)
