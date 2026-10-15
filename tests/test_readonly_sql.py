# -*- coding: utf-8 -*-
"""Ejecución con el rol de solo lectura.

Necesitan el rol creado (scripts/create_readonly_role.sql) y configurado en
dromax_asistente_compras.ro_user / ro_password; si no, se saltan. La conexión
es independiente de la transacción del test: solo ve datos ya confirmados.
"""
from unittest import SkipTest

from odoo.tests.common import TransactionCase, new_test_user, tagged

from ..services import readonly_sql
from ..services.config import get_setting
from ..services.readonly_sql import ReadonlySqlError, run_readonly_sql
from ..services.sql_validator import DEFAULT_WHITELIST, SqlValidationError
from ..services.tools import call_tool, tool_definitions

P = "dromax_asistente_compras."


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestReadonlySql(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not get_setting(cls.env, "ro_user"):
            raise SkipTest("Rol de solo lectura sin configurar (%sro_user)." % P)
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        cls.buyer = new_test_user(
            cls.env, login="ai_purchase_buyer",
            groups="base.group_user,dromax_asistente_compras.group_ai_purchase_user")
        cls.outsider = new_test_user(cls.env, login="ai_purchase_outsider", groups="base.group_user")

    def test_select(self):
        res = run_readonly_sql(self.env, "SELECT count(*) AS n FROM product_template")
        self.assertEqual(res["columns"], ["n"])
        self.assertEqual(res["row_count"], 1)
        self.assertIsInstance(res["rows"][0][0], int)
        self.assertFalse(res["truncated"])

    def test_truncated(self):
        res = run_readonly_sql(self.env, "SELECT g FROM generate_series(1, 50) g", max_rows=10)
        self.assertEqual(res["row_count"], 10)
        self.assertTrue(res["truncated"])
        self.ICP.set_param(P + "sql_max_rows", "5")
        res = run_readonly_sql(self.env, "SELECT g FROM generate_series(1, 50) g", max_rows=10)
        self.assertEqual(res["row_count"], 5, "el parámetro manda sobre lo que pida el modelo")

    def test_json_values(self):
        res = run_readonly_sql(
            self.env, "SELECT 1.5::numeric AS d, now()::date AS f, repeat('x', 900) AS t")
        d, f, t = res["rows"][0]
        self.assertEqual(d, 1.5)
        self.assertIsInstance(f, str)
        self.assertLess(len(t), 900)

    def test_role_is_second_barrier(self):
        # Aunque alguien amplíe la lista blanca del módulo, el rol no tiene
        # permiso sobre res_users: Postgres lo rechaza.
        self.ICP.set_param(P + "sql_whitelist", ",".join(DEFAULT_WHITELIST | {"res_users"}))
        with self.assertRaisesRegex(ReadonlySqlError, "permiso"):
            run_readonly_sql(self.env, "SELECT login FROM res_users")

    def test_session_is_read_only(self):
        # Saltándose el validador, la conexión sigue sin poder escribir.
        conn = readonly_sql._connect(self.env)
        try:
            with conn.cursor() as cr:
                with self.assertRaises(Exception) as ctx:
                    cr.execute("CREATE TEMP TABLE ai_probe (x int)")
                self.assertIn("read-only", str(ctx.exception))
        finally:
            conn.rollback()
            conn.close()

    def test_timeout(self):
        self.ICP.set_param(P + "sql_timeout_seconds", "1")
        with self.assertRaisesRegex(ReadonlySqlError, "tiempo"):
            run_readonly_sql(self.env, "SELECT count(*) FROM generate_series(1, 2000000000)")

    def test_cte_cannot_shadow_real_table(self):
        with self.assertRaisesRegex(SqlValidationError, "res_users"):
            run_readonly_sql(
                self.env,
                "SELECT * FROM (WITH res_users AS (SELECT 1) SELECT 1) a, sale_order")

    def test_refuses_odoo_db_user(self):
        from odoo.tools import config
        self.ICP.set_param(P + "ro_user", config.get("db_user"))
        with self.assertRaisesRegex(ReadonlySqlError, "mismo"):
            run_readonly_sql(self.env, "SELECT 1")

    def test_call_tool(self):
        ok = call_tool(self.env(user=self.buyer), "run_readonly_sql", {"query": "SELECT 1 AS uno"})
        self.assertEqual(ok["rows"], [[1]])
        rejected = call_tool(self.env(user=self.buyer), "run_readonly_sql", {"query": "DELETE FROM sale_order"})
        self.assertIn("rechazada", rejected["error"])
        denied = call_tool(self.env(user=self.outsider), "run_readonly_sql", {"query": "SELECT 1"})
        self.assertIn("acceso", denied["error"])
        unknown = call_tool(self.env(user=self.buyer), "create_rfq", {})
        self.assertIn("desconocida", unknown["error"])

    def test_tool_definitions(self):
        spec = tool_definitions(self.env)[0]["function"]
        self.assertEqual(spec["name"], "run_readonly_sql")
        self.assertIn("stock_quant", spec["description"])
