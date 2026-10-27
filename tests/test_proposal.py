# -*- coding: utf-8 -*-
"""Propuesta de compra semanal (ai.purchase.proposal)."""
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import new_test_user, tagged

from .test_analisis import AnalisisCase
from .test_oferta import _ean


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestProposal(AnalisisCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ibuprofeno.write({"name": "ZQX IBUPROFENO 400MG [lab zqx]", "barcode": _ean("759999900093")})
        cls.vitamina.barcode = _ean("759999900094")
        Partner = cls.env["res.partner"]
        cls.drog_a = Partner.create({"name": "ZQX DROG A", "is_company": True})
        cls.drog_b = Partner.create({"name": "ZQX DROG B", "is_company": True})
        cls.buyer = new_test_user(cls.env, login="ai_proposal_buyer",
                                  groups="base.group_user,dromax_asistente_compras.group_ai_purchase_user",
                                  company_id=cls.company.id, company_ids=[cls.company.id])
        cls.Proposal = cls.env["ai.purchase.proposal"]

    def _generate(self):
        self.env.flush_all()
        return self.Proposal._generate()

    def _line(self, proposal, product):
        line = proposal.line_ids.filtered(lambda l: l.product_id == product)
        self.assertEqual(len(line), 1)
        return line

    def test_generate_snapshot(self):
        self._offer(self.drog_a, self.ibuprofeno, 1.5, 100, lab="OTRO LAB", discount=10)
        self._offer(self.drog_b, self.ibuprofeno, 2.0, 100)
        self._weekly_sales(self.ibuprofeno, 10)
        self._set_stock(self.ibuprofeno, 3)
        proposal = self._generate()
        self.assertEqual(proposal.state, "ready")
        self.assertEqual((proposal.window_weeks, proposal.coverage_weeks), (8, 1))
        self.assertTrue(proposal.offer_date)
        self.assertTrue(proposal.name.startswith("Compra de la semana del"))
        line = self._line(proposal, self.ibuprofeno)
        self.assertEqual(line.company_id, self.company)
        self.assertEqual(line.laboratorio, "LAB ZQX")                      # del nombre, no de la droguería
        self.assertEqual((line.demanda, line.stock, line.pendiente), (10, 3, 0))
        self.assertEqual((line.cantidad_sugerida, line.cantidad_final), (7, 7))
        self.assertEqual((line.drogueria_sugerida_id, line.drogueria_id), (self.drog_a, self.drog_a))
        self.assertEqual((line.precio_sugerido, line.precio, line.descuento), (1.5, 1.5, 10))
        self.assertEqual((line.monto, line.monto_sugerido), (10.5, 10.5))
        self.assertFalse(line.ajustada)
        self.assertIn("Demanda 10/sem × 1 sem − stock 3 − pendiente 0 = 7.", line.motivo)
        self.assertEqual(line.drogueria_opciones_ids, self.drog_a | self.drog_b)

    def test_laboratory_sources(self):
        self.vitamina.default_code = "lab del campo"
        self._offer(self.drog_a, self.vitamina, 1.0, 100, lab="lab drogueria ")
        self._weekly_sales(self.vitamina, 5)
        self.assertEqual(self._line(self._generate(), self.vitamina).laboratorio, "LAB DROGUERIA")
        # Campo configurado en Ajustes (aquí uno cualquiera de texto, para la prueba).
        self.env["ir.config_parameter"].sudo().set_param("dromax_asistente_compras.laboratory_field", "default_code")
        self.assertEqual(self._line(self._generate(), self.vitamina).laboratorio, "LAB DEL CAMPO")
        # Sin campo, sin corchetes y sin droguería.
        self.env["ir.config_parameter"].sudo().set_param("dromax_asistente_compras.laboratory_field", "no_existe")
        self.env["supplier.product.staging"].search([("partner_id", "=", self.drog_a.id)]).unlink()
        line = self._line(self._generate(), self.vitamina)
        self.assertEqual(line.laboratorio, "SIN LABORATORIO")
        self.assertFalse(line.drogueria_id)
        self.assertIn("Ninguna droguería lo ofrece.", line.motivo)

    def test_new_proposal_replaces_open_ones(self):
        self._weekly_sales(self.ibuprofeno, 10)
        first = self._generate()
        second = self._generate()
        self.assertEqual((first.state, second.state), ("discarded", "ready"))
        with self.assertRaises(UserError):
            self._line(first, self.ibuprofeno).cantidad_final = 1
        first_done = self._generate()
        first_done.state = "done"
        self._generate()
        self.assertEqual(first_done.state, "done")

    def test_buyer_adjustments(self):
        self._offer(self.drog_a, self.ibuprofeno, 1.5, 100)
        self._offer(self.drog_b, self.ibuprofeno, 2.0, 100, discount=5)
        self._weekly_sales(self.ibuprofeno, 10)
        proposal = self._generate()
        line = self._line(proposal, self.ibuprofeno).with_user(self.buyer)
        line.cantidad_final = 12
        self.assertTrue(line.ajustada)
        self.assertEqual(proposal.state, "in_review")
        self.assertEqual(line.monto, 18)
        line.drogueria_id = self.drog_b                                 # cambia el precio con la oferta
        self.assertEqual((line.precio, line.descuento, line.monto), (2.0, 5, 24))
        self.assertEqual(line.monto_sugerido, 15)
        self.assertEqual(proposal.lines_changed, len(proposal.line_ids.filtered("ajustada")))
        sin_oferta = self.env["res.partner"].create({"name": "ZQX DROG SIN OFERTA", "is_company": True})
        with self.assertRaises(UserError):
            line.drogueria_id = sin_oferta
        with self.assertRaises(ValidationError):
            line.cantidad_final = -1
        line.cantidad_final = 0                                         # no pedir
        self.assertEqual(line.monto, 0)
        proposal.with_user(self.buyer).action_discard()
        with self.assertRaises(UserError):
            line.cantidad_final = 5

    def test_adjustment_metric(self):
        self._weekly_sales(self.ibuprofeno, 10)
        self._weekly_sales(self.vitamina, 10)
        proposal = self._generate()
        lines = proposal.line_ids.filtered(lambda l: l.product_id in (self.ibuprofeno | self.vitamina))
        base = proposal.adjustment_pct
        total = sum(proposal.line_ids.mapped("cantidad_sugerida"))
        lines[0].cantidad_final += 5
        lines[1].cantidad_final -= 5
        self.assertAlmostEqual(proposal.adjustment_pct, base + 100.0 * 10 / total)

    def test_buyer_generates_for_all_pharmacies(self):
        otra = self.env["res.company"].create({"name": "ZQX FARMACIA 2"})
        self._set_stock(self.ibuprofeno, 30, company=otra)
        self._weekly_sales(self.ibuprofeno, 10)
        self.env.flush_all()
        action = self.Proposal.with_user(self.buyer).action_generate()
        proposal = self.Proposal.browse(action["res_id"])
        self.assertEqual(self._line(proposal, self.ibuprofeno).excedente_otras, 30)
        # El comprador solo ve las líneas de sus farmacias.
        lines = self.env["ai.purchase.proposal.line"].with_user(self.buyer).search([("proposal_id", "=", proposal.id)])
        self.assertEqual(lines.company_id, self.company)
        outsider = new_test_user(self.env, login="ai_proposal_outsider", groups="base.group_user")
        with self.assertRaises(AccessError):
            self.Proposal.with_user(outsider).action_generate()
        with self.assertRaises(AccessError):
            proposal.with_user(outsider).read(["name"])

    def test_cron(self):
        cron = self.env.ref("dromax_asistente_compras.cron_ai_purchase_proposal")
        self.assertEqual((cron.interval_number, cron.interval_type), (1, "weeks"))
        self.assertEqual(cron.nextcall.weekday(), 0)
        self._weekly_sales(self.ibuprofeno, 10)
        self.env.flush_all()
        before = self.Proposal.search_count([])
        cron.method_direct_trigger()
        self.assertEqual(self.Proposal.search_count([]), before + 1)
