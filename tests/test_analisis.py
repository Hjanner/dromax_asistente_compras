# -*- coding: utf-8 -*-
"""Vistas de análisis: ai_v_rotacion, ai_v_stock y ai_v_pendiente."""
from datetime import datetime, time, timedelta

import pytz

from odoo import Command, fields
from odoo.tests import Form
from odoo.tests.common import TransactionCase, new_test_user, tagged

from ..services.prompt import build_system_prompt
from .test_oferta import _ean

TZ = "America/Caracas"


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestAnalisis(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.partner_id.tz = TZ
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param("dromax_asistente_compras.rotation_window_weeks", "8")
        ICP.set_param("dromax_asistente_compras.outlier_mad_factor", "3")
        ICP.set_param("dromax_asistente_compras.coverage_weeks", "1")
        ICP.set_param("dromax_asistente_compras.excess_weeks", "4")
        Product = cls.env["product.product"]
        cls.ibuprofeno = Product.create({"name": "ZQX IBUPROFENO 400MG", "detailed_type": "product",
                                         "default_code": "ZQX1"})
        cls.vitamina = Product.create({"name": "ZQX VITAMINA C", "detailed_type": "product"})
        cls.dozen = cls.env.ref("uom.product_uom_dozen")
        cls.cliente = cls.env["res.partner"].create({"name": "ZQX ALCALDÍA"})
        cls.drogueria = cls.env["res.partner"].create({"name": "ZQX DROGUERÍA", "is_company": True})
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        config = cls.env["pos.config"].create({"name": "ZQX TPV"})
        cls.session = cls.env["pos.session"].create({"config_id": config.id, "user_id": cls.env.uid})

    # -- utilidades ---------------------------------------------------------

    def _week_date(self, weeks_ago, weekday=2, hour=15):
        """Fecha UTC (naive, como la guarda Odoo) del día `weekday` de la semana
        local de hace `weeks_ago` semanas (0 = la semana en curso)."""
        local_now = datetime.now(pytz.timezone(TZ))
        monday = local_now.date() - timedelta(days=local_now.weekday())
        day = monday - timedelta(weeks=weeks_ago) + timedelta(days=weekday)
        local = pytz.timezone(TZ).localize(datetime.combine(day, time(hour)))
        return local.astimezone(pytz.utc).replace(tzinfo=None)

    def _pos_sale(self, product, qty, when, **line_vals):
        order = self.env["pos.order"].create({
            "session_id": self.session.id, "amount_tax": 0, "amount_total": qty,
            "amount_paid": qty, "amount_return": 0,
            "lines": [Command.create(dict({
                "product_id": product.id, "qty": qty, "price_unit": 1,
                "price_subtotal": qty, "price_subtotal_incl": qty}, **line_vals))],
        })
        order.write({"state": "paid", "date_order": when})
        return order

    def _sale_order(self, product, qty, when, uom=None, confirm=True):
        order = self.env["sale.order"].create({
            "partner_id": self.cliente.id,
            "order_line": [Command.create({"product_id": product.id, "product_uom_qty": qty,
                                           "product_uom": (uom or product.uom_id).id})],
        })
        if confirm:
            order.action_confirm()
        order.date_order = when
        return order

    def _purchase(self, product, qty, uom=None, confirm=True):
        order = self.env["purchase.order"].create({
            "partner_id": self.drogueria.id,
            "order_line": [Command.create({"product_id": product.id, "product_qty": qty,
                                           "product_uom": (uom or product.uom_id).id, "price_unit": 1})],
        })
        if confirm:
            order.button_confirm()
        return order

    def _receive(self, order, units, backorder=True):
        """Recibe `units` (en la unidad del producto) de la recepción abierta."""
        picking = order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))
        move = picking.move_ids
        move.quantity = move.product_id.uom_id._compute_quantity(units, move.product_uom)
        res = picking.button_validate()
        wizard = Form(self.env[res["res_model"]].with_context(res["context"])).save()
        wizard.process() if backorder else wizard.process_cancel_backorder()

    def _row(self, model, product, company=None):
        # Las vistas reutilizan los mismos ids: sin vaciar la caché se leerían valores viejos.
        self.env.flush_all()
        self.env.invalidate_all()
        rows = self.env[model].search([("product_id", "=", product.id),
                                       ("company_id", "=", (company or self.company).id)])
        self.assertLessEqual(len(rows), 1)
        return rows

    # -- rotación -----------------------------------------------------------

    def test_rotation_discards_atypical_weeks(self):
        weekly = [10, 12, 11, 10, 13, 12, 11, 60]  # semanas 1..8 atrás; 60 es atípica
        for weeks_ago, qty in enumerate(weekly, start=1):
            self._pos_sale(self.ibuprofeno, qty, self._week_date(weeks_ago))
        self._pos_sale(self.ibuprofeno, 500, self._week_date(0))   # semana en curso: no entra
        self._pos_sale(self.ibuprofeno, 500, self._week_date(9))   # fuera de la ventana
        row = self._row("ai.v.rotacion", self.ibuprofeno)
        self.assertEqual(row.semanas, 8)
        self.assertEqual(row.semanas_con_venta, 8)
        self.assertEqual(row.venta_total, 139)
        self.assertAlmostEqual(row.demanda_media, 17.38)
        self.assertAlmostEqual(row.demanda_mediana, 11.5)
        self.assertAlmostEqual(row.mad, 1.0)
        self.assertEqual(row.semanas_atipicas, 1)
        self.assertAlmostEqual(row.demanda_depurada, 11.29)  # 79 / 7
        self.assertEqual(row.producto, "ZQX IBUPROFENO 400MG")
        self.assertEqual(row.codigo, "ZQX1")

    def test_rotation_weeks_follow_local_time(self):
        # Domingo 22:00 en Caracas = lunes 02:00 UTC: sigue siendo la semana del domingo.
        self._pos_sale(self.ibuprofeno, 7, self._week_date(1, weekday=6, hour=22))
        row = self._row("ai.v.rotacion", self.ibuprofeno)
        self.assertEqual(row.venta_total, 7)
        self.assertEqual(row.hasta, row.desde + timedelta(weeks=8, days=-1))

    def test_rotation_window_setting(self):
        self._pos_sale(self.ibuprofeno, 4, self._week_date(6))
        self.assertTrue(self._row("ai.v.rotacion", self.ibuprofeno))
        self.env["ir.config_parameter"].sudo().set_param(
            "dromax_asistente_compras.rotation_window_weeks", "4")
        self.assertFalse(self._row("ai.v.rotacion", self.ibuprofeno))

    def test_rotation_sale_orders(self):
        # 1 docena = 12 unidades; el borrador no cuenta; el ticket del TPV que liquida
        # el pedido no se suma otra vez.
        order = self._sale_order(self.vitamina, 1, self._week_date(2), uom=self.dozen)
        self._sale_order(self.vitamina, 50, self._week_date(2), confirm=False)
        self._pos_sale(self.vitamina, 12, self._week_date(2),
                       sale_order_origin_id=order.id, sale_order_line_id=order.order_line.id)
        row = self._row("ai.v.rotacion", self.vitamina)
        self.assertEqual(row.venta_pedidos, 12)
        self.assertEqual(row.venta_tpv, 0)
        # Una sola semana con venta: es esporádica y la demanda depurada queda en 0.
        self.assertEqual(row.semanas_con_venta, 1)
        self.assertEqual(row.semanas_atipicas, 1)
        self.assertEqual(row.demanda_depurada, 0)
        self.assertAlmostEqual(row.demanda_media, 1.5)

    def test_rotation_keeps_small_sporadic_sales(self):
        # Con MAD = 0 se usa 1: una semana de 2 unidades (≤ 3 × 1) no es atípica.
        self._pos_sale(self.vitamina, 2, self._week_date(3))
        row = self._row("ai.v.rotacion", self.vitamina)
        self.assertEqual(row.semanas_atipicas, 0)
        self.assertAlmostEqual(row.demanda_depurada, 0.25)

    # -- stock --------------------------------------------------------------

    def test_stock(self):
        self.env["stock.quant"].with_context(inventory_mode=True).create({
            "product_id": self.ibuprofeno.id, "location_id": self.warehouse.lot_stock_id.id,
            "inventory_quantity": 30,
        }).action_apply_inventory()
        row = self._row("ai.v.stock", self.ibuprofeno)
        self.assertEqual((row.stock, row.reservado, row.disponible), (30, 0, 30))
        self.assertEqual(row.farmacia, self.company.name)
        self.assertFalse(self._row("ai.v.stock", self.vitamina))

    # -- pendiente ----------------------------------------------------------

    def test_pending_with_partial_receipt(self):
        order = self._purchase(self.ibuprofeno, 2, uom=self.dozen)   # 24 unidades
        self.assertEqual(self._row("ai.v.pendiente", self.ibuprofeno).pendiente, 24)
        self._receive(order, 12)                                      # llega 1 docena
        row = self._row("ai.v.pendiente", self.ibuprofeno)
        self.assertEqual((row.pendiente, row.ordenes), (12, 1))
        self.assertEqual(row.proxima_llegada, order.order_line.date_planned)

    def test_receipt_without_backorder_is_not_pending(self):
        order = self._purchase(self.ibuprofeno, 10)
        self._receive(order, 4, backorder=False)
        self.assertLess(order.order_line.qty_received, order.order_line.product_qty)
        self.assertFalse(self._row("ai.v.pendiente", self.ibuprofeno))

    def test_pending_without_moves_and_rfqs(self):
        # Compras importadas o simuladas: confirmadas sin recepción.
        imported = self._purchase(self.ibuprofeno, 10, confirm=False)
        imported.write({"state": "purchase"})
        imported.order_line.write({"qty_received": 3,
                                   "date_planned": fields.Datetime.now() - timedelta(days=2)})
        self._purchase(self.ibuprofeno, 5, confirm=False)               # solicitud sin confirmar
        self._purchase(self.ibuprofeno, 9, confirm=False).button_cancel()
        row = self._row("ai.v.pendiente", self.ibuprofeno)
        self.assertEqual((row.pendiente, row.atrasado, row.en_solicitud), (7, 7, 5))

    # -- faltantes ------------------------------------------------------------

    def _weekly_sales(self, product, qty, weeks=8):
        for weeks_ago in range(1, weeks + 1):
            self._pos_sale(product, qty, self._week_date(weeks_ago))

    def _set_stock(self, product, qty, company=None):
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", (company or self.company).id)], limit=1)
        self.env["stock.quant"]._update_available_quantity(product, warehouse.lot_stock_id, qty)

    def test_shortage_formula(self):
        self._weekly_sales(self.ibuprofeno, 10)
        self._set_stock(self.ibuprofeno, 3)
        self._purchase(self.ibuprofeno, 2)                        # pendiente: se resta
        self._purchase(self.ibuprofeno, 4, confirm=False)         # solicitud: solo avisa
        row = self._row("ai.v.faltantes", self.ibuprofeno)
        self.assertEqual((row.demanda_semanal, row.semanas_cobertura, row.necesidad), (10, 1, 10))
        self.assertEqual((row.stock, row.pendiente, row.en_solicitud), (3, 2, 4))
        self.assertEqual((row.faltante, row.cantidad_sugerida), (5, 5))
        self.assertAlmostEqual(row.cobertura_actual, 0.5)
        self.assertFalse(row.drogueria_id)
        self.env["ir.config_parameter"].sudo().set_param("dromax_asistente_compras.coverage_weeks", "2")
        self.assertEqual(self._row("ai.v.faltantes", self.ibuprofeno).cantidad_sugerida, 15)

    def test_shortage_rounds_up_and_ignores_negative_stock(self):
        self._pos_sale(self.vitamina, 2, self._week_date(3))      # demanda depurada 0,25
        self._set_stock(self.vitamina, -4)
        row = self._row("ai.v.faltantes", self.vitamina)
        self.assertEqual((row.stock, row.faltante, row.cantidad_sugerida), (0, 0.25, 1))

    def test_no_shortage_when_covered(self):
        self._weekly_sales(self.ibuprofeno, 10)
        self._set_stock(self.ibuprofeno, 8)
        self._purchase(self.ibuprofeno, 2)
        self.assertFalse(self._row("ai.v.faltantes", self.ibuprofeno))

    def test_shortage_picks_cheapest_drugstore_with_enough_stock(self):
        self.ibuprofeno.barcode = _ean("759999900091")
        Partner = self.env["res.partner"]
        cara, media, barata = (Partner.create({"name": "ZQX DROG %s" % n, "is_company": True})
                               for n in ("CARA", "MEDIA", "BARATA"))
        for partner, price, stock in ((cara, 2.0, 100), (media, 1.5, 100), (barata, 1.0, 4)):
            self.env["supplier.product.staging"].create({
                "partner_id": partner.id, "codigo_barras": self.ibuprofeno.barcode,
                "descripcion_producto": "IBUPROFENO", "fecha_lote": "30/01/2031", "laboratorio": "LAB ZQX",
                "precio_unitario": price, "porcentaje_oferta_vigente": 0.0,
                "precio_unitario_final": price, "stock_disponible": stock,
            })
        self._weekly_sales(self.ibuprofeno, 10)
        row = self._row("ai.v.faltantes", self.ibuprofeno)
        self.assertEqual(row.cantidad_sugerida, 10)
        self.assertEqual(row.drogueria_id, media)                 # la barata solo tiene 4
        self.assertEqual((row.precio, row.mejor_precio, row.monto), (1.5, 1.0, 15))
        self.assertTrue(row.stock_drogueria_suficiente)
        self.assertEqual((row.droguerias_con_oferta, row.laboratorio), (3, "LAB ZQX"))
        # Si ninguna tiene toda la cantidad, la más barata.
        self.env["ir.config_parameter"].sudo().set_param("dromax_asistente_compras.coverage_weeks", "20")
        row = self._row("ai.v.faltantes", self.ibuprofeno)
        self.assertEqual((row.cantidad_sugerida, row.drogueria_id), (200, barata))
        self.assertFalse(row.stock_drogueria_suficiente)

    def test_shortage_flags_other_presentations(self):
        # Costo 1: la oferta de 45 es un dispensador de 50 blísteres con el mismo código.
        self.ibuprofeno.barcode = _ean("759999900092")
        self.ibuprofeno.with_company(self.company).standard_price = 1.0
        staging = self.env["supplier.product.staging"]
        blister, dispensador = (staging.create({
            "partner_id": self.env["res.partner"].create({"name": "ZQX DROG %s" % name, "is_company": True}).id,
            "codigo_barras": self.ibuprofeno.barcode, "descripcion_producto": name, "fecha_lote": "30/01/2031",
            "precio_unitario": price, "porcentaje_oferta_vigente": 0.0,
            "precio_unitario_final": price, "stock_disponible": stock,
        }) for name, price, stock in (("BLISTER", 1.2, 4), ("DISPENSADOR X 50", 45.0, 100)))
        self._weekly_sales(self.ibuprofeno, 10)
        row = self._row("ai.v.faltantes", self.ibuprofeno)
        self.assertEqual(row.drogueria_id, blister.partner_id)    # aunque no tenga toda la cantidad
        self.assertFalse(row.stock_drogueria_suficiente)
        self.assertFalse(row.revisar_presentacion)
        self.assertEqual(row.costo, 1.0)
        blister.unlink()
        row = self._row("ai.v.faltantes", self.ibuprofeno)
        self.assertEqual(row.drogueria_id, dispensador.partner_id)
        self.assertTrue(row.revisar_presentacion)

    def test_shortage_reports_excess_in_other_pharmacies(self):
        otra = self.env["res.company"].create({"name": "ZQX FARMACIA 2"})
        self._weekly_sales(self.ibuprofeno, 10)
        self._set_stock(self.ibuprofeno, 30, company=otra)         # sin ventas allí: le sobra todo
        row = self._row("ai.v.faltantes", self.ibuprofeno)
        self.assertEqual((row.cantidad_sugerida, row.excedente_otras), (10, 30))
        self.assertEqual(row.farmacias_con_excedente, "ZQX FARMACIA 2 (30)")
        self.assertFalse(self._row("ai.v.faltantes", self.ibuprofeno, company=otra))
        # Si la otra farmacia también lo vende, se queda con 4 semanas de su demanda.
        for weeks_ago in range(1, 9):
            order = self.env["sale.order"].with_company(otra).create({
                "partner_id": self.cliente.id, "company_id": otra.id,
                "order_line": [Command.create({"product_id": self.ibuprofeno.id, "product_uom_qty": 2})],
            })
            order.action_confirm()
            order.date_order = self._week_date(weeks_ago)
        self.assertEqual(self._row("ai.v.faltantes", self.ibuprofeno).excedente_otras, 22)   # 30 − 2 × 4
        self.env["ir.config_parameter"].sudo().set_param("dromax_asistente_compras.excess_weeks", "10")
        self.assertEqual(self._row("ai.v.faltantes", self.ibuprofeno).excedente_otras, 10)   # 30 − 2 × 10

    # -- farmacias y agente ---------------------------------------------------

    def test_branch_rule(self):
        otra = self.env["res.company"].create({"name": "ZQX FARMACIA 2"})
        otro_almacen = self.env["stock.warehouse"].search([("company_id", "=", otra.id)], limit=1)
        self.env["stock.quant"].with_context(inventory_mode=True).create({
            "product_id": self.ibuprofeno.id, "location_id": otro_almacen.lot_stock_id.id,
            "inventory_quantity": 8,
        }).action_apply_inventory()
        self.env.flush_all()
        comprador = new_test_user(self.env, login="ai_analisis_compras",
                                  groups="base.group_user,purchase.group_purchase_user",
                                  company_id=self.company.id, company_ids=[self.company.id])
        Stock = self.env["ai.v.stock"].with_user(comprador)
        self.assertFalse(Stock.search([("company_id", "=", otra.id)]))
        comprador.company_ids |= otra
        rows = Stock.with_context(allowed_company_ids=[self.company.id, otra.id]).search(
            [("product_id", "=", self.ibuprofeno.id)])
        self.assertEqual(rows.company_id, otra)
        self.assertEqual(rows.stock, 8)

    def test_prompt_uses_views(self):
        prompt = build_system_prompt(self.env)
        for name in ("ai_v_rotacion", "demanda_depurada", "ai_v_stock", "ai_v_pendiente", "en_solicitud",
                     "ai_v_faltantes", "cantidad_sugerida", "excedente_otras", "revisar_presentacion"):
            self.assertIn(name, prompt)
