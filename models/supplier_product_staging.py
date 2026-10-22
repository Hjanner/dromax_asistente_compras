# -*- coding: utf-8 -*-
from datetime import datetime

from odoo import api, fields, models

from ..services.barcode import normalize_barcode

# Formatos en los que llega la fecha del lote en los .txt de las droguerías.
DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y")


def parse_lot_date(value):
    value = (value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return False


class SupplierProductStaging(models.Model):
    """Oferta de las droguerías (módulo FTP), con los datos ya listos para cruzar."""
    _inherit = "supplier.product.staging"

    ai_barcode_norm = fields.Char(
        "Código de barras normalizado", compute="_compute_ai_barcode_norm", store=True, index=True)
    ai_fecha_vencimiento = fields.Date(
        "Vencimiento", compute="_compute_ai_fecha_vencimiento", store=True,
        help="Fecha del lote leída del archivo de la droguería; vacía si no se entiende.")

    @api.depends("codigo_barras")
    def _compute_ai_barcode_norm(self):
        for line in self:
            line.ai_barcode_norm = normalize_barcode(line.codigo_barras)

    @api.depends("fecha_lote")
    def _compute_ai_fecha_vencimiento(self):
        for line in self:
            line.ai_fecha_vencimiento = parse_lot_date(line.fecha_lote)
