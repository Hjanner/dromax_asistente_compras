# -*- coding: utf-8 -*-
"""Las pantallas cargan en el navegador.

El servidor no valida que los campos de una vista existan; eso lo hace el
cliente web al dibujarla. Por eso se abren de verdad: cualquier error en la
consola del navegador hace fallar el test.
"""
from odoo.tests.common import HttpCase, new_test_user, tagged

WAIT_FOR = """
const sel = %r;
let n = 0;
const t = setInterval(() => {
    if (document.querySelector(sel)) {
        clearInterval(t);
        console.log("test successful");
    } else if (++n > 150) {
        clearInterval(t);
        console.error("No apareció " + sel);
    }
}, 100);
"""


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestUi(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        new_test_user(cls.env, login="ai_purchase_admin_ui",
                      groups="base.group_user,base.group_system")
        cls.env["ai.usage.log"].create({
            "level": "advanced", "model": "anthropic/claude-opus-5.5", "state": "ok",
            "prompt_tokens": 1000, "completion_tokens": 200, "cost": 0.008,
        })

    def _open(self, fragment, selector):
        self.browser_js("/web#" + fragment, WAIT_FOR % selector, login="ai_purchase_admin_ui")

    def test_settings(self):
        self._open("action=dromax_asistente_compras.action_ai_purchase_settings",
                   "div[name='ai_purchase_model_advanced'] input")

    def test_usage_log_list(self):
        self._open("action=dromax_asistente_compras.action_ai_usage_log&view_type=list",
                   ".o_list_view .o_data_row")

    def test_usage_log_pivot(self):
        self._open("action=dromax_asistente_compras.action_ai_usage_log&view_type=pivot",
                   ".o_pivot table")

    def test_usage_log_graph(self):
        self._open("action=dromax_asistente_compras.action_ai_usage_log&view_type=graph",
                   ".o_graph_renderer canvas")
