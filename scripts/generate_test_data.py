# -*- coding: utf-8 -*-
"""Synthetic multi-branch test data for the purchasing assistant.

Run inside an Odoo shell on a disposable LOCAL copy of the database:

    DROMAX_TEST_DATA_DB=dromax_ai_multi \
    ./venv/bin/python odoo/odoo-bin shell -c config/odoo17.conf \
        -d dromax_ai_multi --no-http \
        < extra-addons/dromax_asistente_compras/scripts/generate_test_data.py

Optional environment variables:
    TEST_DATA_SEED      random seed (default 17): same seed, same data
    TEST_DATA_YEARS     years of history (default 3)
    TEST_DATA_PRODUCTS  catalog size (default 1200)

What it builds, for every company (branch) in the database:
- A pharmacy catalog with presentations, laboratories, categories and
  supplier prices for the FTP drugstores.
- A week-by-week simulation of demand, stock and replenishment, written as
  POS sales (one order per branch and week) and purchase orders (received,
  pending and draft). Current stock is the end state of the simulation.
- B2B sale orders from the largest branch, and reorder rules.
- Deliberate scenarios: stockouts, low cover, overstock, dead stock, new and
  discontinued products, seasonal demand, products without barcode and
  duplicated barcodes.

No accounting is generated: history is written with SQL, so no pickings,
invoices or journal entries exist. To regenerate, restore the DB and rerun.
"""
import math
import os
import random
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from psycopg2.extras import execute_values

DB = env.cr.dbname
if os.environ.get('DROMAX_TEST_DATA_DB') != DB:
    raise SystemExit('Set DROMAX_TEST_DATA_DB=%s to confirm this is a disposable test DB.' % DB)
ICP = env['ir.config_parameter'].sudo()
if ICP.get_param('dromax_test_data.generated'):
    raise SystemExit('Test data already generated in %s; restore the DB to regenerate.' % DB)

SEED = int(os.environ.get('TEST_DATA_SEED', 17))
YEARS = int(os.environ.get('TEST_DATA_YEARS', 3))
N_PRODUCTS = int(os.environ.get('TEST_DATA_PRODUCTS', 1200))
WEEKS = YEARS * 52
TODAY = date.today()
START = TODAY - timedelta(weeks=WEEKS)
INFLATION = 0.05  # yearly, USD

rng = random.Random(SEED)
cr = env.cr
ADMIN = env.ref('base.user_admin').id
UOM_UNIT = env.ref('uom.product_uom_unit').id


def log(msg):
    print('[test-data] %s' % msg, flush=True)


# ---------------------------------------------------------------- helpers

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
    """Bulk insert dicts (only existing columns are kept); returns new ids."""
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


def ean13(body12):
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body12))
    return body12 + str((10 - total % 10) % 10)


def week_date(w):
    return START + timedelta(weeks=w)


def stamp(d, hour=20):
    return datetime.combine(d, time(hour, 0))


def audit(dt):
    return {'create_uid': ADMIN, 'write_uid': ADMIN, 'create_date': dt, 'write_date': dt}


# ---------------------------------------------------------------- catalog

LABS = ['LAB ANDINO', 'GENERICOS DEL SUR', 'FARMA CARIBE', 'BIOVITAL', 'LAB ORIENTE',
        'MEDIGEN', 'PHARMALAB', 'TERAFARMA', 'QUIMICA ARAGUA', 'NOVAMED', 'VITAFARM',
        'SALUDGEN']

# (molecule, category, doses, presentations, unit price USD range)
MOLECULES = [
    ('ACETAMINOFEN', 'Analgesicos', ['500 MG', '650 MG'], ['TAB X 10', 'TAB X 20', 'JARABE 120 ML'], (0.8, 3)),
    ('IBUPROFENO', 'Analgesicos', ['400 MG', '600 MG'], ['TAB X 10', 'TAB X 20', 'SUSPENSION 100 ML'], (1, 4)),
    ('DICLOFENAC SODICO', 'Analgesicos', ['50 MG', '75 MG'], ['TAB X 10', 'TAB X 30', 'AMP X 3'], (1, 5)),
    ('KETOROLAC', 'Analgesicos', ['10 MG', '30 MG'], ['TAB X 10', 'AMP X 1'], (1.5, 5)),
    ('METAMIZOL', 'Analgesicos', ['500 MG', '1 G'], ['TAB X 10', 'AMP X 5'], (1, 4)),
    ('NAPROXENO', 'Analgesicos', ['250 MG', '500 MG'], ['TAB X 10', 'TAB X 20'], (1.5, 5)),
    ('AMOXICILINA', 'Antibioticos', ['500 MG', '250 MG/5 ML'], ['CAP X 21', 'SUSPENSION 60 ML'], (2, 7)),
    ('AZITROMICINA', 'Antibioticos', ['500 MG', '200 MG/5 ML'], ['TAB X 3', 'SUSPENSION 15 ML'], (3, 9)),
    ('CIPROFLOXACINA', 'Antibioticos', ['500 MG'], ['TAB X 10', 'TAB X 14'], (2.5, 8)),
    ('CEFADROXILO', 'Antibioticos', ['500 MG', '250 MG/5 ML'], ['CAP X 12', 'SUSPENSION 60 ML'], (3, 9)),
    ('CLARITROMICINA', 'Antibioticos', ['500 MG'], ['TAB X 10', 'TAB X 14'], (5, 14)),
    ('LORATADINA', 'Respiratorio', ['10 MG', '5 MG/5 ML'], ['TAB X 10', 'TAB X 30', 'JARABE 60 ML'], (1, 4)),
    ('CETIRIZINA', 'Respiratorio', ['10 MG'], ['TAB X 10', 'TAB X 20', 'GOTAS 20 ML'], (1, 4)),
    ('AMBROXOL', 'Respiratorio', ['30 MG/5 ML', '15 MG/5 ML'], ['JARABE 120 ML', 'GOTAS 30 ML'], (1.5, 5)),
    ('SALBUTAMOL', 'Respiratorio', ['100 MCG', '2 MG/5 ML'], ['INHALADOR 200 DOSIS', 'JARABE 120 ML'], (2, 8)),
    ('DEXTROMETORFANO', 'Respiratorio', ['15 MG/5 ML'], ['JARABE 120 ML', 'JARABE 240 ML'], (2, 6)),
    ('FENILEFRINA + CLORFENAMINA', 'Respiratorio', ['10 MG/4 MG'], ['TAB X 10', 'JARABE 120 ML'], (1.5, 5)),
    ('LOSARTAN POTASICO', 'Cardiovascular', ['50 MG', '100 MG'], ['TAB X 30', 'TAB X 60'], (2, 9)),
    ('ENALAPRIL', 'Cardiovascular', ['10 MG', '20 MG'], ['TAB X 30'], (1.5, 6)),
    ('AMLODIPINA', 'Cardiovascular', ['5 MG', '10 MG'], ['TAB X 30', 'TAB X 10'], (1.5, 7)),
    ('ATORVASTATINA', 'Cardiovascular', ['20 MG', '40 MG'], ['TAB X 30', 'TAB X 10'], (3, 12)),
    ('ACIDO ACETILSALICILICO', 'Cardiovascular', ['81 MG', '100 MG'], ['TAB X 30', 'TAB X 100'], (1, 4)),
    ('HIDROCLOROTIAZIDA', 'Cardiovascular', ['25 MG', '50 MG'], ['TAB X 30'], (1, 4)),
    ('METFORMINA', 'Diabetes', ['500 MG', '850 MG', '1000 MG'], ['TAB X 30', 'TAB X 60'], (1.5, 7)),
    ('GLIBENCLAMIDA', 'Diabetes', ['5 MG'], ['TAB X 30', 'TAB X 60'], (1, 4)),
    ('INSULINA NPH', 'Diabetes', ['100 UI/ML'], ['VIAL 10 ML'], (8, 20)),
    ('OMEPRAZOL', 'Gastro', ['20 MG', '40 MG'], ['CAP X 14', 'CAP X 30'], (1.5, 6)),
    ('ESOMEPRAZOL', 'Gastro', ['20 MG', '40 MG'], ['TAB X 14', 'TAB X 28'], (3, 12)),
    ('RANITIDINA', 'Gastro', ['150 MG', '300 MG'], ['TAB X 20'], (1, 4)),
    ('LOPERAMIDA', 'Gastro', ['2 MG'], ['TAB X 6', 'TAB X 12'], (1, 3)),
    ('SALES DE REHIDRATACION ORAL', 'Gastro', ['SABOR NATURAL', 'SABOR FRESA'], ['SOBRE X 1', 'SOLUCION 500 ML'], (0.5, 2.5)),
    ('METOCLOPRAMIDA', 'Gastro', ['10 MG'], ['TAB X 20', 'AMP X 5'], (1, 4)),
    ('CLOTRIMAZOL', 'Dermatologia', ['1 %'], ['CREMA 20 G', 'CREMA 40 G'], (1.5, 5)),
    ('BETAMETASONA', 'Dermatologia', ['0.05 %'], ['CREMA 30 G', 'LOCION 30 ML'], (2, 6)),
    ('MUPIROCINA', 'Dermatologia', ['2 %'], ['UNGUENTO 15 G'], (3, 8)),
    ('KETOCONAZOL', 'Dermatologia', ['2 %'], ['CREMA 30 G', 'CHAMPU 120 ML'], (2, 7)),
    ('VITAMINA C', 'Vitaminas', ['500 MG', '1 G'], ['TAB X 30', 'TAB EFERV X 10'], (1.5, 6)),
    ('COMPLEJO B', 'Vitaminas', ['FORTE'], ['TAB X 30', 'AMP X 3'], (2, 7)),
    ('ACIDO FOLICO', 'Vitaminas', ['1 MG', '5 MG'], ['TAB X 30'], (1, 3)),
    ('HIERRO + ACIDO FOLICO', 'Vitaminas', ['FORTE'], ['TAB X 30', 'JARABE 120 ML'], (2, 7)),
    ('CALCIO + VITAMINA D', 'Vitaminas', ['600 MG/400 UI'], ['TAB X 30', 'TAB X 60'], (3, 9)),
    ('ZINC', 'Pediatria', ['20 MG/5 ML'], ['JARABE 120 ML'], (2, 5)),
    ('ACETAMINOFEN PEDIATRICO', 'Pediatria', ['160 MG/5 ML', '100 MG/ML'], ['JARABE 120 ML', 'GOTAS 30 ML'], (1.5, 4)),
    ('ALBENDAZOL', 'Pediatria', ['400 MG', '200 MG/5 ML'], ['TAB X 1', 'SUSPENSION 20 ML'], (0.8, 3)),
]

SUPPLIES = [
    ('GASA ESTERIL', ['10 X 10 CM'], ['X 1 UND', 'X 100 UND'], (0.2, 0.5)),
    ('JERINGA DESECHABLE', ['3 ML', '5 ML', '10 ML'], ['X 1 UND', 'X 100 UND'], (0.15, 0.4)),
    ('GUANTES DE LATEX', ['TALLA M', 'TALLA L'], ['X 2 UND', 'CAJA X 100 UND'], (0.2, 0.6)),
    ('ALCOHOL ISOPROPILICO', ['70 %'], ['FRASCO 250 ML', 'FRASCO 1 L'], (1, 4)),
    ('TAPABOCAS', ['TRIPLE CAPA'], ['X 1 UND', 'CAJA X 50 UND'], (0.1, 0.3)),
    ('PINZA UMBILICAL', ['ESTERIL'], ['X 1 UND', 'X 100 UND'], (0.25, 0.5)),
    ('VENDA ELASTICA', ['5 CM', '10 CM'], ['X 1 UND'], (0.8, 2.5)),
    ('ADHESIVO MICROPOROSO', ['1 PULG', '2 PULG'], ['ROLLO X 1'], (0.6, 2)),
]


def pack_factor(presentation):
    """Rough price multiplier from the pack size in the presentation name."""
    words = presentation.split()
    for i, word in enumerate(words):
        if word == 'X' and i + 1 < len(words) and words[i + 1].isdigit():
            return max(0.3, min(12.0, (int(words[i + 1]) / 10.0) ** 0.8))
    return 1.4


def build_catalog():
    candidates = []
    for mol, categ, doses, forms, (lo, hi) in MOLECULES:
        unit = rng.uniform(lo, hi)
        labs = rng.sample(LABS, rng.randint(4, 8))
        for dose in doses:
            for form in forms:
                for lab in labs:
                    lab_factor = rng.uniform(0.75, 1.35)
                    price = round(unit * pack_factor(form) * lab_factor, 2)
                    candidates.append(('%s %s %s - %s' % (mol, dose, form, lab), categ, max(price, 0.3)))
    for name, sizes, forms, (lo, hi) in SUPPLIES:
        unit = rng.uniform(lo, hi)
        for size in sizes:
            for form in forms:
                n = next((int(t) for t in form.split() if t.isdigit()), 1)
                candidates.append(('%s %s %s' % (name, size, form), 'Material medico', round(max(unit * n * rng.uniform(0.8, 1.0), 0.1), 2)))
    rng.shuffle(candidates)
    return candidates[:N_PRODUCTS]


PROFILES = [  # name, share, weekly demand range per branch
    ('alta', 0.10, (15, 50)),
    ('media', 0.28, (4, 15)),
    ('baja', 0.34, (0.2, 3)),
    ('estacional', 0.08, (3, 12)),
    ('muerto', 0.08, (1, 8)),
    ('nuevo', 0.06, (3, 15)),
    ('descontinuado', 0.06, (2, 10)),
]


def pick_profile(categ):
    if categ == 'Respiratorio' and rng.random() < 0.5:
        return 'estacional'
    r, acc = rng.random(), 0.0
    for name, share, _rng in PROFILES:
        acc += share
        if r <= acc:
            return name
    return 'baja'


log('db=%s seed=%s years=%s products=%s start=%s' % (DB, SEED, YEARS, N_PRODUCTS, START))

companies = env['res.company'].search([], order='id')
warehouses = {w.company_id.id: w for w in env['stock.warehouse'].search([])}
pos_configs = {}
for cfg in env['pos.config'].search([], order='id'):
    pos_configs.setdefault(cfg.company_id.id, cfg)
branches = [c for c in companies if c.id in warehouses and c.id in pos_configs]
log('branches: %s' % ', '.join(b.name for b in branches))

Partner = env['res.partner']
suppliers = []
for name in ('VITALCLINIC', 'INTERCONTINENTAL', 'INSUAMINCA'):
    partner = Partner.search([('name', '=ilike', name), ('is_company', '=', True)], limit=1)
    if not partner:
        partner = Partner.create({'name': name, 'is_company': True})
    partner.supplier_rank = max(partner.supplier_rank, 1)
    suppliers.append(partner)

customers = Partner.create([{'name': 'CLINICA %s' % n, 'is_company': True, 'customer_rank': 1}
                            for n in ('SAN RAFAEL', 'LOS ANDES', 'EL VALLE', 'SANTA ANA',
                                      'LA FLORIDA', 'CENTRO MEDICO NORTE')])

categ_root = env.ref('product.product_category_all')
categories = {}
for mol in MOLECULES:
    categories.setdefault(mol[1], None)
categories['Material medico'] = None
for name in categories:
    categories[name] = env['product.category'].create({'name': name, 'parent_id': categ_root.id}).id

catalog = build_catalog()
products = []  # dicts with simulation attributes
Template = env['product.template']
for start in range(0, len(catalog), 200):
    chunk = catalog[start:start + 200]
    vals = []
    for i, (name, categ, price) in enumerate(chunk, start=start + 1):
        vals.append({
            'name': name,
            'default_code': 'DRX%05d' % i,
            'barcode': ean13('759%09d' % (100000000 + i * 7919 % 899999999)),
            'categ_id': categories[categ],
            'detailed_type': 'product',
            'list_price': price,
            'sale_ok': True,
            'purchase_ok': True,
            'available_in_pos': True,
            'taxes_id': [(6, 0, [])],
            'supplier_taxes_id': [(6, 0, [])],
        })
    templates = Template.create(vals)
    for tmpl, (name, categ, price) in zip(templates, chunk):
        profile = pick_profile(categ)
        lo, hi = next(p[2] for p in PROFILES if p[0] == profile)
        products.append({
            'id': tmpl.product_variant_id.id,
            'tmpl': tmpl.id,
            'name': '[%s] %s' % (tmpl.default_code, name),
            'price': price,
            'cost': round(price * rng.uniform(0.6, 0.75), 2),
            'profile': profile,
            'base': rng.uniform(lo, hi),
            'death': rng.randint(WEEKS - 80, WEEKS - 52),
            'launch': rng.randint(WEEKS - 14, WEEKS - 4),
            'end': rng.randint(40, 70),
        })
    cr.commit()
log('products created: %s' % len(products))

# Barcode edge cases for the barcode report (SQL: the ORM forbids duplicates).
no_barcode = rng.sample(products, max(1, len(products) * 3 // 100))
cr.execute('UPDATE product_product SET barcode = NULL WHERE id = ANY(%s)', ([p['id'] for p in no_barcode],))
with_barcode = [p for p in products if p not in no_barcode]
for a, b in zip(rng.sample(with_barcode, 6), rng.sample(with_barcode, 6)):
    if a is not b:
        cr.execute('UPDATE product_product SET barcode = (SELECT barcode FROM product_product WHERE id = %s) WHERE id = %s',
                   (a['id'], b['id']))

# Supplier prices: each drugstore a little cheaper or more expensive.
supplier_bias = {s.id: rng.uniform(0.94, 1.06) for s in suppliers}
supplier_price = {}
info_vals = []
for p in products:
    for s in suppliers:
        price = round(p['cost'] * supplier_bias[s.id] * rng.uniform(0.95, 1.05), 2)
        supplier_price[(p['id'], s.id)] = price
        info_vals.append({'partner_id': s.id, 'product_tmpl_id': p['tmpl'], 'price': price,
                          'min_qty': 1, 'delay': rng.randint(1, 4)})
env['product.supplierinfo'].create(info_vals)
for c in branches:
    env['ir.property'].with_company(c)._set_multi(
        'standard_price', 'product.product', {p['id']: p['cost'] for p in products})
cr.commit()
log('supplier prices and costs set')


# ---------------------------------------------------------------- demand model

def season(profile, d):
    factor = 1.15 if d.month == 12 else 1.0
    if profile == 'estacional':  # rainy season peak around July, small bump in December
        factor *= 1 + 1.3 * max(0.0, math.cos(2 * math.pi * (d.month - 7) / 12))
    return factor


def active(p, w):
    prof = p['profile']
    if prof == 'muerto':
        return w < p['death']
    if prof == 'nuevo':
        return w >= p['launch']
    if prof == 'descontinuado':
        return w < p['end']
    return True


def expected(p, branch_size, w):
    if not active(p, w):
        return 0.0
    d = week_date(w)
    trend = (1.06) ** (w / 52.0)
    ramp = min(1.0, (w - p['launch'] + 1) / 4.0) if p['profile'] == 'nuevo' else 1.0
    return p['base'] * branch_size * season(p['profile'], d) * trend * ramp


def price_at(price_now, w):
    return round(price_now / (1 + INFLATION) ** ((WEEKS - w) / 52.0), 2)


def can_reorder(p, w):
    prof = p['profile']
    if prof == 'muerto':
        return w < p['death'] + 4
    if prof == 'descontinuado':
        return w < p['end'] - 2
    if prof == 'nuevo':
        return w >= p['launch'] - 1
    return True


# ---------------------------------------------------------------- simulation per branch

totals = defaultdict(int)
po_seq = 0
for idx, branch in enumerate(branches):
    wh = warehouses[branch.id]
    cfg = pos_configs[branch.id]
    size = 1.4 if idx == 1 else rng.uniform(0.5, 1.2)
    overbuy = 1.6 if idx == len(branches) - 1 else rng.uniform(0.9, 1.2)
    carry_prob = 0.55 + 0.35 * min(1.0, size)
    carried = [p for p in products if rng.random() < carry_prob]
    short = (branch.name.split()[-1][:6]).upper()
    log('%s: size=%.2f overbuy=%.2f carried=%s' % (branch.name, size, overbuy, len(carried)))

    session_id = insert('pos_session', [dict(
        name='%s/%s/HIST' % (cfg.name, branch.id), config_id=cfg.id, user_id=ADMIN, state='closed',
        company_id=branch.id, start_at=stamp(START, 8), stop_at=stamp(TODAY - timedelta(days=1), 21),
        sequence_number=WEEKS + 1, login_number=1, **audit(stamp(START, 8)))])[0]

    stock = {p['id']: (0 if p['profile'] == 'nuevo' else math.ceil(expected(p, size, 0) * 4)) for p in carried}
    ema = {p['id']: expected(p, size, 0) for p in carried}
    arrivals = defaultdict(list)
    pending = defaultdict(float)
    pos_orders, pos_lines = [], []
    po_orders, po_lines = [], []

    for w in range(WEEKS):
        d = week_date(w)
        for pid, qty in arrivals.pop(w, []):
            stock[pid] += qty
            pending[pid] -= qty

        # Sales of the week, capped by stock (real stockouts in the history).
        lines = []
        for p in carried:
            demand = poisson(expected(p, size, w))
            sold = min(demand, stock[p['id']])
            stock[p['id']] -= sold
            ema[p['id']] = 0.7 * ema[p['id']] + 0.3 * demand
            if sold:
                price = price_at(p['price'], w)
                lines.append(dict(product_id=p['id'], qty=sold, price=price,
                                  cost=price_at(p['cost'], w), name=p['name']))
        if lines:
            pos_orders.append((w, lines))

        # Replenishment: weekly order, cheapest drugstore most of the time.
        by_supplier = defaultdict(list)
        for p in carried:
            if not can_reorder(p, w):
                continue
            pid = p['id']
            avg = max(ema[pid], 0.2 if p['profile'] == 'baja' else 0.5)
            if stock[pid] + pending[pid] < avg * 2:
                qty = math.ceil(avg * 4 * overbuy) - stock[pid] - pending[pid]
                if qty <= 0:
                    continue
                options = sorted(suppliers, key=lambda s: supplier_price[(pid, s.id)])
                supplier = options[0] if rng.random() < 0.7 else rng.choice(options)
                lead = 1 if rng.random() < 0.85 else 2
                pending[pid] += qty
                arrivals[w + lead].append((pid, qty))
                by_supplier[supplier.id].append((p, qty, lead))
        for supplier_id, items in by_supplier.items():
            po_orders.append((w, supplier_id, items))

    # Forced current scenarios on top of the natural ones.
    actives = [p for p in carried if active(p, WEEKS - 1) and p['profile'] in ('alta', 'media')]
    for p in rng.sample(actives, len(actives) * 7 // 100):
        stock[p['id']] = 0
    lows = [p for p in carried if p['profile'] == 'baja']
    for p in rng.sample(lows, len(lows) * 5 // 100):
        stock[p['id']] += math.ceil(p['base'] * size * 30) + 5

    # Write POS history.
    order_rows = []
    for w, lines in pos_orders:
        dt = stamp(week_date(w) + timedelta(days=5))
        total = round(sum(l['qty'] * l['price'] for l in lines), 2)
        order_rows.append(dict(
            name='%s/VTA/%05d' % (short, w + 1), pos_reference='Semana %s-%05d' % (cfg.id, w + 1),
            company_id=branch.id, session_id=session_id, config_id=cfg.id, user_id=ADMIN,
            date_order=dt, state='done', amount_tax=0.0, amount_total=total, amount_paid=total,
            amount_return=0.0, sequence_number=w + 1, uuid=str(uuid.uuid4()), **audit(dt)))
    order_ids = insert('pos_order', order_rows)
    line_rows = []
    for oid, (w, lines), orow in zip(order_ids, pos_orders, order_rows):
        for l in lines:
            sub = round(l['qty'] * l['price'], 2)
            line_rows.append(dict(
                order_id=oid, company_id=branch.id, product_id=l['product_id'], name=l['name'],
                full_product_name=l['name'], qty=l['qty'], price_unit=l['price'], discount=0.0,
                price_subtotal=sub, price_subtotal_incl=sub, total_cost=round(l['qty'] * l['cost'], 2),
                is_total_cost_computed=True, uuid=str(uuid.uuid4()), **audit(orow['date_order'])))
    insert('pos_order_line', line_rows)
    totals['pos_orders'] += len(order_rows)
    totals['pos_lines'] += len(line_rows)

    # Write purchase history: received, pending (ordered in the last weeks) and drafts.
    for w, supplier_id, items in po_orders:
        po_seq += 1
        dt = stamp(week_date(w) + timedelta(days=rng.randint(0, 2)), 10)
        received = all(w + lead < WEEKS for _p, _q, lead in items)
        planned = stamp(week_date(w + max(lead for _p, _q, lead in items)), 10)
        state = 'purchase'
        if w >= WEEKS - 1 and rng.random() < 0.3:
            state = 'draft'
        rows = []
        for p, qty, lead in items:
            price = round(price_at(supplier_price[(p['id'], supplier_id)], w) * rng.uniform(0.97, 1.03), 2)
            got = qty if (state == 'purchase' and w + lead < WEEKS) else 0
            rows.append(dict(
                name=p['name'], product_id=p['id'], product_qty=qty, product_uom_qty=qty,
                product_uom=UOM_UNIT, price_unit=price, price_subtotal=round(qty * price, 2),
                price_total=round(qty * price, 2), price_tax=0.0, discount=0.0,
                qty_received=got, qty_received_method='stock_moves', qty_invoiced=0.0,
                qty_to_invoice=0.0, date_planned=planned, company_id=branch.id,
                partner_id=supplier_id, currency_id=branch.currency_id.id, state=state,
                sequence=10, **audit(dt)))
        amount = round(sum(r['price_subtotal'] for r in rows), 2)
        po_id = insert('purchase_order', [dict(
            name='PO/%s/%05d' % (short, po_seq), partner_id=supplier_id,
            currency_id=branch.currency_id.id, company_id=branch.id, date_order=dt,
            date_approve=dt if state == 'purchase' else None, date_planned=planned,
            picking_type_id=wh.in_type_id.id, state=state, invoice_status='no',
            receipt_status=('full' if received else 'pending') if state == 'purchase' else None,
            amount_untaxed=amount, amount_tax=0.0, amount_total=amount, user_id=ADMIN,
            priority='0', origin='Datos de prueba', **audit(dt))])[0]
        for r in rows:
            r['order_id'] = po_id
        insert('purchase_order_line', rows)
        totals['purchase_orders'] += 1
        totals['purchase_lines'] += len(rows)
        totals['purchase_%s%s' % (state, '' if received or state == 'draft' else '_pending')] += 1

    # Current stock = end of simulation.
    quant_rows = [dict(product_id=pid, location_id=wh.lot_stock_id.id, company_id=branch.id,
                       quantity=qty, reserved_quantity=0.0, inventory_quantity=0.0,
                       inventory_diff_quantity=0.0, inventory_quantity_set=False,
                       in_date=stamp(TODAY - timedelta(days=7)), **audit(stamp(TODAY, 6)))
                  for pid, qty in stock.items() if qty > 0]
    insert('stock_quant', quant_rows)
    totals['quants'] += len(quant_rows)
    totals['stockouts_now'] += sum(1 for p in carried if stock[p['id']] <= 0 and active(p, WEEKS - 1))

    # Reorder rules for fast movers.
    rules = [{'product_id': p['id'], 'warehouse_id': wh.id, 'location_id': wh.lot_stock_id.id,
              'company_id': branch.id, 'trigger': 'manual',
              'product_min_qty': math.ceil(ema[p['id']] * 2), 'product_max_qty': math.ceil(ema[p['id']] * 6)}
             for p in carried if p['profile'] in ('alta', 'media') and active(p, WEEKS - 1)]
    env['stock.warehouse.orderpoint'].with_company(branch).create(rules)
    totals['orderpoints'] += len(rules)
    cr.commit()
    log('%s done: %s POS orders, %s POs, %s quants' % (branch.name, len(order_rows), len(po_orders), len(quant_rows)))

# ---------------------------------------------------------------- B2B sale orders

seller = branches[1] if len(branches) > 1 else branches[0]
seller_wh = warehouses[seller.id]
sellable = [p for p in products if p['profile'] in ('alta', 'media', 'estacional')]
so_seq = 0
for month in range(YEARS * 12):
    d = START + timedelta(days=30 * month + rng.randint(0, 25))
    for customer in rng.sample(list(customers), rng.randint(1, 3)):
        so_seq += 1
        dt = stamp(d, 11)
        w = min(WEEKS - 1, (d - START).days // 7)
        items = rng.sample(sellable, rng.randint(8, 25))
        rows = []
        for p in items:
            qty = rng.randint(5, 60)
            price = round(price_at(p['price'], w) * 0.85, 2)
            rows.append(dict(
                name=p['name'], product_id=p['id'], product_uom_qty=qty, product_uom=UOM_UNIT,
                price_unit=price, price_subtotal=round(qty * price, 2), price_total=round(qty * price, 2),
                price_tax=0.0, discount=0.0, qty_delivered=qty, qty_delivered_method='stock_move',
                qty_invoiced=0.0, qty_to_invoice=0.0, customer_lead=0.0, company_id=seller.id,
                currency_id=seller.currency_id.id, order_partner_id=customer.id, state='sale',
                invoice_status='no', sequence=10, **audit(dt)))
        amount = round(sum(r['price_subtotal'] for r in rows), 2)
        so_id = insert('sale_order', [dict(
            name='SO/HIST/%05d' % so_seq, company_id=seller.id, partner_id=customer.id,
            partner_invoice_id=customer.id, partner_shipping_id=customer.id, date_order=dt,
            warehouse_id=seller_wh.id, picking_policy='direct', state='sale', invoice_status='no',
            delivery_status='full', currency_id=seller.currency_id.id, amount_untaxed=amount,
            amount_tax=0.0, amount_total=amount, user_id=ADMIN, require_signature=False,
            require_payment=False, origin='Datos de prueba', **audit(dt))])[0]
        for r in rows:
            r['order_id'] = so_id
        insert('sale_order_line', rows)
        totals['sale_orders'] += 1
        totals['sale_lines'] += len(rows)

profiles = defaultdict(int)
for p in products:
    profiles[p['profile']] += 1
summary = dict(totals, products=len(products), branches=len(branches), seed=SEED, years=YEARS,
               profiles=dict(profiles), generated_on=str(TODAY))
ICP.set_param('dromax_test_data.generated', repr(summary))
cr.commit()
env.registry.clear_cache()
log('DONE %s' % summary)
