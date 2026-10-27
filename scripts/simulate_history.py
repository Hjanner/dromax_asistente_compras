# -*- coding: utf-8 -*-
"""Historial simulado sobre el inventario real (productos y existencias reales).

Complementa `import_inventory.py`: con el inventario ya cargado, inventa unos
meses de ventas y compras que sean coherentes con las existencias de cada
farmacia, para probar el agente con productos que sí coinciden con la oferta
de las droguerías. Se ejecuta dentro de una consola de Odoo, sobre una copia
local desechable:

    SIM_DB=<base> python3 odoo/odoo-bin shell -c <odoo.conf> -d <base> --no-http \\
        < <ruta al módulo>/scripts/simulate_history.py

Variables de entorno:
    SIM_DB     obligatoria; tiene que ser igual al nombre de la base.
    SIM_SEED   semilla (por defecto 17): misma semilla, mismos datos.
    SIM_WEEKS  semanas completas de historial (por defecto 26), más los días
               ya transcurridos de la semana en curso.

Qué hace, en cada farmacia con existencias:
- Una demanda semanal por producto que se deduce de su stock real: la mayoría
  tiene entre 1 y 6 semanas de cobertura; algunos van cortos (hay que
  pedirlos), otros sobrados, sin movimiento o recién lanzados.
- Productos agotados: tienen stock en otras farmacias, se vendían aquí y ya
  no queda (sin existencia).
- Ventas por TPV (un ticket por farmacia y día), con semanas atípicas (picos
  y semanas en cero), y pedidos de venta grandes a clientes especiales.
- Compras: recibidas en el historial, pendientes de recibir (algunas
  atrasadas o con recepción parcial) y solicitudes en borrador. La droguería
  y el precio salen de la oferta FTP cuando el producto la tiene.

No toca las existencias (stock_quant): el stock sigue siendo el del Excel. El
historial se escribe con SQL, sin movimientos de inventario ni contabilidad.
Para repetirlo hay que restaurar la base.
"""
import math
import os
import random
import statistics
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from psycopg2.extras import execute_values

DB = env.cr.dbname
if os.environ.get('SIM_DB') != DB:
    raise SystemExit('Usa SIM_DB=%s para confirmar que es una base de pruebas desechable.' % DB)
ICP = env['ir.config_parameter'].sudo()
if ICP.get_param('dromax_sim.generated'):
    raise SystemExit('Ya hay historial simulado en %s; restaura la base para repetirlo.' % DB)

SEED = int(os.environ.get('SIM_SEED', 17))
WEEKS = int(os.environ.get('SIM_WEEKS', 26))
TODAY = date.today()
MONDAY = TODAY - timedelta(days=TODAY.weekday())       # lunes de la semana en curso
START = MONDAY - timedelta(weeks=WEEKS)
DAYS = (TODAY - START).days                             # hasta ayer
UTC_OFFSET = 4                                          # Caracas = UTC-4

rng = random.Random(SEED)
cr = env.cr
ADMIN = env.ref('base.user_admin').id
UOM_UNIT = env.ref('uom.product_uom_unit').id


def log(msg):
    print('[simulacion] %s' % msg, flush=True)


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


_columns = {}


def insert(table, rows):
    """Inserta diccionarios en bloque (solo las columnas que existen) y
    devuelve los ids nuevos."""
    if not rows:
        return []
    if table not in _columns:
        cr.execute('SELECT column_name FROM information_schema.columns WHERE table_name = %s', (table,))
        _columns[table] = {r[0] for r in cr.fetchall()}
    keys = [k for k in rows[0] if k in _columns[table]]
    sql = 'INSERT INTO %s (%s) VALUES %%s RETURNING id' % (table, ', '.join(keys))
    res = execute_values(cr._obj, sql, [tuple(r[k] for k in keys) for r in rows],
                         page_size=2000, fetch=True)
    return [r[0] for r in res]


def stamp(d, local_hour=14):
    """Hora local de Caracas guardada en UTC, como lo hace Odoo."""
    return datetime.combine(d, time(local_hour)) + timedelta(hours=UTC_OFFSET)


def audit(dt):
    return {'create_uid': ADMIN, 'write_uid': ADMIN, 'create_date': dt, 'write_date': dt}


# ---------------------------------------------------------------- datos reales

cr.execute("""
    SELECT q.company_id, q.product_id, sum(q.quantity)
      FROM stock_quant q JOIN stock_location l ON l.id = q.location_id
     WHERE l.usage = 'internal'
     GROUP BY 1, 2 HAVING sum(q.quantity) > 0
""")
stock = defaultdict(dict)
for company_id, product_id, qty in cr.fetchall():
    stock[company_id][product_id] = qty

warehouses = {w.company_id.id: w for w in env['stock.warehouse'].search([])}
pos_configs = {}
for cfg in env['pos.config'].search([], order='id'):
    pos_configs.setdefault(cfg.company_id.id, cfg)
branches = env['res.company'].browse(sorted(c for c in stock if c in warehouses and c in pos_configs))
log('base=%s semilla=%s semanas=%s desde=%s farmacias=%s'
    % (DB, SEED, WEEKS, START, ', '.join(branches.mapped('name'))))

all_ids = sorted({pid for per in stock.values() for pid in per})
products = env['product.product'].browse(all_ids)
info = {p['id']: p for p in products.read(['display_name', 'list_price'])}
cost = {}
for branch in branches:
    for p in products.with_company(branch).read(['standard_price']):
        cost[(branch.id, p['id'])] = p['standard_price']

# Oferta FTP: precio final de cada droguería por producto.
cr.execute("""
    SELECT product_id, partner_id, min(precio_final)
      FROM ai_v_oferta WHERE product_id IS NOT NULL AND precio_final > 0
     GROUP BY 1, 2
""")
offer = defaultdict(list)
for product_id, partner_id, price in cr.fetchall():
    offer[product_id].append((float(price), partner_id))
for options in offer.values():
    options.sort()
cr.execute('SELECT DISTINCT partner_id FROM supplier_product_staging')
suppliers = [r[0] for r in cr.fetchall()]
if not suppliers:
    raise SystemExit('No hay oferta de droguerías en la base: cópiala antes de simular.')
log('productos con stock=%s, con oferta=%s, droguerías=%s'
    % (len(all_ids), sum(1 for pid in all_ids if pid in offer), len(suppliers)))


def unit_cost(branch_id, pid):
    c = cost.get((branch_id, pid)) or 0
    return c if c > 0 else round((info[pid]['list_price'] or 1) * 0.7, 2)


def sale_price(branch_id, pid):
    return info[pid]['list_price'] or round(unit_cost(branch_id, pid) * 1.35, 2)


def supplier_for(pid, branch_id):
    """Droguería y precio: la más barata casi siempre, a veces otra."""
    options = offer.get(pid)
    if options:
        price, partner = options[0] if rng.random() < 0.75 else rng.choice(options)
        return partner, round(price, 2)
    return rng.choice(suppliers), round(unit_cost(branch_id, pid) * rng.uniform(0.95, 1.05), 2)


# ---------------------------------------------------------------- demanda

def pick_profile():
    r = rng.random()
    for name, share in (('normal', 0.70), ('corto', 0.12), ('sobrado', 0.10), ('muerto', 0.05)):
        if r < share:
            return name
        r -= share
    return 'nuevo'


demand = {}   # (company, product) -> perfil y demanda semanal base
for branch in branches:
    for pid, qty in stock[branch.id].items():
        profile = pick_profile()
        cover = {'normal': lambda: min(12.0, max(0.7, rng.lognormvariate(math.log(3), 0.6))),
                 'corto': lambda: rng.uniform(0.3, 1.0),
                 'sobrado': lambda: rng.uniform(12, 40),
                 'muerto': lambda: rng.uniform(4, 12),
                 'nuevo': lambda: rng.uniform(1, 4)}[profile]()
        demand[(branch.id, pid)] = {'profile': profile, 'base': min(qty / cover, 400.0),
                                    'launch': WEEKS - rng.randint(3, 6), 'death': WEEKS - rng.randint(10, 16)}

# Agotados: productos que otras farmacias tienen y aquí se vendían, pero ya no hay.
for branch in branches:
    elsewhere = defaultdict(list)
    for (company_id, pid), d in demand.items():
        if company_id != branch.id and d['profile'] in ('normal', 'corto'):
            elsewhere[pid].append(d['base'])
    candidates = [pid for pid, bases in elsewhere.items() if len(bases) >= 2 and pid not in stock[branch.id]]
    for pid in rng.sample(candidates, min(len(candidates), len(stock[branch.id]) * 4 // 100)):
        demand[(branch.id, pid)] = {'profile': 'agotado',
                                    'base': statistics.median(elsewhere[pid]) * rng.uniform(0.6, 1.2),
                                    'out': WEEKS - rng.randint(0, 2)}


def weekly_qty(d, w, days=7):
    """Venta de la semana w (0 = la más antigua; WEEKS = la semana en curso)."""
    profile = d['profile']
    if profile == 'muerto' and w >= d['death']:
        return 0
    if profile == 'nuevo' and w < d['launch']:
        return 0
    if profile == 'agotado' and w >= d['out']:
        return 0
    lam = d['base'] * traffic[w] * days / 7.0
    if profile in ('normal', 'corto', 'sobrado') and w < WEEKS:
        r = rng.random()
        if r < 0.03:
            lam *= rng.uniform(3, 6)       # pico: semana atípica por arriba
        elif r < 0.06:
            return 0                       # semana atípica en cero
    return poisson(lam)


traffic = [rng.gauss(1.0, 0.08) * (1.15 if (START + timedelta(weeks=w)).month == 12 else 1.0)
           for w in range(WEEKS + 1)]
partial_days = TODAY.weekday()                          # días ya cerrados de la semana en curso

# ---------------------------------------------------------------- ventas por TPV

totals = defaultdict(int)
sold = defaultdict(lambda: defaultdict(float))          # company -> (pid, semana) -> unidades
for branch in branches:
    cfg = pos_configs[branch.id]
    session_id = insert('pos_session', [dict(
        name='%s/%s/SIM' % (cfg.name, branch.id), config_id=cfg.id, user_id=ADMIN, state='closed',
        company_id=branch.id, start_at=stamp(START, 8), stop_at=stamp(TODAY - timedelta(days=1), 20),
        sequence_number=DAYS + 1, login_number=1, **audit(stamp(START, 8)))])[0]

    by_day = defaultdict(list)
    for (company_id, pid), d in demand.items():
        if company_id != branch.id:
            continue
        for w in range(WEEKS + 1):
            days = 7 if w < WEEKS else partial_days
            if not days:
                continue
            qty = weekly_qty(d, w, days)
            if not qty:
                continue
            sold[branch.id][(pid, w)] += qty
            # Repartida en hasta 3 días de la semana.
            parts = min(qty, rng.randint(1, 3))
            for i in range(parts):
                part = qty // parts + (1 if i < qty % parts else 0)
                by_day[w * 7 + rng.randrange(days)].append((pid, part))

    order_rows, order_lines = [], []
    for n, day in enumerate(sorted(by_day), start=1):
        dt = stamp(START + timedelta(days=day), rng.randint(9, 19))
        lines = []
        for pid, qty in by_day[day]:
            price = sale_price(branch.id, pid)
            lines.append(dict(product_id=pid, qty=qty, price=price, cost=unit_cost(branch.id, pid)))
        total = round(sum(l['qty'] * l['price'] for l in lines), 2)
        order_rows.append(dict(
            name='SIM%s/%05d' % (branch.id, n), pos_reference='Simulado %s-%05d' % (cfg.id, n),
            company_id=branch.id, session_id=session_id, config_id=cfg.id, user_id=ADMIN,
            date_order=dt, state='done', amount_tax=0.0, amount_total=total, amount_paid=total,
            amount_return=0.0, sequence_number=n, uuid=str(uuid.uuid4()), **audit(dt)))
        order_lines.append(lines)
    order_ids = insert('pos_order', order_rows)
    line_rows = []
    for oid, lines, orow in zip(order_ids, order_lines, order_rows):
        for l in lines:
            sub = round(l['qty'] * l['price'], 2)
            name = info[l['product_id']]['display_name']
            line_rows.append(dict(
                order_id=oid, company_id=branch.id, product_id=l['product_id'], name=name,
                full_product_name=name, qty=l['qty'], price_unit=l['price'], discount=0.0,
                price_subtotal=sub, price_subtotal_incl=sub, total_cost=round(l['qty'] * l['cost'], 2),
                is_total_cost_computed=True, uuid=str(uuid.uuid4()), **audit(orow['date_order'])))
    insert('pos_order_line', line_rows)
    totals['tpv_tickets'] += len(order_rows)
    totals['tpv_lineas'] += len(line_rows)
    cr.commit()
    log('%s: %s tickets, %s líneas' % (branch.name, len(order_rows), len(line_rows)))

# ---------------------------------------------------------------- pedidos de venta

customers = env['res.partner'].create([{'name': name, 'is_company': True, 'customer_rank': 1}
                                       for name in ('ALCALDÍA (SIMULADO)', 'CLÍNICA SAN RAFAEL (SIMULADO)',
                                                    'PERSONAL DROMAX (SIMULADO)')])
seller = max(branches, key=lambda b: len(stock[b.id]))
seller_wh = warehouses[seller.id]
top = sorted((d['base'], pid) for (c, pid), d in demand.items()
             if c == seller.id and d['profile'] == 'normal' and d['base'] >= 1)[-200:]
so_seq = 0
for w in range(0, WEEKS, 2):
    if rng.random() < 0.4:
        continue
    so_seq += 1
    customer = rng.choice(customers)
    dt = stamp(START + timedelta(weeks=w, days=rng.randint(0, 4)), 11)
    rows = []
    for base, pid in rng.sample(top, min(len(top), rng.randint(3, 10))):
        qty = max(1, int(base * rng.uniform(2, 6)))
        price = round(sale_price(seller.id, pid) * 0.9, 2)
        sold[seller.id][(pid, w)] += qty
        rows.append(dict(
            name=info[pid]['display_name'], product_id=pid, product_uom_qty=qty, product_uom=UOM_UNIT,
            price_unit=price, price_subtotal=round(qty * price, 2), price_total=round(qty * price, 2),
            price_tax=0.0, discount=0.0, qty_delivered=qty, qty_delivered_method='stock_move',
            qty_invoiced=0.0, qty_to_invoice=0.0, customer_lead=0.0, company_id=seller.id,
            currency_id=seller.currency_id.id, order_partner_id=customer.id, state='sale',
            invoice_status='no', sequence=10, **audit(dt)))
    amount = round(sum(r['price_subtotal'] for r in rows), 2)
    so_id = insert('sale_order', [dict(
        name='SO/SIM/%05d' % so_seq, company_id=seller.id, partner_id=customer.id,
        partner_invoice_id=customer.id, partner_shipping_id=customer.id, date_order=dt,
        warehouse_id=seller_wh.id, picking_policy='direct', state='sale', invoice_status='no',
        delivery_status='full', currency_id=seller.currency_id.id, amount_untaxed=amount,
        amount_tax=0.0, amount_total=amount, user_id=ADMIN, require_signature=False,
        require_payment=False, origin='Datos simulados', **audit(dt))])[0]
    for r in rows:
        r['order_id'] = so_id
    insert('sale_order_line', rows)
    totals['pedidos_venta'] += 1
    totals['pedidos_venta_lineas'] += len(rows)
cr.commit()
log('pedidos de venta: %s (%s)' % (so_seq, seller.name))

# ---------------------------------------------------------------- compras

po_seq = 0


def write_purchase(branch, supplier_id, items, ordered, planned, state, received_fn):
    """items: [(pid, qty, price)]. received_fn(qty) -> unidades recibidas."""
    global po_seq
    po_seq += 1
    wh = warehouses[branch.id]
    rows = []
    for pid, qty, price in items:
        rows.append(dict(
            name=info[pid]['display_name'], product_id=pid, product_qty=qty, product_uom_qty=qty,
            product_uom=UOM_UNIT, price_unit=price, price_subtotal=round(qty * price, 2),
            price_total=round(qty * price, 2), price_tax=0.0, discount=0.0,
            qty_received=received_fn(qty) if state == 'purchase' else 0.0,
            qty_received_method='stock_moves', qty_invoiced=0.0, qty_to_invoice=0.0,
            date_planned=planned, company_id=branch.id, partner_id=supplier_id,
            currency_id=branch.currency_id.id, state=state, sequence=10, **audit(ordered)))
    full = all(r['qty_received'] >= r['product_qty'] for r in rows)
    amount = round(sum(r['price_subtotal'] for r in rows), 2)
    po_id = insert('purchase_order', [dict(
        name='PO/SIM/%05d' % po_seq, partner_id=supplier_id, currency_id=branch.currency_id.id,
        company_id=branch.id, date_order=ordered, date_approve=ordered if state == 'purchase' else None,
        date_planned=planned, picking_type_id=wh.in_type_id.id, state=state, invoice_status='no',
        receipt_status=('full' if full else 'pending') if state == 'purchase' else None,
        amount_untaxed=amount, amount_tax=0.0, amount_total=amount, user_id=ADMIN,
        priority='0', origin='Datos simulados', **audit(ordered))])[0]
    for r in rows:
        r['order_id'] = po_id
    insert('purchase_order_line', rows)
    totals['compras'] += 1
    totals['compras_lineas'] += len(rows)


def by_supplier(branch, items):
    grouped = defaultdict(list)
    for pid, qty in items:
        partner, price = supplier_for(pid, branch.id)
        grouped[partner].append((pid, qty, price))
    return grouped.items()


for branch in branches:
    # Reposición del historial: el lunes se pide lo vendido la semana anterior.
    for w in range(1, WEEKS):
        items = [(pid, math.ceil(qty * rng.uniform(0.9, 1.2)))
                 for (pid, week), qty in sold[branch.id].items() if week == w - 1]
        monday = START + timedelta(weeks=w)
        for supplier_id, lines in by_supplier(branch, items):
            write_purchase(branch, supplier_id, lines, stamp(monday, 10),
                           stamp(monday + timedelta(days=rng.randint(1, 4)), 10), 'purchase', lambda q: q)

    # Pendiente de recibir: parte de los productos cortos o agotados.
    short_items = []
    for (company_id, pid), d in demand.items():
        if company_id != branch.id or d['base'] < 0.5:
            continue
        cover = stock[branch.id].get(pid, 0) / d['base']
        if cover < 1.5 and rng.random() < 0.35:
            short_items.append((pid, math.ceil(d['base'] * rng.uniform(1.5, 3))))
    pending_pids = set()
    for supplier_id, lines in by_supplier(branch, short_items):
        pending_pids.update(pid for pid, _q, _p in lines)
        late = rng.random() < 0.25
        ordered = MONDAY - timedelta(weeks=2 if late else 0)
        planned = (MONDAY - timedelta(days=3)) if late else (TODAY + timedelta(days=rng.randint(1, 5)))
        partial = rng.random() < 0.15
        write_purchase(branch, supplier_id, lines, stamp(ordered, 10), stamp(planned, 10), 'purchase',
                       (lambda q: math.floor(q / 2)) if partial else (lambda q: 0))
        totals['compras_pendientes'] += 1

    # Solicitudes de presupuesto sin confirmar en algunas farmacias.
    if rng.random() < 0.5:
        candidates = [(pid, math.ceil(d['base'] * 2)) for (c, pid), d in demand.items()
                      if c == branch.id and d['base'] >= 1 and pid not in pending_pids]
        for supplier_id, lines in list(by_supplier(branch, rng.sample(candidates, min(len(candidates), 12))))[:1]:
            write_purchase(branch, supplier_id, lines, stamp(TODAY, 9), stamp(TODAY + timedelta(days=3), 10),
                           'draft', lambda q: 0)
            totals['solicitudes'] += 1
    cr.commit()
    log('%s: compras escritas' % branch.name)

profiles = defaultdict(int)
for d in demand.values():
    profiles[d['profile']] += 1
summary = dict(totals, semilla=SEED, semanas=WEEKS, farmacias=len(branches),
               perfiles=dict(profiles), generado=str(TODAY))
ICP.set_param('dromax_sim.generated', repr(summary))
cr.commit()
env.registry.clear_cache()
log('LISTO %s' % summary)
