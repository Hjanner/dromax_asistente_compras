# -*- coding: utf-8 -*-
"""Validador de SQL: lo que debe pasar y lo que no, sin tocar la base."""
from odoo.tests.common import BaseCase, tagged

from ..services.sql_validator import SqlValidationError, validate_select

ACCEPTED = [
    "SELECT 1",
    "select id, name from product_template where active limit 5;",
    "SELECT pt.name, sum(sol.product_uom_qty) FROM sale_order_line sol "
    "JOIN product_product pp ON pp.id = sol.product_id "
    "JOIN product_template pt ON pt.id = pp.product_tmpl_id GROUP BY 1 ORDER BY 2 DESC",
    "WITH v AS (SELECT product_id, sum(quantity) q FROM stock_quant GROUP BY 1), "
    "w AS MATERIALIZED (SELECT * FROM v) SELECT * FROM w",
    # FROM dentro de funciones no abre una lista de tablas
    "SELECT extract(year FROM date_order) y, count(*) FROM sale_order GROUP BY 1",
    "SELECT substring(name FROM 1 FOR 3) FROM product_template",
    "SELECT * FROM stock_quant WHERE location_id IS DISTINCT FROM 3",
    "SELECT * FROM stock_quant a, stock_location b WHERE a.location_id = b.id",
    "SELECT * FROM generate_series(1, 3) g(x)",
    "SELECT * FROM (SELECT * FROM purchase_order_line) x",
    "SELECT * FROM public.sale_order",
    "SELECT x FROM unnest(array[1, 2]) WITH ORDINALITY AS t(x, n)",
    "SELECT name FROM product_template ORDER BY name FETCH FIRST 3 ROWS WITH TIES",
    "SELECT * FROM sale_order so LEFT JOIN LATERAL "
    "(SELECT * FROM sale_order_line l WHERE l.order_id = so.id LIMIT 1) l ON true",
    # Palabras prohibidas dentro de literales, comentarios o como parte de otra palabra
    "SELECT 'drop table x; update' AS texto FROM product_template -- delete",
    'SELECT date_update_x FROM "product_template"',
]

REJECTED = [
    "", ";", "   ",
    # Más de una sentencia
    "SELECT 1; SELECT 2",
    "SELECT * FROM sale_order;; DROP TABLE x",
    "SELECT * FROM sale_order /* ; */ ; SELECT 2",
    "SELECT $$x$$; DROP TABLE y",
    # No es SELECT
    "DELETE FROM sale_order",
    "UPDATE product_template SET name = 'x'",
    "EXPLAIN SELECT 1",
    # Escritura camuflada
    "WITH d AS (DELETE FROM sale_order RETURNING *) SELECT * FROM d",
    "SELECT * INTO nueva FROM sale_order",
    "SELECT * FROM sale_order FOR UPDATE",
    "SELECT * FROM sale_order FOR SHARE",
    # Fuera de la lista blanca
    "SELECT * FROM res_users",
    "SELECT * FROM res_partner",
    'SELECT * FROM "res_users"',
    "SELECT * FROM sale_order, res_users",
    "SELECT * FROM sale_order s JOIN res_users u ON true",
    "SELECT (SELECT login FROM res_users LIMIT 1)",
    "SELECT * FROM sale_order WHERE id IN (SELECT id FROM account_move)",
    "SELECT * FROM (TABLE res_users) t",
    "SELECT * FROM otro.sale_order",
    # Catálogo y funciones de sistema
    "SELECT * FROM pg_catalog.pg_user",
    "SELECT * FROM pg_shadow",
    "SELECT * FROM information_schema.tables",
    "SELECT pg_sleep(10)",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT lo_import('/etc/passwd')",
    "SELECT query_to_xml('select * from res_users', true, true, '')",
    "SELECT set_config('statement_timeout', '0', false)",
    "SELECT current_setting('is_superuser')",
    "SELECT nextval('sale_order_id_seq')",
    "SELECT * FROM dblink('x', 'y') t(a int)",
    # Lo que no se entiende entero no se ejecuta
    "SELECT 1 /* sin cerrar",
    "SELECT 'sin cerrar",
    "SELECT (1",
    "SELECT 1)",
]


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestSqlValidator(BaseCase):

    def test_accepted(self):
        for sql in ACCEPTED:
            with self.subTest(sql=sql):
                validate_select(sql)

    def test_rejected(self):
        for sql in REJECTED:
            with self.subTest(sql=sql):
                with self.assertRaises(SqlValidationError):
                    validate_select(sql)

    def test_result(self):
        res = validate_select(
            "WITH v AS (SELECT * FROM stock_quant) "
            "SELECT * FROM v JOIN stock_location l ON l.id = v.location_id; -- fin")
        self.assertEqual(res["tables"], {"stock_quant", "stock_location"})
        self.assertEqual(res["ctes"], {"v"})
        self.assertFalse(res["sql"].endswith(";"))
        self.assertNotIn("fin", res["sql"])

    def test_custom_whitelist(self):
        validate_select("SELECT * FROM sale_order", whitelist={"sale_order"})
        with self.assertRaises(SqlValidationError):
            validate_select("SELECT * FROM product_template", whitelist={"sale_order"})

    def test_error_lists_allowed_tables(self):
        # El modelo necesita saber qué puede usar para corregir la consulta.
        with self.assertRaisesRegex(SqlValidationError, "sale_order"):
            validate_select("SELECT * FROM res_users", whitelist={"sale_order"})
