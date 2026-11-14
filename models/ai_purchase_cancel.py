# -*- coding: utf-8 -*-
from odoo import Command, _, api, fields, models

# Estados de una solicitud de presupuesto (aún no es orden de compra).
RFQ_STATES = ("draft", "sent", "to approve")


class AiPurchaseCancel(models.TransientModel):
    """Cancelar varias solicitudes de presupuesto a la vez.

    Odoo solo permite cancelarlas de una en una desde el formulario. Se eligen
    en la lista de Compras (botón «Cancelar solicitudes» o Acciones → Cancelar
    solicitudes), se confirma en un diálogo y se cancelan las que siguen
    siendo solicitudes (borrador, enviadas o por aprobar). Las órdenes ya
    confirmadas, recibidas o canceladas no se tocan: cancelar una confirmada
    afecta recepciones y facturas, y se hace desde su ficha. Corre con los
    permisos del usuario (Compras / Usuario).
    """
    _name = "ai.purchase.cancel"
    _description = "Cancelar varias solicitudes de presupuesto"

    order_ids = fields.Many2many("purchase.order", string="Elegidas")
    to_cancel_ids = fields.Many2many("purchase.order", compute="_compute_to_cancel",
                                     string="Se cancelan")
    to_cancel_count = fields.Integer(compute="_compute_to_cancel")
    skipped_count = fields.Integer(compute="_compute_to_cancel",
                                   string="Ya confirmadas o canceladas (no se tocan)")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get("active_model") == "purchase.order" and "order_ids" in fields_list:
            res["order_ids"] = [Command.set(self.env.context.get("active_ids") or [])]
        return res

    @api.depends("order_ids.state")
    def _compute_to_cancel(self):
        for wizard in self:
            orders = wizard.order_ids.filtered(lambda o: o.state in RFQ_STATES)
            wizard.to_cancel_ids = orders
            wizard.to_cancel_count = len(orders)
            wizard.skipped_count = len(wizard.order_ids) - len(orders)

    def action_confirm(self):
        self.ensure_one()
        # Se vuelve a filtrar: otra persona pudo confirmar alguna mientras el diálogo estaba abierto.
        orders = self.order_ids.filtered(lambda o: o.state in RFQ_STATES)
        orders.button_cancel()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Solicitudes canceladas: %s", len(orders)),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
