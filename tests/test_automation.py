# -*- coding: utf-8 -*-
"""Automatizaciones: borradores de compra (lunes) y de traslado (diario)."""
from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import new_test_user, tagged

from ..services.network_sql import MATRIX_PARAM
from .test_analisis import AnalisisCase
from .test_oferta import _ean

PREFIX = "dromax_asistente_compras."


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestAutomation(AnalisisCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ibuprofeno.barcode = _ean("759999900193")
        cls.vitamina.barcode = _ean("759999900194")
        cls.drog = cls.env["res.partner"].create({"name": "ZQX DROG AUTO", "is_company": True})
        cls.buyer = new_test_user(
            cls.env, login="ai_auto_buyer", groups="base.group_user,purchase.group_purchase_user",
            company_id=cls.matriz.id, company_ids=[cls.matriz.id, cls.company.id])
        cls.dispatcher = new_test_user(
            cls.env, login="ai_auto_dispatcher",
            groups="base.group_user,stock.group_stock_user,dromax_asistente_compras.group_ai_purchase_user",
            company_id=cls.matriz.id, company_ids=[cls.matriz.id, cls.company.id])
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        cls.ICP.set_param(PREFIX + "purchase_responsible_id", str(cls.buyer.id))
        cls.ICP.set_param(PREFIX + "transfer_responsible_id", str(cls.dispatcher.id))
        cls.ICP.set_param(MATRIX_PARAM, str(cls.matriz.id))
        cls.Proposal = cls.env["ai.purchase.proposal"]
        cls.wh_matriz = cls.env["stock.warehouse"].search([("company_id", "=", cls.matriz.id)], limit=1)
        cls.wh_matriz.int_type_id.active = True

    def _ours(self, orders):
        """Las órdenes con nuestros productos (la base de pruebas puede traer otras faltas)."""
        return orders.filtered(lambda o: o.order_line.product_id & (self.ibuprofeno | self.vitamina))

    def _activity(self, record, user):
        return record.activity_ids.filtered(lambda a: a.user_id == user)

    # -- compra semanal ---------------------------------------------------------

    def test_weekly_purchase_without_auto_only_notifies(self):
        self._offer(self.drog, self.ibuprofeno, 1.5, 100)
        self._weekly_sales(self.ibuprofeno, 10)
        self.env.flush_all()
        proposal = self.Proposal._cron_generate()
        self.assertFalse(proposal.purchase_order_ids)
        activity = self._activity(proposal, self.buyer)
        self.assertEqual(activity.summary, "Revisar la compra de la semana")
        self.assertIn("Generar borradores", activity.note)

    def test_weekly_purchase_auto(self):
        self.ICP.set_param(PREFIX + "auto_rfq", "True")
        self._offer(self.drog, self.ibuprofeno, 1.5, 100)
        self._offer(self.drog, self.vitamina, 2.0, 100)
        self._weekly_sales(self.ibuprofeno, 10)                     # le faltan 10
        self._weekly_sales(self.vitamina, 4)                        # le faltan 4, pero ya hay una solicitud
        manual = self.env["purchase.order"].with_company(self.matriz).create({
            "partner_id": self.drog.id, "company_id": self.matriz.id,
            "order_line": [Command.create({"product_id": self.vitamina.id, "product_qty": 2, "price_unit": 2})]})
        # Un borrador de una propuesta anterior que nadie confirmó: se cancela.
        self.env.flush_all()
        old = self.Proposal._generate()
        old_lines = old.line_ids.filtered(lambda l: l.product_id == self.ibuprofeno)
        stale = old_lines._create_orders(self.buyer, "prueba")
        self.assertEqual(stale.state, "draft")

        proposal = self.Proposal._cron_generate()
        self.assertEqual(stale.state, "cancel")
        self.assertEqual(manual.state, "draft")                     # lo hecho a mano no se toca
        orders = self._ours(proposal.purchase_order_ids)
        self.assertEqual(len(orders), 1)
        self.assertEqual((orders.state, orders.company_id, orders.partner_id, orders.user_id),
                         ("draft", self.matriz, self.drog, self.buyer))
        self.assertEqual(orders.order_line.mapped("product_id"), self.ibuprofeno)
        self.assertEqual((orders.order_line.product_qty, orders.order_line.price_unit), (10, 1.5))
        # La vitamina queda fuera (ya está en una solicitud) y se avisa en la actividad.
        vit = proposal.line_ids.filtered(lambda l: l.product_id == self.vitamina)
        self.assertFalse(vit.purchase_line_id)
        note = self._activity(proposal, self.buyer).note
        self.assertIn(orders.name, note)
        self.assertIn("Ya están en solicitudes sin confirmar", note)
        self.assertIn(stale.name, note)
        self.assertIn("generó", proposal.message_ids.mapped("body")[0])

    def test_weekly_cron_auto(self):
        self.ICP.set_param(PREFIX + "auto_rfq", "True")
        self._offer(self.drog, self.ibuprofeno, 1.5, 100)
        self._weekly_sales(self.ibuprofeno, 10)
        self.env.flush_all()
        before = self.Proposal.search([], limit=1)
        self.env.ref("dromax_asistente_compras.cron_ai_purchase_proposal").method_direct_trigger()
        proposal = self.Proposal.search([], limit=1)
        self.assertNotEqual(proposal, before)
        self.assertTrue(self._ours(proposal.purchase_order_ids))

    # -- reabastecimiento diario -------------------------------------------------

    def test_daily_transfers(self):
        cron = self.env.ref("dromax_asistente_compras.cron_ai_replenishment")
        self.assertEqual((cron.interval_number, cron.interval_type), (1, "days"))
        R = self.env["ai.v.reabastecimiento"]
        self._weekly_sales(self.ibuprofeno, 10)                     # a la farmacia le faltan 10
        self._set_stock(self.ibuprofeno, 30, company=self.matriz)
        self.assertFalse(R._cron_generate_transfers())              # apagado: no hace nada
        self.ICP.set_param(PREFIX + "auto_transfers", "True")
        pickings = R._cron_generate_transfers()
        ours = pickings.filtered(lambda p: p.move_ids.product_id == self.ibuprofeno)
        self.assertEqual(len(ours), 1)
        self.assertEqual((ours.state, ours.company_id, ours.location_dest_id.company_id),
                         ("draft", self.matriz, self.company))
        self.assertEqual(ours.move_ids.product_uom_qty, 10)
        activity = self._activity(ours, self.dispatcher)
        self.assertIn(self.company.name, activity.summary)
        # Al día siguiente no se duplica: el borrador ya cuenta como en camino.
        again = R._cron_generate_transfers()
        self.assertFalse(again.filtered(lambda p: p.move_ids.product_id == self.ibuprofeno))

    def test_run_now_buttons(self):
        settings = self.env["res.config.settings"].create({})
        with self.assertRaises(UserError):
            settings.action_ai_run_replenishment()                  # el ajuste está apagado
        self._weekly_sales(self.ibuprofeno, 10)
        self._set_stock(self.ibuprofeno, 30, company=self.matriz)
        settings = self.env["res.config.settings"].create({"ai_purchase_auto_transfers": True})
        action = settings.action_ai_run_replenishment()
        self.assertEqual(action["res_model"], "stock.picking")
        action = self.env["res.config.settings"].create({}).action_ai_run_weekly_purchase()
        self.assertEqual(action["res_model"], "ai.purchase.proposal")
