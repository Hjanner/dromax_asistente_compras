# -*- coding: utf-8 -*-
"""Automatizaciones: borradores de compra (lunes) y de traslado (diario)."""
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests.common import new_test_user, tagged
from odoo.tools import mute_logger

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
        manual = self.env["purchase.order"].create({
            "partner_id": self.drog.id,                             # de la farmacia, como su compra
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
                         ("draft", self.company, self.drog, self.buyer))
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

    def _manual_transfer(self, product, qty):
        """Un borrador de la matriz a la farmacia hecho a mano (sin la marca de la tarea)."""
        src = self.wh_matriz.lot_stock_id
        dest = self.env["stock.warehouse"].search([("company_id", "=", self.company.id)], limit=1).lot_stock_id
        return self.env["stock.picking"].with_company(self.matriz).create({
            "picking_type_id": self.wh_matriz.int_type_id.id,
            "location_id": src.id, "location_dest_id": dest.id, "origin": "Reabastecimiento a mano",
            "move_ids_without_package": [Command.create({
                "name": product.name, "product_id": product.id, "product_uom_qty": qty,
                "product_uom": product.uom_id.id, "location_id": src.id, "location_dest_id": dest.id})],
        })

    def test_daily_transfers_replace_stale_drafts(self):
        """La tarea cancela sus borradores de días anteriores; los hechos a mano no se tocan."""
        R = self.env["ai.v.reabastecimiento"]
        self.ICP.set_param(PREFIX + "auto_transfers", "True")
        self._weekly_sales(self.ibuprofeno, 10)
        self._set_stock(self.ibuprofeno, 30, company=self.matriz)
        first = R._cron_generate_transfers().filtered(lambda p: p.move_ids.product_id == self.ibuprofeno)
        self.assertEqual(first.ai_auto_replenishment_date, fields.Date.context_today(R))
        self.assertTrue(self._activity(first, self.dispatcher))
        # Otra corrida el mismo día no cancela nada ni duplica.
        self.assertFalse(R._cron_generate_transfers().filtered(lambda p: p.move_ids.product_id == self.ibuprofeno))
        self.assertEqual(first.state, "draft")

        yesterday = fields.Date.context_today(R) - timedelta(days=1)
        first.ai_auto_replenishment_date = yesterday
        manual = self._manual_transfer(self.vitamina, 2)              # borrador a mano: no se toca
        started = self._manual_transfer(self.vitamina, 1)             # de la tarea, pero ya en curso
        started.ai_auto_replenishment_date = yesterday
        started.action_confirm()
        self.assertNotEqual(started.state, "draft")

        second = R._cron_generate_transfers().filtered(lambda p: p.move_ids.product_id == self.ibuprofeno)
        self.assertEqual(first.state, "cancel")
        self.assertFalse(self._activity(first, self.dispatcher))
        self.assertTrue(first.message_ids.filtered(lambda m: "lo reemplazan" in (m.body or "")))
        self.assertEqual(manual.state, "draft")
        self.assertNotEqual(started.state, "cancel")
        # Lo del borrador cancelado vuelve a salir, en uno nuevo de hoy.
        self.assertEqual(second.move_ids.product_uom_qty, 10)
        self.assertEqual(second.ai_auto_replenishment_date, fields.Date.context_today(R))

    def test_stale_drafts_stay_if_new_ones_fail(self):
        R = self.env["ai.v.reabastecimiento"]
        self.ICP.set_param(PREFIX + "auto_transfers", "True")
        self._weekly_sales(self.ibuprofeno, 10)
        self._set_stock(self.ibuprofeno, 30, company=self.matriz)
        first = R._cron_generate_transfers().filtered(lambda p: p.move_ids.product_id == self.ibuprofeno)
        first.ai_auto_replenishment_date = fields.Date.context_today(R) - timedelta(days=1)
        with patch.object(type(R), "_create_transfers", side_effect=UserError("sin almacén")), \
                mute_logger("odoo.addons.dromax_asistente_compras.models.ai_v_reabastecimiento"):
            self.assertFalse(R._cron_generate_transfers())
        self.assertEqual(first.state, "draft")
        self.assertTrue(self._activity(first, self.dispatcher))

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
