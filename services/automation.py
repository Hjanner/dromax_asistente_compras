# -*- coding: utf-8 -*-
"""Ajustes de las automatizaciones (borradores de compra y de traslado).

Las tareas programadas corren siempre; estos ajustes deciden si además crean
borradores y a quién le dejan la actividad para revisarlos:

- auto_rfq: al calcular la propuesta semanal, crear los borradores de compra.
- purchase_responsible_id: quién revisa la compra (actividad en la propuesta
  y comprador de las órdenes).
- auto_transfers: cada día, crear los borradores de traslado desde la matriz.
- transfer_responsible_id: quién despacha (actividad en cada borrador).
"""
from markupsafe import Markup, escape

from .config import get_setting

AUTO_RFQ = "auto_rfq"
PURCHASE_RESPONSIBLE = "purchase_responsible_id"
AUTO_TRANSFERS = "auto_transfers"
TRANSFER_RESPONSIBLE = "transfer_responsible_id"


def is_enabled(env, name):
    return str(get_setting(env, name) or "").strip().lower() in ("1", "true", "yes", "si", "sí")


def responsible(env, name):
    """Usuario activo del ajuste, o un registro vacío si no hay o no es válido."""
    value = str(get_setting(env, name) or "").strip()
    user = env["res.users"].sudo().browse(int(value)) if value.isdigit() else env["res.users"]
    return user.exists().filtered("active")


def html_list(title, items):
    """Párrafo con título y lista con viñetas, escapando el texto."""
    body = Markup("<p>%s</p>") % title
    if items:
        body += Markup("<ul>%s</ul>") % Markup("").join(Markup("<li>%s</li>") % escape(i) for i in items)
    return body
