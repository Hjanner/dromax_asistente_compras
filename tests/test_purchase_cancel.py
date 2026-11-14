# -*- coding: utf-8 -*-
"""Cancelar varias solicitudes de presupuesto a la vez (lista de Compras)."""
from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestPurchaseCancel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login="ai_cancel_buyer", groups="base.group_user,purchase.group_purchase_user")
        cls.vendor = cls.env["res.partner"].create({"name": "ZQX DROGUERIA CANCELAR"})
        cls.product = cls.env["product.product"].create({"name": "ZQX CANCELAR COMPRA", "type": "consu"})

    def _order(self):
        return self.env["purchase.order"].create({
            "partner_id": self.vendor.id,
            "order_line": [Command.create({"product_id": self.product.id, "product_qty": 2, "price_unit": 5})],
        })

    def _wizard(self, orders, user=None):
        Wizard = self.env["ai.purchase.cancel"].with_user(user or self.user)
        return Wizard.with_context(active_model="purchase.order", active_ids=orders.ids).create({})

    def test_cancel_only_rfqs(self):
        draft = self._order()
        sent = self._order()
        sent.write({"state": "sent"})
        confirmed = self._order()
        confirmed.button_confirm()
        cancelled = self._order()
        cancelled.button_cancel()
        wizard = self._wizard(draft | sent | confirmed | cancelled)
        self.assertEqual((wizard.to_cancel_count, wizard.skipped_count), (2, 2))
        self.assertEqual(wizard.to_cancel_ids, draft | sent)
        action = wizard.action_confirm()
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual((draft | sent | confirmed | cancelled).mapped("state"),
                         ["cancel", "cancel", "purchase", "cancel"])

    def test_confirmed_meanwhile_is_not_cancelled(self):
        draft = self._order()
        wizard = self._wizard(draft)
        draft.button_confirm()
        wizard.action_confirm()
        self.assertEqual(draft.state, "purchase")

    def test_list_button_opens_wizard(self):
        orders = self._order() | self._order()
        action = orders.with_user(self.user).action_ai_cancel_rfqs()
        self.assertEqual(action["res_model"], "ai.purchase.cancel")
        wizard = self.env["ai.purchase.cancel"].with_user(self.user).with_context(action["context"]).create({})
        self.assertEqual(wizard.order_ids, orders)

    def test_action_bound_to_purchase_list(self):
        action = self.env.ref("dromax_asistente_compras.action_ai_purchase_cancel")
        self.assertEqual((action.binding_model_id.model, action.binding_view_types),
                         ("purchase.order", "list"))
        self.assertIn(self.env.ref("purchase.group_purchase_user"), action.groups_id)

    def test_needs_purchase_user(self):
        outsider = new_test_user(self.env, login="ai_cancel_no_buyer", groups="base.group_user")
        draft = self._order()
        with self.assertRaises(AccessError):
            self._wizard(draft, user=outsider)
        self.assertEqual(draft.state, "draft")
