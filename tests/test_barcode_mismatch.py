# -*- coding: utf-8 -*-
"""Reporte de incoherencias de código de barras (ai.barcode.mismatch).

Productos, droguerías y códigos inventados (prefijos ZQX y 759999...) para no
cruzarse con la oferta real que pueda haber cargada en la base: el reporte es
global, así que cada prueba mira solo sus filas.
"""
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from ..models.ai_barcode_mismatch import norm_sql
from ..services.barcode import gtin_check_digit

GROUPS = "base.group_user,dromax_asistente_compras.group_ai_purchase_user"


def _ean(body):
    return body + gtin_check_digit(body)


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestNormalization(TransactionCase):

    def _norm(self, text):
        self.env.cr.execute("SELECT " + norm_sql("t") + " FROM (SELECT %s::text AS t) x", (text,))
        return self.env.cr.fetchone()[0]

    def test_norm(self):
        cases = {
            "Ibuprofeno 800 mg x10 Tabletas (caja)": "IBUPROFENO 800MG X 10 TAB CAJA",
            "IBUPROFENO 800MG X 10TAB CAJA": "IBUPROFENO 800MG X 10 TAB CAJA",
            "Ácido fólico 0,5 MG X 30 Comprimidos": "ACIDO FOLICO 0.5MG X 30 COMP",
            "ACIDO FOLICO 10MGX30TAB": "ACIDO FOLICO 10MG X 30 TAB",
            "Alcohol 70 % 120 ml": "ALCOHOL 70% 120ML",
            "SAL DE HIGUERA 20 GR X 6 SOBRES": "SAL DE HIGUERA 20G X 6 SOBRES",
            "GORRO X 50 UNIDADES": "GORRO X 50 UND",
            "SOL.OFT. X 5ML": "SOL OFT X 5ML",
        }
        for raw, expected in cases.items():
            self.assertEqual(self._norm(raw), expected, raw)


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestBarcodeMismatch(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Partner = cls.env["res.partner"]
        cls.drog_a = Partner.create({"name": "ZQX DROGUERÍA A", "is_company": True})
        cls.drog_b = Partner.create({"name": "ZQX DROGUERÍA B", "is_company": True})
        Product = cls.env["product.product"]
        product = lambda name, barcode=False, **kw: Product.create(
            dict({"name": name, "barcode": barcode, "detailed_type": "product"}, **kw))

        cls.cod_pril = _ean("759999910001")         # código propio de ZQXPRIL
        cls.cod_pril_drog = _ean("759999910002")    # el que usa la droguería
        cls.pril = product("ZQXPRIL 10 MG X 30 TABLETAS LABZQ", cls.cod_pril)
        cls.caina = product("ZQXCAINA 500 MG X 20 CAPSULAS LABZQ")             # sin código
        cls.sin_oferta = product("ZQXSOLO 5 MG X 10 TAB", _ean("759999910010"))
        cls.dup_upc = _ean("79999991002")                                          # UPC-A
        cls.dup1 = product("ZQXDUP UNO", cls.dup_upc)
        cls.dup2 = product("ZQXDUP DOS", "0" + cls.dup_upc)                        # mismo código normalizado
        cls.ofrecido = product("ZQXOFRECIDO 1 MG", _ean("759999910020"))
        cls.servicio = product("ZQXSERVICIO", detailed_type="service")
        cls.no_comprable = product("ZQXNOCOMPRA 1 MG", purchase_ok=False)

        line = lambda partner, barcode, desc, price=10.0, stock=5: cls.env["supplier.product.staging"].create({
            "partner_id": partner.id, "codigo_barras": barcode, "descripcion_producto": desc,
            "precio_unitario_final": price, "stock_disponible": stock,
        })
        line(cls.drog_a, cls.cod_pril_drog, "ZQXPRIL 10MG X 30 TAB LABZQ")
        # Señuelos con otro código: dosis, empaque o laboratorio distintos.
        cls.cod_dosis = _ean("759999910003")
        cls.cod_pack = _ean("759999910004")
        cls.cod_lab = _ean("759999910005")
        line(cls.drog_b, cls.cod_dosis, "ZQXPRIL 20MG X 30 TAB LABZQ")
        line(cls.drog_b, cls.cod_pack, "ZQXPRIL 10MG X 10 TAB LABZQ")
        line(cls.drog_b, cls.cod_lab, "ZQXPRIL 10MG X 30 TAB OTROLAB")
        cls.cod_caina = _ean("759999910006")
        line(cls.drog_b, cls.cod_caina, "ZQXCAINA 500MG X 20 CAP LABZQ")
        line(cls.drog_a, cls.ofrecido.barcode, "ZQXOFRECIDO 1MG")
        # Mismo código, precio 50 veces mayor en B: otra presentación.
        cls.cod_present = _ean("759999910007")
        line(cls.drog_a, cls.cod_present, "ZQXGASA X 1 UND", price=1.0)
        line(cls.drog_b, cls.cod_present, "ZQXGASA X 50 UND", price=50.0)
        cls.cod_normal = _ean("759999910008")
        line(cls.drog_a, cls.cod_normal, "ZQXNORMAL", price=1.0)
        line(cls.drog_b, cls.cod_normal, "ZQXNORMAL", price=2.0)

        cls.Report = cls.env["ai.barcode.mismatch"]
        cls.total = cls.Report._generate()

    def _rows(self, **domain):
        return self.Report.search([(k, "=", v) for k, v in domain.items()])

    def test_similar_name_other_code(self):
        rows = self._rows(tipo="codigo_distinto", product_id=self.pril.id)
        self.assertEqual(rows.mapped("codigo_barras"), [self.cod_pril_drog],
                         "no deben salir los señuelos de otra dosis, empaque o laboratorio")
        self.assertEqual(rows.partner_id, self.drog_a)
        self.assertEqual(rows.codigo_propio, self.cod_pril)
        self.assertGreaterEqual(rows.similitud, 0.6)
        self.assertIn(self.cod_pril_drog, rows.detalle)

    def test_product_without_barcode_gets_suggestion(self):
        rows = self._rows(tipo="codigo_distinto", product_id=self.caina.id)
        self.assertEqual(rows.codigo_barras, self.cod_caina)
        self.assertIn("Sin código propio", rows.detalle)
        self.assertTrue(self._rows(tipo="sin_codigo", product_id=self.caina.id))

    def test_without_offer(self):
        self.assertTrue(self._rows(tipo="sin_oferta", product_id=self.sin_oferta.id))
        self.assertFalse(self._rows(tipo="sin_oferta", product_id=self.ofrecido.id))

    def test_duplicates(self):
        rows = self.Report.search([("tipo", "=", "duplicado"), ("product_id", "in", (self.dup1 | self.dup2).ids)])
        self.assertEqual(len(rows), 2)
        self.assertIn("ZQXDUP DOS", rows.filtered(lambda r: r.product_id == self.dup1).detalle)

    def test_codes_without_own_product(self):
        rows = self._rows(tipo="sin_producto", codigo_barras=self.cod_present)
        self.assertEqual(len(rows), 1, "una fila por código")
        self.assertEqual(rows.droguerias, "ZQX DROGUERÍA A, ZQX DROGUERÍA B")
        self.assertEqual(rows.precio_min, 1.0)
        self.assertTrue(self._rows(tipo="sin_producto", codigo_barras=self.cod_dosis))
        self.assertFalse(self._rows(tipo="sin_producto", codigo_barras=self.ofrecido.barcode))

    def test_presentation(self):
        row = self._rows(tipo="presentacion", codigo_barras=self.cod_present)
        self.assertEqual((row.precio_min, row.precio_max), (1.0, 50.0))
        self.assertEqual(row.partner_id, self.drog_b)
        self.assertIn("ZQXGASA X 50 UND", row.detalle)
        self.assertFalse(self._rows(tipo="presentacion", codigo_barras=self.cod_normal))

    def test_scope(self):
        ignored = self.servicio | self.no_comprable
        self.assertFalse(self.Report.search([("product_id", "in", ignored.ids)]))

    def test_regenerate_replaces(self):
        self.assertEqual(self.Report._generate(), self.total)
        self.assertEqual(self.Report.search_count([]), self.total)

    def test_access(self):
        outsider = new_test_user(self.env, login="ai_bm_outsider", groups="base.group_user")
        with self.assertRaises(AccessError):
            self.Report.with_user(outsider).action_generate()
        buyer = new_test_user(self.env, login="ai_bm_buyer", groups=GROUPS)
        result = self.Report.with_user(buyer).action_generate()
        self.assertEqual(result["params"]["type"], "success")
        row = self.Report.with_user(buyer).search([("product_id", "=", self.pril.id)], limit=1)
        self.assertEqual(row.create_uid, buyer)
