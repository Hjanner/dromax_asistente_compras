# -*- coding: utf-8 -*-
"""Las líneas de la propuesta pasan a ser de cada sucursal que recibe.

Antes toda la compra era de la matriz; ahora hay líneas de cada farmacia y
de la matriz. La regla de las líneas usaba las empresas marcadas en el
selector, así que un comprador con solo Dromax marcada no vería las compras
de las farmacias. Ahora usa todas las sucursales permitidas del usuario. La
regla está en un bloque noupdate: se cambia aquí, solo si conserva el
dominio anterior.
"""
from odoo import SUPERUSER_ID, api

OLD = "[('company_id', 'in', company_ids)]"
NEW = "[('company_id', 'in', user.company_ids.ids)]"


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    rule = env.ref("dromax_asistente_compras.rule_ai_purchase_proposal_line_company", raise_if_not_found=False)
    if rule and rule.domain_force == OLD:
        rule.domain_force = NEW
