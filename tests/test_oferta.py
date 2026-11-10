# -*- coding: utf-8 -*-
"""Normalización de códigos de barras y vista ai_v_oferta."""
from datetime import date, timedelta

from odoo.tests.common import TransactionCase, tagged

from ..models.supplier_product_staging import parse_lot_date
from ..services import presets
from ..services.barcode import gtin_check_digit, is_valid_ean13, normalize_barcode
from ..services.prompt import build_system_prompt


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestBarcode(TransactionCase):

    def test_check_digit(self):
        # EAN-13 reales de los inventarios: 7591619520889, 7598055000418
        self.assertEqual(gtin_check_digit("759161952088"), "9")
        self.assertEqual(gtin_check_digit("759805500041"), "8")

    def test_normalize(self):
        cases = {
            "7591619520889": "7591619520889",       # EAN-13
            " 759-1619 520889 ": "7591619520889",   # con separadores
            "612197152006": "0612197152006",        # UPC-A
            "0612197152006": "0612197152006",
            "00612197152006": "0612197152006",      # GTIN-14 con indicador 0
            "17593255000418": "7593255000411",      # GTIN-14 de INTERCONTINENTAL
            "37594001560563": "7594001560562",
            "75970529": "0000075970529",            # EAN-8
            "00001": "1",                           # código interno corto
            "123456789012345": "123456789012345",   # demasiado largo: solo dígitos
            "": False,
            "S/C": False,
            False: False,
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_barcode(raw), expected, raw)

    def test_valid_ean13(self):
        for code in ("7591619520889", "7593255000411", "0612197152006"):
            self.assertTrue(is_valid_ean13(code), code)
        for code in ("0", "10", "0000000000000", "7591619520880", "123456789012345", False, ""):
            self.assertFalse(is_valid_ean13(code), code)                    # el penúltimo: control mal

    def test_lot_date(self):
        self.assertEqual(parse_lot_date("31/07/2029"), date(2029, 7, 31))
        self.assertEqual(parse_lot_date("2029-07-31"), date(2029, 7, 31))
        self.assertFalse(parse_lot_date("31/02/2029"))
        self.assertFalse(parse_lot_date(""))


def _ean(body):
    return body + gtin_check_digit(body)


# Códigos inventados (prefijo 759999...) para no cruzarse con la oferta real
# que pueda haber cargada en la base.
GASA = _ean("759999900001")              # EAN-13 del producto propio
GASA_14 = _ean("1" + "759999900001")     # el mismo artículo como GTIN-14
UPC = _ean("79999904436")                # UPC-A de 12 dígitos
AJENO = _ean("759999900002")


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestOfertaView(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env["res.partner"]
        cls.inter = Partner.create({"name": "DROGUERÍA A", "is_company": True})
        cls.insua = Partner.create({"name": "DROGUERÍA B", "is_company": True})
        Product = cls.env["product.product"]
        cls.gasa = Product.create({"name": "GASA 4X4 CAJA X 25", "barcode": GASA})
        cls.valproico = Product.create({"name": "ACIDO VALPROICO 250MG", "barcode": UPC})
        cls.staging = cls.env["supplier.product.staging"]
        line = lambda partner, barcode, price, stock, lote="30/01/2031": cls.staging.create({
            "partner_id": partner.id, "codigo_barras": barcode, "codigo_producto": barcode[-5:],
            "descripcion_producto": "PRODUCTO %s" % barcode, "fecha_lote": lote, "laboratorio": "LAB",
            "precio_unitario": price, "porcentaje_oferta_vigente": 0.0,
            "precio_unitario_final": price, "stock_disponible": stock,
        })
        cls.gasa_a = line(cls.inter, GASA_14, 4.17, 1824)
        cls.gasa_b = line(cls.insua, GASA, 4.20, 30)
        cls.gasa_a_agotada = line(cls.inter, GASA, 3.00, 0)   # más barata, sin stock
        cls.valproico_b = line(cls.insua, "0" + UPC, 2.57, 12, lote="")
        cls.ajeno = line(cls.inter, AJENO, 18.40, 16)  # sin producto propio
        cls.env.flush_all()

    def _row(self, staging_line):
        return self.env["ai.v.oferta"].browse(staging_line.id)

    def test_product_match(self):
        self.assertEqual(self.gasa.ai_barcode_norm, GASA)
        self.assertEqual(self.valproico.ai_barcode_norm, "0" + UPC)
        self.assertEqual(self._row(self.gasa_a).product_id, self.gasa)
        self.assertEqual(self._row(self.gasa_b).product_id, self.gasa)
        self.assertEqual(self._row(self.valproico_b).product_id, self.valproico)
        self.assertEqual(self._row(self.valproico_b).producto, "ACIDO VALPROICO 250MG")
        self.assertFalse(self._row(self.ajeno).product_id)
        self.assertEqual(self._row(self.gasa_a).codigo_barras_drogueria, GASA_14)
        self.assertEqual(self._row(self.gasa_a).drogueria, "DROGUERÍA A")

    def test_price_comparison(self):
        best, second, agotada = self._row(self.gasa_a), self._row(self.gasa_b), self._row(self.gasa_a_agotada)
        self.assertEqual((best.orden_precio, second.orden_precio), (1, 2))
        self.assertFalse(agotada.disponible)
        self.assertFalse(agotada.orden_precio)
        for row in (best, second, agotada):
            self.assertAlmostEqual(row.mejor_precio, 4.17)
            self.assertAlmostEqual(row.segundo_precio, 4.20)
            self.assertEqual(row.droguerias_con_stock, 2)
        self.assertEqual(self._row(self.ajeno).droguerias_con_stock, 1)
        self.assertFalse(self._row(self.ajeno).segundo_precio)

    def test_comparison_is_between_droguerias(self):
        """Un segundo lote de la misma droguería no es el «segundo precio»."""
        lote2 = self.staging.create({
            "partner_id": self.inter.id, "codigo_barras": GASA, "descripcion_producto": "GASA LOTE 2",
            "precio_unitario_final": 4.18, "stock_disponible": 7,
        })
        self.env.flush_all()
        self.assertEqual(self._row(self.gasa_a).orden_precio, 1)
        self.assertFalse(self._row(lote2).orden_precio)
        self.assertEqual(self._row(self.gasa_b).orden_precio, 2)
        self.assertAlmostEqual(self._row(lote2).segundo_precio, 4.20)
        self.assertEqual(self._row(lote2).droguerias_con_stock, 2)

    def test_tie_keeps_two_places(self):
        self.gasa_b.precio_unitario_final = 4.17
        self.env.flush_all()
        places = sorted([self._row(self.gasa_a).orden_precio, self._row(self.gasa_b).orden_precio])
        self.assertEqual(places, [1, 2])
        self.assertAlmostEqual(self._row(self.gasa_a).segundo_precio, 4.17)

    def test_dates(self):
        self.assertEqual(self._row(self.gasa_a).fecha_vencimiento, date(2031, 1, 30))
        self.assertFalse(self._row(self.valproico_b).fecha_vencimiento)

    def test_duplicate_own_products_do_not_duplicate_rows(self):
        # Mismo código escrito de dos formas: Odoo no lo ve repetido, el cruce sí.
        self.env["product.product"].create({"name": "ACIDO VALPROICO (DUPLICADO)", "barcode": "0" + UPC})
        self.env.flush_all()
        rows = self.env["ai.v.oferta"].search([("codigo_barras", "=", "0" + UPC)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.product_id, self.valproico)

    def test_barcode_change_recomputes(self):
        self.ajeno.codigo_barras = GASA_14
        self.valproico.barcode = AJENO
        self.env.flush_all()
        self.assertEqual(self._row(self.ajeno).product_id, self.gasa)
        self.assertEqual(self.valproico.ai_barcode_norm, AJENO)

    def test_prompt_describes_offer(self):
        prompt = build_system_prompt(self.env)
        self.assertIn("ai_v_oferta", prompt)
        self.assertIn("precio_final", prompt)
        self.assertIn("ai_barcode_norm", prompt)

    # -- reglas de comparación y comparador -------------------------------------

    def _comparador(self, code):
        self.env.flush_all()
        self.env.invalidate_all()
        return self.env["ai.v.comparador"].search([("codigo_barras", "=", code)])

    def test_invalid_codes_and_expired_lots_are_not_compared(self):
        """Códigos como «0» juntarían productos distintos; un lote vencido no se puede comprar."""
        propio = self.env["product.product"].create({"name": "PRODUCTO CON CODIGO 0", "barcode": "0"})
        uno, otro = (self.staging.create({
            "partner_id": partner.id, "codigo_barras": "0", "descripcion_producto": name,
            "precio_unitario_final": price, "stock_disponible": 5,
        }) for partner, name, price in ((self.inter, "CLINDAMICINA AMP", 4.0), (self.insua, "FUROSEMIDA AMP", 0.7)))
        vencida = self.staging.create({
            "partner_id": self.insua.id, "codigo_barras": GASA, "descripcion_producto": "GASA VENCIDA",
            "fecha_lote": (date.today() - timedelta(days=1)).strftime("%d/%m/%Y"),
            "precio_unitario_final": 1.0, "stock_disponible": 50,
        })
        self.env.flush_all()
        for line in (uno, otro):
            row = self._row(line)
            self.assertFalse(row.codigo_valido)
            self.assertFalse(row.disponible)
            self.assertFalse(row.orden_precio)
            self.assertFalse(row.product_id)                                    # no se cruza con «0» propio
            self.assertFalse(row.comparador_id)
        self.assertEqual(propio.ai_barcode_norm, "0")                          # existe, pero no se cruza
        row = self._row(vencida)
        self.assertTrue(row.vencido)
        self.assertFalse(row.disponible)
        self.assertAlmostEqual(self._row(self.gasa_a).mejor_precio, 4.17)      # la vencida no gana

    def test_comparador(self):
        gasa = self._comparador(GASA)
        self.assertEqual(len(gasa), 1)
        self.assertEqual((gasa.nombre, gasa.product_id, gasa.en_catalogo), ("GASA 4X4 CAJA X 25", self.gasa, True))
        self.assertEqual((gasa.drogueria_id, gasa.segunda_drogueria_id), (self.inter, self.insua))
        self.assertAlmostEqual(gasa.mejor_precio, 4.17)
        self.assertAlmostEqual(gasa.segundo_precio, 4.20)
        self.assertAlmostEqual(gasa.ahorro, 0.03)
        self.assertAlmostEqual(gasa.ahorro_pct, 0.7)
        self.assertEqual((gasa.droguerias_con_stock, gasa.droguerias_total, gasa.stock_total), (2, 2, 1854))
        self.assertEqual(gasa.oferta_ids, self.env["ai.v.oferta"].browse(
            (self.gasa_a | self.gasa_b | self.gasa_a_agotada).ids))
        self.assertFalse(gasa.precios_dispares or gasa.vence_pronto or gasa.stock_bajo)
        # Sin producto propio: el nombre de la droguería, sin segunda.
        ajeno = self._comparador(AJENO)
        self.assertEqual((ajeno.nombre, ajeno.en_catalogo, ajeno.segundo_precio), ("PRODUCTO %s" % AJENO, False, 0))
        # Alertas: otra presentación, lote por vencer y poco stock en la más barata.
        self.staging.create({"partner_id": self.env["res.partner"].create({"name": "DROGUERÍA C"}).id,
                             "codigo_barras": GASA, "descripcion_producto": "GASA CAJA X 10 CAJAS",
                             "precio_unitario_final": 13.0, "stock_disponible": 4})
        self.gasa_a.write({"stock_disponible": 2,
                           "fecha_lote": (date.today() + timedelta(days=30)).strftime("%d/%m/%Y")})
        gasa = self._comparador(GASA)
        self.assertTrue(gasa.precios_dispares and gasa.vence_pronto and gasa.stock_bajo)
        self.assertEqual(gasa.droguerias_con_stock, 3)

    def test_agent_uses_the_comparator(self):
        self.assertIn("ai_v_comparador", build_system_prompt(self.env))
        self.assertIn("ai_v_comparador", presets.get_preset("comparar_droguerias")["prompt"])
