# -*- coding: utf-8 -*-
from odoo import Command, _, api, fields, models


class AiPickingCancel(models.TransientModel):
    """Cancelar varias operaciones de inventario a la vez (menú Acciones de la lista).

    Odoo solo permite cancelar de una en una desde el formulario. Aquí se eligen
    en la lista, se confirma en un diálogo y se cancelan las que aún no están
    hechas ni canceladas; las demás se ignoran. Corre con los permisos del
    usuario (Inventario / Usuario).
    """
    _name = "ai.picking.cancel"
    _description = "Cancelar varias operaciones de inventario"

    picking_ids = fields.Many2many("stock.picking", string="Operaciones elegidas")
    to_cancel_ids = fields.Many2many("stock.picking", compute="_compute_to_cancel",
                                     string="Se cancelan")
    to_cancel_count = fields.Integer(compute="_compute_to_cancel")
    skipped_count = fields.Integer(compute="_compute_to_cancel",
                                   string="Ya hechas o canceladas (no se tocan)")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get("active_model") == "stock.picking" and "picking_ids" in fields_list:
            res["picking_ids"] = [Command.set(self.env.context.get("active_ids") or [])]
        return res

    @api.depends("picking_ids.state")
    def _compute_to_cancel(self):
        for wizard in self:
            pickings = wizard.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))
            wizard.to_cancel_ids = pickings
            wizard.to_cancel_count = len(pickings)
            wizard.skipped_count = len(wizard.picking_ids) - len(pickings)

    def action_confirm(self):
        self.ensure_one()
        pickings = self.to_cancel_ids
        pickings.action_cancel()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Operaciones canceladas: %s", len(pickings)),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
