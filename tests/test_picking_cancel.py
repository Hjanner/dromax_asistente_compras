# -*- coding: utf-8 -*-
"""Cancelar varias operaciones de inventario a la vez (Acciones → Cancelar)."""
from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestPickingCancel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login="ai_cancel_stock", groups="base.group_user,stock.group_stock_user")
        cls.product = cls.env["product.product"].create({"name": "ZQX CANCELAR", "type": "product"})
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.env.company.id)], limit=1)

    def _picking(self):
        pt = self.warehouse.int_type_id
        return self.env["stock.picking"].create({
            "picking_type_id": pt.id,
            "location_id": self.warehouse.lot_stock_id.id,
            "location_dest_id": self.warehouse.lot_stock_id.id,
            "move_ids": [Command.create({
                "name": self.product.name, "product_id": self.product.id, "product_uom_qty": 3,
                "product_uom": self.product.uom_id.id,
                "location_id": self.warehouse.lot_stock_id.id,
                "location_dest_id": self.warehouse.lot_stock_id.id})],
        })

    def _wizard(self, pickings, user=None):
        Wizard = self.env["ai.picking.cancel"].with_user(user or self.user)
        return Wizard.with_context(active_model="stock.picking", active_ids=pickings.ids).create({})

    def test_cancel_several(self):
        draft = self._picking()
        ready = self._picking()
        ready.action_confirm()
        cancelled = self._picking()
        cancelled.action_cancel()
        wizard = self._wizard(draft | ready | cancelled)
        self.assertEqual((wizard.to_cancel_count, wizard.skipped_count), (2, 1))
        self.assertEqual(wizard.to_cancel_ids, draft | ready)
        action = wizard.action_confirm()
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual((draft | ready | cancelled).mapped("state"), ["cancel"] * 3)
        self.assertEqual(draft.move_ids.state, "cancel")

    def test_nothing_to_cancel(self):
        cancelled = self._picking()
        cancelled.action_cancel()
        wizard = self._wizard(cancelled)
        self.assertEqual((wizard.to_cancel_count, wizard.skipped_count), (0, 1))

    def test_action_bound_to_transfer_list(self):
        action = self.env.ref("dromax_asistente_compras.action_ai_picking_cancel")
        self.assertEqual((action.binding_model_id.model, action.binding_view_types),
                         ("stock.picking", "list"))
        self.assertIn(self.env.ref("stock.group_stock_user"), action.groups_id)

    def test_needs_inventory_user(self):
        outsider = new_test_user(self.env, login="ai_cancel_outsider", groups="base.group_user")
        draft = self._picking()
        with self.assertRaises(AccessError):
            self._wizard(draft, user=outsider)
        self.assertEqual(draft.state, "draft")
