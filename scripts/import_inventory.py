# -*- coding: utf-8 -*-
"""Importa el inventario unificado de las farmacias (Excel de Daniel).

Se ejecuta dentro de una consola de Odoo, sobre la base donde se quiere cargar:

    INVENTORY_DB=<base> INVENTORY_XLSX="/ruta/INVENTARIO CON CANTIDAD FINAL.xlsx" \\
    python3 odoo/odoo-bin shell -c <odoo.conf> -d <base> --no-http \\
        < <ruta al módulo>/scripts/import_inventory.py

Variables de entorno:
    INVENTORY_DB       obligatoria; tiene que ser igual al nombre de la base
                       (protección para no cargar en la base equivocada).
    INVENTORY_XLSX     obligatoria; ruta del Excel.
    INVENTORY_DRY_RUN  "1" para solo leer y mostrar lo que haría, sin escribir.
    INVENTORY_REPORT   opcional; ruta de un CSV con todas las incidencias
                       (códigos de barras no válidos o repetidos, etc.).

Qué hace (se puede repetir: actualiza en vez de duplicar):
- Un producto por fila, identificado por su referencia interna (columnas
  REF_*), que se guarda como código interno y como ID externo
  dromax_inventario.producto_<REF>. Nombre = columna NOMBRE (ya lleva el
  laboratorio entre corchetes). Almacenable, se compra y se vende en el TPV,
  sin impuestos (la base trae un 22 % por defecto que no corresponde).
- Código de barras: el primero válido (8 a 14 dígitos) de UPC1..UPC8 que no
  tenga ya otro producto. Los demás se informan como incidencias.
- Precio de venta: mediana de los VENTA_* mayores que cero.
- Por cada farmacia (sucursal), el costo (precio de coste por empresa) y las
  existencias en su almacén principal, como ajuste de inventario (con su
  movimiento de stock). Las cantidades negativas se cargan como 0.

Las columnas de cada farmacia se localizan por su cabecera ID_<FARMACIA> y
van en este orden: ID, nombre, referencia, cantidad, costo, venta, código.
"""
import csv
import os
import re
import statistics
from collections import Counter, defaultdict

import openpyxl

# Bloque del Excel -> nombre de la sucursal (res.company) en la base.
BRANCHES = {
    'DROMAX': 'DROMAX',
    'FARMAX': 'FARMACIA FARMAX',
    'GANGA': 'FARMACIA FARMAGANGA',
    'IDEAL': 'FARMACIA IDEAL',
    'LA45': 'FARMACIA LA 45',
    'MANAL': 'FARMACIA MANAL',
    'SOLIDARIA': 'FARMARCIA FSC',
}
XMLID_MODULE = 'dromax_inventario'
EMPTY = {'', 'NULL', 'NO APLICA', '#N/A', '0', 'NONE'}
BATCH = 500


def log(msg):
    print('[inventario] %s' % msg, flush=True)


def text(value):
    value = '' if value is None else str(value).strip()
    return '' if value.upper() in EMPTY else value


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


DB = env.cr.dbname
if os.environ.get('INVENTORY_DB') != DB:
    raise SystemExit('Define INVENTORY_DB=%s para confirmar que es la base donde quieres cargar.' % DB)
XLSX = os.environ.get('INVENTORY_XLSX')
if not XLSX or not os.path.isfile(XLSX):
    raise SystemExit('Define INVENTORY_XLSX con la ruta del Excel (no se encontró: %r).' % XLSX)
DRY_RUN = os.environ.get('INVENTORY_DRY_RUN') == '1'
REPORT = os.environ.get('INVENTORY_REPORT')

# ------------------------------------------------------------------ lectura

wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
ws = wb.worksheets[0]
rows = ws.iter_rows(values_only=True)
header = [text(h).upper() for h in next(rows)]


def column(name):
    if name not in header:
        raise SystemExit('No está la columna %s en el Excel.' % name)
    return header.index(name)


COL_NAME = column('NOMBRE')
COL_UPC = [column('UPC%d' % i) for i in range(1, 9)]
blocks = {}
for block in BRANCHES:
    start = column('ID_%s' % block)
    blocks[block] = {'ref': start + 2, 'qty': start + 3, 'cost': start + 4, 'price': start + 5}

issues = []  # (fila, producto, tipo, detalle)


def issue(row_no, name, kind, detail=''):
    issues.append((row_no, name, kind, detail))


items = []
seen_refs = {}
for row_no, row in enumerate(rows, start=2):
    name = text(row[COL_NAME])
    if not name:
        continue
    refs = []
    for block in BRANCHES:
        ref = text(row[blocks[block]['ref']])
        if re.fullmatch(r'\d+', ref) and ref not in refs:
            refs.append(ref)
    if not refs:
        issue(row_no, name, 'sin referencia', 'la fila no se importa')
        continue
    ref = refs[0]
    if len(refs) > 1:
        issue(row_no, name, 'varias referencias', 'se usa %s; también: %s' % (ref, ', '.join(refs[1:])))
    if ref in seen_refs:
        issue(row_no, name, 'referencia repetida', '%s ya está en la fila %s; la fila no se importa' % (ref, seen_refs[ref]))
        continue
    seen_refs[ref] = row_no
    barcodes = []
    for col in COL_UPC:
        code = text(row[col])
        if not code:
            continue
        if re.fullmatch(r'\d{8,14}', code):
            if code not in barcodes:
                barcodes.append(code)
        else:
            issue(row_no, name, 'código de barras no válido', code)
    stock, cost, prices = {}, {}, []
    for block, cols in blocks.items():
        qty = number(row[cols['qty']])
        if qty < 0:
            issue(row_no, name, 'cantidad negativa', '%s: %s, se carga 0' % (block, qty))
            qty = 0.0
        stock[block] = qty
        if number(row[cols['cost']]) > 0:
            cost[block] = round(number(row[cols['cost']]), 4)
        if number(row[cols['price']]) > 0:
            prices.append(number(row[cols['price']]))
    items.append({
        'row': row_no, 'ref': ref, 'name': name, 'barcodes': barcodes,
        'price': round(statistics.median(prices), 2) if prices else 0.0,
        'stock': stock, 'cost': cost,
    })
log('base=%s excel=%s filas válidas=%s%s' % (DB, os.path.basename(XLSX), len(items), ' (SIMULACIÓN)' if DRY_RUN else ''))

# ------------------------------------------------------------------ sucursales

Company = env['res.company']
companies, warehouses = {}, {}
for block, company_name in BRANCHES.items():
    company = Company.search([('name', '=', company_name)], limit=1)
    if not company:
        raise SystemExit('No existe la sucursal %r (bloque %s).' % (company_name, block))
    warehouse = env['stock.warehouse'].search([('company_id', '=', company.id)], order='id', limit=1)
    if not warehouse:
        raise SystemExit('La sucursal %s no tiene almacén.' % company_name)
    companies[block], warehouses[block] = company, warehouse

# ------------------------------------------------------------------ productos

IMD = env['ir.model.data'].sudo()
existing = {d.name[len('producto_'):]: d.res_id
            for d in IMD.search([('module', '=', XMLID_MODULE), ('model', '=', 'product.template')])}
templates = env['product.template'].browse(existing.values()).exists()
existing = {ref: tid for ref, tid in existing.items() if tid in templates.ids}
own_variants = templates.product_variant_ids

# Códigos de barras ya usados por productos que no son de este import.
env.cr.execute("SELECT barcode, id FROM product_product WHERE barcode IS NOT NULL")
used = {code: pid for code, pid in env.cr.fetchall() if pid not in own_variants.ids}
taken = set(used)
for item in items:
    item['barcode'] = False
    for code in item['barcodes']:
        if code in taken:
            other = 'otra fila del Excel' if code not in used else 'el producto id %s' % used[code]
            issue(item['row'], item['name'], 'código de barras repetido', '%s ya lo tiene %s' % (code, other))
            continue
        item['barcode'] = code
        taken.add(code)
        break
    extra = [c for c in item['barcodes'] if c != item['barcode']]
    if item['barcode'] and extra:
        issue(item['row'], item['name'], 'códigos de barras adicionales', 'se usa %s; también: %s' % (item['barcode'], ', '.join(extra)))
    if not item['barcode']:
        issue(item['row'], item['name'], 'sin código de barras', '')

to_create = [i for i in items if i['ref'] not in existing]
to_update = [i for i in items if i['ref'] in existing]
log('productos: %s nuevos, %s ya importados antes' % (len(to_create), len(to_update)))
kinds = Counter(kind for _r, _n, kind, _d in issues)
for kind, count in kinds.most_common():
    log('incidencia: %s = %s' % (kind, count))
for block in BRANCHES:
    units = sum(i['stock'][block] for i in items)
    log('%s -> %s: %s productos con existencias, %s unidades' % (
        block, BRANCHES[block], sum(1 for i in items if i['stock'][block] > 0), int(units)))

if REPORT:
    with open(REPORT, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.writer(fh)
        writer.writerow(['fila', 'producto', 'incidencia', 'detalle'])
        writer.writerows(issues)
    log('incidencias guardadas en %s' % REPORT)

if DRY_RUN:
    env.cr.rollback()
    raise SystemExit('Simulación terminada: no se escribió nada.')


def template_vals(item):
    return {
        'name': item['name'],
        'default_code': item['ref'],
        'barcode': item['barcode'],
        'list_price': item['price'],
    }


Template = env['product.template'].with_context(tracking_disable=True)
ref_to_tmpl = dict(existing)
for start in range(0, len(to_create), BATCH):
    chunk = to_create[start:start + BATCH]
    created = Template.create([dict(template_vals(i), detailed_type='product', sale_ok=True,
                                    purchase_ok=True, available_in_pos=True,
                                    taxes_id=[(6, 0, [])], supplier_taxes_id=[(6, 0, [])])
                               for i in chunk])
    IMD.create([{'module': XMLID_MODULE, 'name': 'producto_%s' % i['ref'], 'model': 'product.template',
                 'res_id': t.id, 'noupdate': True} for i, t in zip(chunk, created)])
    for i, t in zip(chunk, created):
        ref_to_tmpl[i['ref']] = t.id
    env.cr.commit()
    log('creados %s/%s' % (min(start + BATCH, len(to_create)), len(to_create)))

updated = 0
for item in to_update:
    tmpl = Template.browse(existing[item['ref']])
    vals = {k: v for k, v in template_vals(item).items() if tmpl[k] != v}
    if vals:
        tmpl.write(vals)
        updated += 1
env.cr.commit()
log('actualizados %s de %s productos ya importados' % (updated, len(to_update)))

variant_of = {t.id: t.product_variant_id.id
              for t in env['product.template'].browse(list(ref_to_tmpl.values()))}

# ------------------------------------------------------------------ costos y existencias

Quant = env['stock.quant'].with_context(inventory_mode=True, tracking_disable=True)
for block, company in companies.items():
    costs = {variant_of[ref_to_tmpl[i['ref']]]: i['cost'][block] for i in items if block in i['cost']}
    env['ir.property'].with_company(company)._set_multi('standard_price', 'product.product', costs)

    location = warehouses[block].lot_stock_id
    targets = {variant_of[ref_to_tmpl[i['ref']]]: i['stock'][block] for i in items}
    env.cr.execute("""
        SELECT product_id, SUM(quantity) FROM stock_quant
         WHERE location_id = %s AND product_id = ANY(%s) GROUP BY product_id
    """, (location.id, list(targets)))
    current = dict(env.cr.fetchall())
    changes = {pid: qty for pid, qty in targets.items() if abs(current.get(pid, 0.0) - qty) > 1e-6}
    pids = list(changes)
    for start in range(0, len(pids), BATCH):
        chunk = pids[start:start + BATCH]
        quants = Quant.with_company(company).create([
            {'product_id': pid, 'location_id': location.id, 'inventory_quantity': changes[pid]}
            for pid in chunk])
        quants.action_apply_inventory()
        env.cr.commit()
    log('%s: %s costos, %s existencias ajustadas, %s unidades en %s' % (
        company.name, len(costs), len(changes), int(sum(targets.values())), location.complete_name))

env.cr.commit()
log('LISTO: %s productos, %s incidencias' % (len(items), len(issues)))
