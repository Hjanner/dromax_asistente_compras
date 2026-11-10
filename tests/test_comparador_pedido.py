# -*- coding: utf-8 -*-
"""Comparador de precios: ventana con cada droguería y pedido a un borrador de compra."""
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form
from odoo.tests.common import new_test_user, tagged

from ..models.ai_comparador_pedido import ORIGIN
from .test_analisis import AnalisisCase
from .test_oferta import _ean


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestComparadorPedido(AnalisisCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ibuprofeno.barcode = _ean("759999900071")
        cls.vitamina.barcode = _ean("759999900072")
        Partner = cls.env["res.partner"]
        cls.drog_a, cls.drog_b, cls.drog_c = (Partner.create({"name": "ZQX DROG %s" % n, "is_company": True})
                                              for n in ("A", "B", "C"))
        cls.buyer = new_test_user(
            cls.env, login="ai_comparador_buyer",
            groups="base.group_user,purchase.group_purchase_user,dromax_asistente_compras.group_ai_purchase_user",
            company_id=cls.matriz.id, company_ids=[cls.matriz.id, cls.company.id])

    def setUp(self):
        super().setUp()
        self._offer(self.drog_a, self.ibuprofeno, 2.0, 100, discount=10)
        self._offer(self.drog_a, self.ibuprofeno, 1.9, 0)                  # sin stock: no cuenta
        self._offer(self.drog_b, self.ibuprofeno, 1.5, 100)
        self._offer(self.drog_c, self.ibuprofeno, 1.7, 100)
        self._offer(self.drog_b, self.vitamina, 3.0, 50)
        self.ajeno = self.env["supplier.product.staging"].create({
            "partner_id": self.drog_a.id, "codigo_barras": _ean("759999900073"),
            "descripcion_producto": "ZQX PRODUCTO AJENO", "precio_unitario_final": 9.0, "stock_disponible": 3})

    def _comparador(self, codes):
        self.env.flush_all()
        self.env.invalidate_all()
        return self.env["ai.v.comparador"].with_user(self.buyer).search([("codigo_barras", "in", codes)])

    def test_modal_shows_each_drugstore_once(self):
        row = self._comparador([self.ibuprofeno.barcode])
        action = row.action_open_offers()
        self.assertEqual((action["target"], action["res_id"]), ("new", row.id))
        self.assertEqual(action["views"][0][0],
                         self.env.ref("dromax_asistente_compras.view_ai_v_comparador_form_modal").id)
        offers = row.mejores_ofertas_ids.sorted("orden_precio")
        self.assertEqual(offers.partner_id.ids, [self.drog_b.id, self.drog_c.id, self.drog_a.id])
        self.assertEqual(offers.mapped("precio_final"), [1.5, 1.7, 2.0])        # la de A sin stock no entra
        self.assertEqual(len(row.oferta_ids), 4)

    def test_wizard_defaults(self):
        self._weekly_sales(self.ibuprofeno, 10)                                  # necesidad de compra: 10
        rows = self._comparador([self.ibuprofeno.barcode, self.vitamina.barcode, self.ajeno.ai_barcode_norm])
        wizard = self.env["ai.comparador.pedido"].browse(rows.action_add_to_rfq()["res_id"])
        self.assertEqual(wizard.company_id, self.matriz)
        self.assertEqual(wizard.sin_catalogo, "ZQX PRODUCTO AJENO")
        ibu = wizard.line_ids.filtered(lambda l: l.product_id == self.ibuprofeno)
        vit = wizard.line_ids.filtered(lambda l: l.product_id == self.vitamina)
        self.assertEqual((ibu.drogueria_id, ibu.precio, ibu.cantidad), (self.drog_b, 1.5, 10))
        self.assertEqual((vit.drogueria_id, vit.precio, vit.cantidad), (self.drog_b, 3.0, 1))  # sin necesidad: 1
        self.assertEqual(ibu.drogueria_opciones_ids, self.drog_a | self.drog_b | self.drog_c)
        self.assertEqual(wizard.amount_total, 18.0)
        # Al cambiar la droguería, el precio sale de su oferta.
        with Form(wizard.with_user(self.buyer)) as form:
            with form.line_ids.edit(list(wizard.line_ids).index(ibu)) as line:
                line.drogueria_id = self.drog_a
        self.assertEqual(ibu.precio, 2.0)

    def test_confirm_creates_and_appends_drafts(self):
        rows = self._comparador([self.ibuprofeno.barcode, self.vitamina.barcode])
        manual = self.env["purchase.order"].with_company(self.matriz).create({"partner_id": self.drog_b.id})
        wizard = self.env["ai.comparador.pedido"].browse(rows.action_add_to_rfq()["res_id"]).with_user(self.buyer)
        ibu = wizard.line_ids.filtered(lambda l: l.product_id == self.ibuprofeno)
        ibu.write({"drogueria_id": self.drog_c.id, "cantidad": 4})
        ibu.write(ibu._offer_values())
        result = wizard.action_confirm()
        self.assertEqual(result["params"]["next"], {"type": "ir.actions.act_window_close"})
        orders = self.env["purchase.order"].search([("origin", "=", ORIGIN), ("user_id", "=", self.buyer.id)])
        self.assertEqual(orders.partner_id, self.drog_b | self.drog_c)          # una por droguería
        self.assertEqual(set(orders.mapped("company_id")), {self.matriz})
        self.assertEqual(set(orders.mapped("state")), {"draft"})
        line_c = orders.filtered(lambda o: o.partner_id == self.drog_c).order_line
        self.assertEqual((line_c.product_id, line_c.product_qty, line_c.price_unit), (self.ibuprofeno, 4, 1.7))
        self.assertFalse(manual.order_line)                                      # un borrador a mano no se toca
        # Otra vez: se suma al mismo borrador y a la misma línea.
        wizard = self.env["ai.comparador.pedido"].browse(rows[:1].action_add_to_rfq()["res_id"]).with_user(self.buyer)
        wizard.line_ids.write({"drogueria_id": self.drog_c.id, "cantidad": 2})
        wizard.line_ids.write(wizard.line_ids._offer_values())
        wizard.action_confirm()
        again = self.env["purchase.order"].search([("origin", "=", ORIGIN), ("user_id", "=", self.buyer.id)])
        self.assertEqual(again, orders)
        self.assertEqual(line_c.product_qty, 6)

    def test_confirm_checks(self):
        rows = self._comparador([self.ibuprofeno.barcode])
        wizard = self.env["ai.comparador.pedido"].browse(rows.action_add_to_rfq()["res_id"])
        sin_compras = new_test_user(self.env, login="ai_comparador_sin_compras",
                                    groups="base.group_user,dromax_asistente_compras.group_ai_purchase_user",
                                    company_id=self.matriz.id, company_ids=[self.matriz.id])
        with self.assertRaisesRegex(AccessError, "permiso de Compras"):
            wizard.with_user(sin_compras).action_confirm()
        sin_matriz = new_test_user(self.env, login="ai_comparador_sin_matriz",
                                   groups="base.group_user,purchase.group_purchase_user",
                                   company_id=self.company.id, company_ids=[self.company.id])
        with self.assertRaisesRegex(UserError, "ZQX MATRIZ"):
            wizard.with_user(sin_matriz).action_confirm()
        wizard.line_ids.cantidad = 0
        with self.assertRaisesRegex(UserError, "nada que pedir"):
            wizard.with_user(self.buyer).action_confirm()
