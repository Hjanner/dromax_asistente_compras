# -*- coding: utf-8 -*-
"""Demo drugstore offers served from a local FTP server.

Writes one inventory .txt per FTP drugstore (VITALCLINIC, INTERCONTINENTAL,
INSUAMINCA) using each drugstore's configured column map, encoding and
separator, points the drugstore at the local FTP server started by
scripts/demo_ftp_server.py, and loads the files through the module's own
_ftp_sync_inventory (the same code the "Descargar y Leer Datos" button runs).

Local test databases only. Usage:

    DROMAX_TEST_DATA_DB=dromax_ai_multi ./odoo-bin shell -c odoo.conf -d dromax_ai_multi --no-http \\
        < scripts/demo_ftp_offers.py

Optional env: DEMO_FTP_DIR (default /opt/odoo17/ftp_demo), DEMO_FTP_PORT (2121),
DEMO_FTP_SEED (17). FTP_SECRET_KEY must be set (the FTP password is stored encrypted).
"""
import json
import os
import random
import re
import secrets
from datetime import date, timedelta

DB = env.cr.dbname  # noqa: F821 (odoo shell)
if os.environ.get('DROMAX_TEST_DATA_DB') != DB:
    raise SystemExit("Refusing to run: set DROMAX_TEST_DATA_DB=%s (local test DB only)" % DB)

BASE_DIR = os.environ.get('DEMO_FTP_DIR', '/opt/odoo17/ftp_demo')
PORT = int(os.environ.get('DEMO_FTP_PORT', '2121'))
rng = random.Random(int(os.environ.get('DEMO_FTP_SEED', '17')))

# Share of our catalog each drugstore offers, and its price level vs. our cost.
PROFILES = {
    'VITALCLINIC': {'coverage': 0.70, 'markup': (1.05, 1.25)},
    'INTERCONTINENTAL': {'coverage': 0.60, 'markup': (1.00, 1.20)},
    'INSUAMINCA': {'coverage': 0.55, 'markup': (0.98, 1.22)},
}
LAB_RE = re.compile(r'[\[(]([^\])]+)[\])]\s*$')
FALLBACK_LABS = ['LETI', 'CALOX', 'GENVEN', 'ELMOR', 'BAYER', 'PFIZER', 'MEGALABS', 'VARGAS']


def lab_of(name):
    match = LAB_RE.search(name or '')
    return match.group(1).strip().upper() if match else rng.choice(FALLBACK_LABS)


def discount_text(keys):
    """One value per 'descuento' column; cascades like '15+0+0+7' when there are several."""
    n = keys.count('descuento')
    if not n:
        return []
    main = rng.choice([0, 0, 5, 10, 12, 15, 20])
    return [str(main)] + [str(rng.choice([0, 0, 0, 3, 7])) for _ in range(n - 1)]


def build_row(keys, product, cost, markup, code):
    price = round(max(cost, 0.3) * rng.uniform(*markup), 2)
    discounts = discount_text(keys)
    factor = 1.0
    for d in discounts:
        factor *= 1 - float(d) / 100
    final = round(price * factor, 2)
    stock = 0 if rng.random() < 0.08 else rng.choice([rng.randint(1, 20), rng.randint(20, 400)])
    lot = (date.today() + timedelta(days=rng.randint(120, 900))).strftime('%m/%Y')
    values = {'codigo': code, 'barras': product.barcode, 'descripcion': product.name.upper(),
              'lote': lot, 'precio': '%.2f' % price, 'precio_final': '%.2f' % final,
              'stock': str(stock), 'laboratorio': lab_of(product.name)}
    out, d_iter = [], iter(discounts)
    for key in keys:
        if key == 'descuento':
            out.append(next(d_iter))
        elif key == 'extra':
            out.append(rng.choice(['', 'N', 'S', '1']))
        else:
            out.append(values.get(key, ''))
    return out


Partner = env['res.partner']  # noqa: F821
products = env['product.product'].search([('barcode', '!=', False), ('purchase_ok', '=', True)])  # noqa: F821
os.makedirs(BASE_DIR, exist_ok=True)
users = {}
for name, profile in PROFILES.items():
    partner = Partner.search([('name', '=ilike', name), ('is_supplier_ftp', '=', True)], limit=1)
    if not partner:
        print('[demo-ftp] skip %s: no FTP drugstore contact' % name)
        continue
    keys = [k.strip() for k in (partner.ftp_column_map or '').split(',')]
    offered = [p for p in products if rng.random() < profile['coverage']]
    lines = []
    for i, product in enumerate(offered, 1):
        cost = product.standard_price or (product.lst_price or 1.0) * 0.7
        lines.append(build_row(keys, product, cost, profile['markup'], '%s%05d' % (name[:3], i)))
    # Drugstore products we don't carry: barcodes that match nothing in our catalog.
    for i in range(25):
        fake = type('P', (), {'barcode': '759%010d' % rng.randint(0, 10 ** 10 - 1),
                              'name': 'PRODUCTO NUEVO %s %d MG X %d TABLETAS [%s]' % (
                                  name[:4], rng.choice([5, 10, 50, 100]), rng.choice([10, 20, 30]),
                                  rng.choice(FALLBACK_LABS))})()
        lines.append(build_row(keys, fake, rng.uniform(1, 30), profile['markup'], '%sN%04d' % (name[:3], i)))
    folder = os.path.join(BASE_DIR, name)
    os.makedirs(folder, exist_ok=True)
    for old in os.listdir(folder):
        os.remove(os.path.join(folder, old))
    encoding = 'latin-1' if (partner.ftp_encoding or '').lower().startswith('latin') else 'utf-8'
    sep = ';'
    path = os.path.join(folder, 'inventario_%s.txt' % date.today().strftime('%Y%m%d'))
    with open(path, 'w', encoding=encoding, errors='replace', newline='\r\n') as fh:
        fh.write('\n'.join(sep.join(row) for row in lines) + '\n')
    user = 'demo_' + name.lower()
    password = secrets.token_urlsafe(12)
    users[user] = {'password': password, 'home': folder}
    partner.write({'ftp_host': '127.0.0.1:%d' % PORT, 'ftp_user': user, 'ftp_directory': False,
                   'ftp_file_pattern': '*.txt', 'ftp_separator': ';'})
    partner.ftp_password_input = password
    print('[demo-ftp] %s: %d rows -> %s' % (name, len(lines), path))

users_file = os.path.join(BASE_DIR, 'users.json')
with open(os.open(users_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as fh:
    json.dump(users, fh)
env.cr.commit()  # noqa: F821
print('[demo-ftp] users written to %s; start scripts/demo_ftp_server.py, then sync' % users_file)

if os.environ.get('DEMO_FTP_SYNC') == '1':
    for name in PROFILES:
        partner = Partner.search([('name', '=ilike', name), ('is_supplier_ftp', '=', True)], limit=1)
        if partner:
            state, message = partner._ftp_sync_inventory(force=True)
            print('[demo-ftp] sync %s: %s %s' % (name, state, message.splitlines()[0]))
    env.cr.commit()  # noqa: F821
