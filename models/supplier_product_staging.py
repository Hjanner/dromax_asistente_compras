# -*- coding: utf-8 -*-
from datetime import datetime

from odoo import api, fields, models

from ..services.barcode import is_valid_ean13, normalize_barcode

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
    ai_barcode_valid = fields.Boolean(
        "Código válido", compute="_compute_ai_barcode_norm", store=True,
        help="EAN-13 con su dígito de control correcto. Los demás no se cruzan ni se comparan.")
    ai_fecha_vencimiento = fields.Date(
        "Vencimiento", compute="_compute_ai_fecha_vencimiento", store=True,
        help="Fecha del lote leída del archivo de la droguería; vacía si no se entiende.")

    @api.depends("codigo_barras")
    def _compute_ai_barcode_norm(self):
        for line in self:
            line.ai_barcode_norm = normalize_barcode(line.codigo_barras)
            line.ai_barcode_valid = is_valid_ean13(line.ai_barcode_norm)

    @api.depends("fecha_lote")
    def _compute_ai_fecha_vencimiento(self):
        for line in self:
            line.ai_fecha_vencimiento = parse_lot_date(line.fecha_lote)
