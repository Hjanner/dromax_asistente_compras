# -*- coding: utf-8 -*-
from odoo import fields, models


class PurchaseOrder(models.Model):
    """Compras del inventario de la matriz a nombre de una farmacia.

    La matriz (Dromax) es una droguería y no compra a su nombre: repone su
    inventario con órdenes a nombre de una farmacia. La orden queda en la
    empresa de la matriz (la mercancía entra a su almacén) y este campo dice a
    nombre de quién sale: lo ve el comprador en la orden y la droguería en el
    PDF (encabezado con los datos de esa farmacia).
    """
    _inherit = "purchase.order"

    ai_a_nombre_de_id = fields.Many2one(
        "res.company", "A nombre de", copy=True, tracking=True,
        help="Farmacia a cuyo nombre se hace la compra cuando no es la empresa que la recibe (p. ej. "
             "el inventario de Dromax). El PDF de la solicitud y de la orden sale con sus datos.")

    def action_ai_cancel_rfqs(self):
        """Botón de la lista: abre el diálogo para cancelar las solicitudes elegidas."""
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "dromax_asistente_compras.action_ai_purchase_cancel")
        action["context"] = {"active_model": "purchase.order", "active_ids": self.ids}
        return action
