# -*- coding: utf-8 -*-
"""Las pantallas cargan en el navegador.

El servidor no valida que los campos de una vista existan; eso lo hace el
cliente web al dibujarla. Por eso se abren de verdad: cualquier error en la
consola del navegador hace fallar el test.
"""
from datetime import timedelta

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

    def _open(self, fragment, selector, login="ai_purchase_admin_ui"):
        self.browser_js("/web#" + fragment, WAIT_FOR % selector, login=login)

    def test_settings(self):
        self._open("action=dromax_asistente_compras.action_ai_purchase_settings",
                   "div[name='ai_purchase_model_advanced'] input")

    def test_offer_list(self):
        buyer = new_test_user(self.env, login="ai_purchase_buyer_ui",
                              groups="base.group_user,dromax_asistente_compras.group_ai_purchase_user")
        buyer.password = buyer.login
        drogueria = self.env["res.partner"].create({"name": "DROGUERÍA UI", "is_company": True})
        self.env["supplier.product.staging"].create({
            "partner_id": drogueria.id, "codigo_barras": "17593255000418",
            "descripcion_producto": "GASA UI", "fecha_lote": "30/01/2031",
            "precio_unitario_final": 4.17, "stock_disponible": 10,
        })
        self._open("action=dromax_asistente_compras.action_ai_v_oferta",
                   ".o_list_view .o_data_row", login=buyer.login)

    def test_analysis_lists(self):
        buyer = new_test_user(self.env, login="ai_analisis_buyer_ui",
                              groups="base.group_user,dromax_asistente_compras.group_ai_purchase_user")
        buyer.password = buyer.login
        product = self.env["product.product"].create({"name": "ZQX UI ANALISIS", "detailed_type": "product"})
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1)
        self.env["stock.quant"].with_context(inventory_mode=True).create({
            "product_id": product.id, "location_id": warehouse.lot_stock_id.id, "inventory_quantity": 5,
        }).action_apply_inventory()
        drogueria = self.env["res.partner"].create({"name": "DROGUERÍA UI", "is_company": True})
        self.env["purchase.order"].create({
            "partner_id": drogueria.id,
            "order_line": [(0, 0, {"product_id": product.id, "product_qty": 3, "price_unit": 1})],
        }).button_confirm()
        session = self.env["pos.session"].create({
            "config_id": self.env["pos.config"].create({"name": "ZQX TPV UI"}).id, "user_id": self.env.uid})
        order = self.env["pos.order"].create({
            "session_id": session.id, "amount_tax": 0, "amount_total": 2, "amount_paid": 2, "amount_return": 0,
            "lines": [(0, 0, {"product_id": product.id, "qty": 2, "price_unit": 1,
                              "price_subtotal": 2, "price_subtotal_incl": 2})],
        })
        order.write({"state": "paid", "date_order": order.date_order - timedelta(weeks=1)})
        for action, column in (("action_ai_v_rotacion", "demanda_depurada"),
                               ("action_ai_v_stock", "disponible"),
                               ("action_ai_v_pendiente", "proxima_llegada")):
            self._open("action=dromax_asistente_compras." + action,
                       ".o_list_view .o_data_row td[name='%s']" % column, login=buyer.login)

    def test_barcode_report(self):
        buyer = new_test_user(self.env, login="ai_bm_buyer_ui",
                              groups="base.group_user,dromax_asistente_compras.group_ai_purchase_user")
        buyer.password = buyer.login
        self.env["ai.barcode.mismatch"].search([]).unlink()
        self.env["product.product"].create({"name": "ZQX UI SIN CODIGO", "detailed_type": "product"})
        self.browser_js("/web#action=dromax_asistente_compras.action_ai_barcode_mismatch", """
            const until = (sel) => new Promise((resolve, reject) => {
                let n = 0;
                const t = setInterval(() => {
                    const el = document.querySelector(sel);
                    if (el) { clearInterval(t); resolve(el); }
                    else if (++n > 600) { clearInterval(t); reject(new Error("No apareció " + sel)); }
                }, 100);
            });
            (async () => {
                try {
                    (await until("button[name='action_generate']")).click();
                    await until(".o_list_view .o_group_header");
                    console.log("test successful");
                } catch (e) {
                    console.error(e.message);
                }
            })();
        """, login=buyer.login)

    def test_usage_log_list(self):
        self._open("action=dromax_asistente_compras.action_ai_usage_log&view_type=list",
                   ".o_list_view .o_data_row")

    def test_usage_log_pivot(self):
        self._open("action=dromax_asistente_compras.action_ai_usage_log&view_type=pivot",
                   ".o_pivot table")

    def test_usage_log_graph(self):
        self._open("action=dromax_asistente_compras.action_ai_usage_log&view_type=graph",
                   ".o_graph_renderer canvas")


CHAT_FLOW = """
const until = (sel, text) => new Promise((resolve, reject) => {
    let n = 0;
    const t = setInterval(() => {
        const el = [...document.querySelectorAll(sel)].find(e => !text || e.textContent.includes(text));
        if (el) { clearInterval(t); resolve(el); }
        else if (++n > 150) { clearInterval(t); reject(new Error("No apareció " + sel + " " + (text || ""))); }
    }, 100);
});
(async () => {
    try {
        await until(".o_ai_preset[data-preset='compra_semana']");
        const input = await until(".o_ai_input");
        input.value = "¿Cuántos productos hay?";
        input.dispatchEvent(new Event("input", { bubbles: true }));
        await until(".o_ai_send:not([disabled])");
        document.querySelector(".o_ai_send").click();
        await until(".o_ai_msg_assistant td", "Kilometraje");
        await until(".o_ai_session", "¿Cuántos productos hay?");
        document.querySelector(".o_ai_trace_toggle").click();
        await until(".o_ai_trace pre", "SELECT");
        console.log("test successful");
    } catch (e) {
        console.error(e.message);
    }
})();
"""


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestChatUi(HttpCase):
    """Conversación completa en el navegador, con OpenRouter y la herramienta
    simulados en el servidor."""

    def test_chat_flow(self):
        from unittest.mock import patch

        from ..services import agent as agent_mod
        from ..services import openrouter
        from .test_chat import ScriptedTransport, text_response, tool_call_response

        new_test_user(self.env, login="ai_chat_ui_buyer",
                      groups="base.group_user,dromax_asistente_compras.group_ai_purchase_user")
        self.env["ir.config_parameter"].sudo().set_param(
            "dromax_asistente_compras.openrouter_api_key", "sk-or-test")
        transport = ScriptedTransport(
            tool_call_response("SELECT name FROM product_template"),
            text_response("| Producto | Unidades |\n|---|---|\n| Kilometraje | 3 |"))
        fake_tool = lambda env, name, args: {"columns": ["name"], "rows": [["Kilometraje"]],
                                             "row_count": 1, "truncated": False, "elapsed_ms": 2}
        with patch.object(openrouter, "Transport", lambda: transport), \
                patch.object(agent_mod, "call_tool", fake_tool):
            self.browser_js("/web#action=dromax_asistente_compras.action_ai_purchase_chat",
                            CHAT_FLOW, login="ai_chat_ui_buyer")
