# -*- coding: utf-8 -*-
"""Reabastecimiento desde la sucursal matriz (ai.v.reabastecimiento)."""
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user, tagged

from ..models.ai_v_reabastecimiento import MATRIX_PARAM
from ..services import presets
from ..services.prompt import build_system_prompt
from .test_analisis import AnalisisCase


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestReabastecimiento(AnalisisCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.otra = cls.env["res.company"].create({"name": "ZQX FARMACIA 2"})   # la matriz viene de AnalisisCase
        cls.env["ir.config_parameter"].sudo().set_param(MATRIX_PARAM, str(cls.matriz.id))
        cls.wh_matriz = cls.env["stock.warehouse"].search([("company_id", "=", cls.matriz.id)], limit=1)
        cls.wh_matriz.int_type_id.active = True
        cls.ibuprofeno.with_company(cls.matriz).standard_price = 2.0
        companies = cls.company | cls.matriz | cls.otra
        cls.user = new_test_user(
            cls.env, login="ai_reabastecimiento",
            groups="base.group_user,stock.group_stock_user,dromax_asistente_compras.group_ai_purchase_user",
            company_id=cls.company.id, company_ids=companies.ids)
        cls.R = cls.env["ai.v.reabastecimiento"].with_user(cls.user).with_context(
            allowed_company_ids=companies.ids)

    def _so(self, company, product, qty, weeks=8):
        self._so_weekly(company, product, qty, weeks)

    def _rows(self, product):
        self.env.flush_all()
        self.env.invalidate_all()
        return {r.company_id: r for r in self.R.search([("product_id", "=", product.id)])}

    def test_line(self):
        self._weekly_sales(self.ibuprofeno, 10)                     # venta de la farmacia: 10/sem
        self._set_stock(self.ibuprofeno, 3)
        self._set_stock(self.ibuprofeno, 30, company=self.matriz)
        row = self._rows(self.ibuprofeno)[self.company]
        self.assertEqual(row.matriz_id, self.matriz)
        self.assertEqual(row.estado, "por_agotarse")
        self.assertEqual((row.demanda_semanal, row.stock, row.falta, row.cantidad), (10, 3, 7, 7))
        self.assertFalse(row.parcial)
        self.assertEqual((row.stock_matriz, row.disponible_matriz), (30, 30))
        self.assertEqual((row.costo, row.valor), (2.0, 14.0))
        self.assertEqual(row.id, self.ibuprofeno.id * 1000 + self.company.id)
        # La matriz no se manda a sí misma ni aparece lo que no le falta a nadie.
        self.assertNotIn(self.matriz, self._rows(self.ibuprofeno))
        self.assertFalse(self._rows(self.vitamina))

    def test_allocation_by_urgency_and_matrix_reserve(self):
        self._weekly_sales(self.ibuprofeno, 10)                     # falta 7 (cobertura 0,3)
        self._set_stock(self.ibuprofeno, 3)
        self._so(self.otra, self.ibuprofeno, 2)                     # en cero: falta 2 (cobertura 0)
        self._set_stock(self.ibuprofeno, 5, company=self.matriz)
        rows = self._rows(self.ibuprofeno)
        self.assertEqual(rows[self.otra].estado, "cero")
        self.assertEqual((rows[self.otra].cantidad, rows[self.otra].parcial), (2, False))   # la más urgente primero
        self.assertEqual((rows[self.company].cantidad, rows[self.company].parcial), (3, True))
        # La matriz se guarda su propia venta de la semana: 5 − 4 = 1, solo para la más urgente.
        self._so(self.matriz, self.ibuprofeno, 4)
        rows = self._rows(self.ibuprofeno)
        self.assertEqual(rows[self.otra].disponible_matriz, 1)
        self.assertEqual((rows[self.otra].cantidad, rows[self.otra].parcial), (1, True))
        self.assertNotIn(self.company, rows)

    def test_sporadic_sale_at_zero(self):
        """Una venta suelta deja la demanda depurada en 0, pero con stock en cero se repone su media."""
        self._pos_sale(self.vitamina, 8, self._week_date(2))        # 8 en una semana de 8: media 1
        self._set_stock(self.vitamina, 10, company=self.matriz)
        row = self._rows(self.vitamina)[self.company]
        self.assertEqual((row.estado, row.demanda_semanal, row.cantidad), ("cero", 1, 1))
        self._set_stock(self.vitamina, 1)                            # con stock ya no entra
        self.assertFalse(self._rows(self.vitamina))

    def _thresholds(self, point, margin):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("dromax_asistente_compras.reorder_point_pct", str(point))
        ICP.set_param("dromax_asistente_compras.safety_margin_pct", str(margin))

    def test_reorder_point_and_safety_margin(self):
        """Entra al bajar al punto de pedido y se repone hasta la cobertura más el margen."""
        self._thresholds(50, 20)
        self._weekly_sales(self.ibuprofeno, 10)                     # objetivo 10 × 1,2 = 12; punto 5
        self._set_stock(self.ibuprofeno, 30, company=self.matriz)
        self._set_stock(self.ibuprofeno, 6)                         # 6 > 5: todavía no
        self.assertFalse(self._rows(self.ibuprofeno))
        self._set_stock(self.ibuprofeno, -1)                        # _set_stock suma: queda en 5
        row = self._rows(self.ibuprofeno)[self.company]
        self.assertEqual((row.estado, row.falta, row.cantidad), ("por_agotarse", 7, 7))
        self._set_stock(self.ibuprofeno, -5)
        self.assertEqual(self._rows(self.ibuprofeno)[self.company].falta, 12)
        # Venta de 3/sem: el margen se redondea (3 × 1,2 = 3,6 → 4).
        self._so(self.otra, self.ibuprofeno, 3)                     # otra farmacia, en cero
        self.assertEqual(self._rows(self.ibuprofeno)[self.otra].falta, 4)

    def test_safety_margin_low_rotation(self):
        """El margen se redondea: en poca venta no duplica el pedido."""
        self._thresholds(50, 20)
        self._pos_sale(self.vitamina, 8, self._week_date(2))        # media 1/sem, en cero
        self._set_stock(self.vitamina, 10, company=self.matriz)
        self.assertEqual(self._rows(self.vitamina)[self.company].cantidad, 1)
        self._thresholds(100, 100)                                  # con 100 % ya son 2
        self.assertEqual(self._rows(self.vitamina)[self.company].cantidad, 2)

    def test_settings_keep_zero_margin(self):
        """Odoo guarda un entero 0 como vacío: el margen 0 se guarda explícito."""
        settings = self.env["res.config.settings"].create({
            "ai_purchase_safety_margin_pct": 0, "ai_purchase_reorder_point_pct": 40})
        settings.execute()
        ICP = self.env["ir.config_parameter"].sudo()
        self.assertEqual(ICP.get_param("dromax_asistente_compras.safety_margin_pct"), "0")
        self.assertEqual(ICP.get_param("dromax_asistente_compras.reorder_point_pct"), "40")
        self.assertEqual(self.env["res.config.settings"].create({}).ai_purchase_safety_margin_pct, 0)

    def test_generate_transfers(self):
        self._weekly_sales(self.ibuprofeno, 10)
        self._set_stock(self.ibuprofeno, 3)
        self._so(self.otra, self.ibuprofeno, 2)
        self._set_stock(self.ibuprofeno, 30, company=self.matriz)
        rows = self.R.browse(list(r.id for r in self._rows(self.ibuprofeno).values()))
        action = rows.action_generate_transfers()
        pickings = self.env["stock.picking"].browse(action["domain"][0][2])
        self.assertEqual(len(pickings), 2)                          # uno por farmacia
        for picking in pickings:
            self.assertEqual(picking.company_id, self.matriz)
            self.assertEqual(picking.picking_type_id, self.wh_matriz.int_type_id)
            self.assertEqual(picking.location_id, self.wh_matriz.lot_stock_id)
            self.assertEqual(picking.state, "draft")
            self.assertTrue(picking.is_interbranch_draft)
            self.assertTrue(picking.origin.startswith("Reabastecimiento"))
        dest = {p.location_dest_id.company_id: p for p in pickings}
        self.assertEqual(dest[self.company].move_ids.product_uom_qty, 7)
        self.assertEqual(dest[self.otra].move_ids.product_uom_qty, 2)
        # Lo que va en borrador ya cuenta como en camino: no se vuelve a sugerir.
        self.assertFalse(self._rows(self.ibuprofeno))
        with self.assertRaises(UserError):
            rows.action_generate_transfers()
        # El botón del módulo de traslados arma la cadena por tránsito a partir del borrador.
        res = dest[self.company].action_generate_definitive_transfer()
        chain = self.env["stock.picking"].browse(res["domain"][0][2])
        self.assertIn("transit", chain.location_dest_id.mapped("usage"))
        self.assertEqual(set(chain.move_ids.mapped("price_unit")), {2.0})
        self.assertFalse(self._rows(self.ibuprofeno))
        # Si se cancela un borrador, la línea vuelve.
        dest[self.otra].action_cancel()
        self.assertEqual(set(self._rows(self.ibuprofeno)), {self.otra})

    def test_generate_checks(self):
        self._weekly_sales(self.ibuprofeno, 10)
        self._set_stock(self.ibuprofeno, 30, company=self.matriz)
        row = self._rows(self.ibuprofeno)[self.company]
        sin_inventario = new_test_user(
            self.env, login="ai_reabastecimiento_sin_stock",
            groups="base.group_user,dromax_asistente_compras.group_ai_purchase_user",
            company_id=self.company.id, company_ids=(self.company | self.matriz).ids)
        with self.assertRaises(AccessError):
            row.with_user(sin_inventario).action_generate_transfers()
        sin_matriz = new_test_user(
            self.env, login="ai_reabastecimiento_sin_matriz",
            groups="base.group_user,stock.group_stock_user,dromax_asistente_compras.group_ai_purchase_user",
            company_id=self.company.id, company_ids=self.company.ids)
        with self.assertRaisesRegex(UserError, "ZQX MATRIZ"):
            row.with_user(sin_matriz).with_context(allowed_company_ids=self.company.ids).action_generate_transfers()
        self.wh_matriz.int_type_id.active = False
        with self.assertRaisesRegex(UserError, "Traslados internos"):
            row.action_generate_transfers()

    def test_agent_knows_the_view(self):
        self.assertIn("ai_v_reabastecimiento", build_system_prompt(self.env))
        self.assertEqual(presets.get_preset("reabastecer")["level"], "medium")
