# -*- coding: utf-8 -*-
from odoo import api, fields, models

from ..services.barcode import normalize_barcode


class ProductProduct(models.Model):
    _inherit = "product.product"

    ai_barcode_norm = fields.Char(
        "Código de barras normalizado", compute="_compute_ai_barcode_norm", store=True, index=True,
        help="EAN-13 sin espacios ni ceros de más; con él se cruza la oferta de las droguerías.")

    @api.depends("barcode")
    def _compute_ai_barcode_norm(self):
        for product in self:
            product.ai_barcode_norm = normalize_barcode(product.barcode)
