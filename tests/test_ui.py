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
        # Las pantallas se prueban sin punto de pedido ni margen (tienen sus tests de cálculo).
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param("dromax_asistente_compras.reorder_point_pct", "100")
        ICP.set_param("dromax_asistente_compras.safety_margin_pct", "0")
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
                              groups="base.group_user,base.group_system")
        buyer.password = buyer.login
        drogueria = self.env["res.partner"].create({"name": "DROGUERÍA UI", "is_company": True})
        self.env["supplier.product.staging"].create({
            "partner_id": drogueria.id, "codigo_barras": "17593255000418",
            "descripcion_producto": "GASA UI", "fecha_lote": "30/01/2031",
            "precio_unitario_final": 4.17, "stock_disponible": 10,
        })
        self._open("action=dromax_asistente_compras.action_ai_v_oferta",
                   ".o_list_view .o_data_row", login=buyer.login)

    def test_comparator(self):
        """Comparador: la lista, el clic que abre la ventana con cada droguería, la ficha completa y
        la selección de productos que abre el asistente para añadirlos a un borrador de compra y el
        botón del aviso que lleva a esa orden en Compras."""
        matriz = self.env["res.company"].create({"name": "ZQX MATRIZ UI COMPARADOR"})
        self.env["ir.config_parameter"].sudo().set_param(
            "dromax_asistente_compras.matrix_company_id", str(matriz.id))
        buyer = new_test_user(self.env, login="ai_comparador_ui",
                              groups="base.group_user,purchase.group_purchase_user,"
                                     "base.group_system",
                              company_id=matriz.id, company_ids=(matriz | self.env.company).ids)
        buyer.password = buyer.login
        Partner = self.env["res.partner"]
        code = "7599999000045"                                   # EAN-13 válido inventado
        self.env["product.product"].create({"name": "00 ZQX UI COMPARADOR PROPIO", "barcode": code,
                                            "detailed_type": "product"})
        lines = self.env["supplier.product.staging"].create([{
            "partner_id": Partner.create({"name": name, "is_company": True}).id, "codigo_barras": code,
            "descripcion_producto": "ZQX UI COMPARADOR %s" % name, "fecha_lote": "30/01/2031",
            "precio_unitario": price, "precio_unitario_final": price, "stock_disponible": 10,
        } for name, price in (("DROG UI A", 2.5), ("DROG UI B", 2.8))])
        self.env.flush_all()
        comparador = self.env["ai.v.comparador"].search([("codigo_barras", "=", code)])
        self.assertEqual(comparador.id, min(lines.ids))
        self.browser_js("/web#action=dromax_asistente_compras.action_ai_v_comparador", """
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
                    (await until(".o_list_view .o_data_row td[name='nombre']", "00 ZQX UI COMPARADOR PROPIO")).click();
                    await until(".modal .o_field_one2many .o_data_row td[name='partner_id']", "DROG UI A");
                    await until(".modal .o_field_one2many .o_data_row td[name='partner_id']", "DROG UI B");
                    (await until(".modal button[special='cancel']")).click();
                    // Al cerrar, la lista se vuelve a dibujar: esperar y buscar la fila de nuevo.
                    await new Promise((resolve) => {
                        const t = setInterval(() => { if (!document.querySelector(".modal")) { clearInterval(t); resolve(); } }, 100);
                    });
                    await new Promise((resolve) => setTimeout(resolve, 500));
                    const row = await until(".o_list_view .o_data_row", "00 ZQX UI COMPARADOR PROPIO");
                    row.querySelector(".o_list_record_selector input").click();
                    (await until("button[name='action_add_to_rfq']")).click();
                    await until(".modal .o_field_one2many .o_data_row td[name='cantidad']");
                    (await until(".modal button[name='action_confirm']")).click();
                    // El aviso trae el botón para ir a la orden en Compras, en la misma pestaña.
                    (await until(".o_notification .o_notification_buttons button", "Ver en Compras")).click();
                    await until(".o_form_view .o_field_one2many .o_data_row td[name='product_id']",
                                "00 ZQX UI COMPARADOR PROPIO");
                    await until(".o_breadcrumb", "Oferta de proveedores");
                    console.log("test successful");
                } catch (e) {
                    console.error(e.message);
                }
            })();
        """, login=buyer.login)
        self._open("action=dromax_asistente_compras.action_ai_v_comparador&id=%d&view_type=form" % comparador.id,
                   ".o_form_view .o_field_one2many .o_data_row td[name='precio_final']", login=buyer.login)

    def test_analysis_lists(self):
        matriz = self.env["res.company"].create({"name": "ZQX MATRIZ UI LISTAS"})
        self.env["ir.config_parameter"].sudo().set_param(
            "dromax_asistente_compras.matrix_company_id", str(matriz.id))
        buyer = new_test_user(self.env, login="ai_analisis_buyer_ui",
                              groups="base.group_user,base.group_system",
                              company_id=self.env.company.id, company_ids=(self.env.company | matriz).ids)
        buyer.password = buyer.login
        product = self.env["product.product"].create({"name": "ZQX UI ANALISIS", "detailed_type": "product"})
        agotado = self.env["product.product"].create({"name": "ZQX UI AGOTADO", "detailed_type": "product"})
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
            "session_id": session.id, "amount_tax": 0, "amount_total": 4, "amount_paid": 4, "amount_return": 0,
            "lines": [(0, 0, {"product_id": p.id, "qty": 2, "price_unit": 1,
                              "price_subtotal": 2, "price_subtotal_incl": 2}) for p in (product, agotado)],
        })
        order.write({"state": "paid", "date_order": order.date_order - timedelta(weeks=1)})
        # La necesidad de compra es de la matriz: se ve con ella marcada en el selector de empresas.
        cids = "&cids=%d-%d" % (self.env.company.id, matriz.id)
        for action, column in (("action_ai_v_faltantes", "cantidad_sugerida"),
                               ("action_ai_v_rotacion", "demanda_depurada"),
                               ("action_ai_v_stock", "disponible"),
                               ("action_ai_v_pendiente", "proxima_llegada")):
            self._open("action=dromax_asistente_compras." + action + cids,
                       ".o_list_view .o_data_row td[name='%s']" % column, login=buyer.login)

    def test_replenishment_screens(self):
        """Reabastecimiento desde la matriz: lista (con el botón al elegir líneas) y tabla dinámica."""
        matriz = self.env["res.company"].create({"name": "ZQX MATRIZ UI"})
        self.env["ir.config_parameter"].sudo().set_param(
            "dromax_asistente_compras.matrix_company_id", str(matriz.id))
        companies = self.env.company | matriz
        user = new_test_user(
            self.env, login="ai_reabastecimiento_ui",
            groups="base.group_user,stock.group_stock_user,dromax_asistente_compras.group_ai_purchase_user",
            company_id=self.env.company.id, company_ids=companies.ids)
        user.password = user.login
        product = self.env["product.product"].create({"name": "ZQX UI REABASTECER", "detailed_type": "product"})
        product.with_company(matriz).standard_price = 3.0
        wh_matriz = self.env["stock.warehouse"].search([("company_id", "=", matriz.id)], limit=1)
        self.env["stock.quant"]._update_available_quantity(product, wh_matriz.lot_stock_id, 20)
        session = self.env["pos.session"].create({
            "config_id": self.env["pos.config"].create({"name": "ZQX TPV UI REAB"}).id, "user_id": self.env.uid})
        order = self.env["pos.order"].create({
            "session_id": session.id, "amount_tax": 0, "amount_total": 16, "amount_paid": 16, "amount_return": 0,
            "lines": [(0, 0, {"product_id": product.id, "qty": 16, "price_unit": 1,
                              "price_subtotal": 16, "price_subtotal_incl": 16})],
        })
        order.write({"state": "paid", "date_order": order.date_order - timedelta(weeks=1)})
        action = "action=dromax_asistente_compras.action_ai_v_reabastecimiento"
        self.browser_js("/web#" + action, """
            const until = (sel) => new Promise((resolve, reject) => {
                let n = 0;
                const t = setInterval(() => {
                    const el = document.querySelector(sel);
                    if (el) { clearInterval(t); resolve(el); }
                    else if (++n > 150) { clearInterval(t); reject(new Error("No apareció " + sel)); }
                }, 100);
            });
            (async () => {
                try {
                    await until(".o_list_view .o_data_row td[name='valor']");
                    (await until(".o_list_view thead .o_list_record_selector input")).click();
                    await until("button[name='action_generate_transfers']");
                    console.log("test successful");
                } catch (e) {
                    console.error(e.message);
                }
            })();
        """, login=user.login)
        self._open(action + "&view_type=pivot", ".o_pivot table", login=user.login)
        # Sin vista de gráfico (pedido del usuario): solo lista y tabla dinámica.
        act = self.env.ref("dromax_asistente_compras.action_ai_v_reabastecimiento")
        self.assertEqual(act.view_mode, "tree,pivot")

    def test_cancel_several_transfers(self):
        """Acciones → Cancelar en la lista de operaciones: diálogo y cancelación de las elegidas."""
        user = new_test_user(self.env, login="ai_cancel_ui", groups="base.group_user,stock.group_stock_user")
        user.password = user.login
        product = self.env["product.product"].create({"name": "ZQX UI CANCELAR", "detailed_type": "product"})
        wh = self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1)
        pickings = self.env["stock.picking"].create([{
            "picking_type_id": wh.int_type_id.id,
            "location_id": wh.lot_stock_id.id, "location_dest_id": wh.lot_stock_id.id,
            "move_ids": [(0, 0, {"name": product.name, "product_id": product.id, "product_uom_qty": 2,
                                 "product_uom": product.uom_id.id, "location_id": wh.lot_stock_id.id,
                                 "location_dest_id": wh.lot_stock_id.id})],
        } for _i in range(2)])
        action = self.env["ir.actions.act_window"].create({
            "name": "ZQX operaciones", "res_model": "stock.picking", "view_mode": "tree,form",
            "domain": [("id", "in", pickings.ids)]})
        self.browser_js("/web#action=%s" % action.id, """
            const until = (sel, text) => new Promise((resolve, reject) => {
                let n = 0;
                const t = setInterval(() => {
                    const el = [...document.querySelectorAll(sel)].find((e) => !text || e.textContent.includes(text));
                    if (el) { clearInterval(t); resolve(el); }
                    else if (++n > 150) { clearInterval(t); reject(new Error("No apareció " + sel + " " + (text || ""))); }
                }, 100);
            });
            (async () => {
                try {
                    await until(".o_list_view .o_data_row");
                    (await until(".o_list_view thead .o_list_record_selector input")).click();
                    (await until(".o_cp_action_menus .dropdown-toggle", "Acciones")).click();
                    (await until(".o-dropdown--menu .dropdown-item", "Cancelar")).click();
                    await until(".modal p", "Se cancelarán");
                    (await until(".modal button[name='action_confirm']")).click();
                    await until(".o_list_view .o_data_row td[name='state']", "Cancelado");
                    console.log("test successful");
                } catch (e) {
                    console.error(e.message);
                }
            })();
        """, login=user.login)
        pickings.invalidate_recordset(["state"])
        self.assertEqual(pickings.mapped("state"), ["cancel", "cancel"])

    def test_proposal_screens(self):
        # La propuesta es de la matriz: el comprador de Dromax la tiene como empresa activa.
        matriz = self.env["res.company"].create({"name": "ZQX MATRIZ UI PROPUESTA"})
        self.env["ir.config_parameter"].sudo().set_param(
            "dromax_asistente_compras.matrix_company_id", str(matriz.id))
        buyer = new_test_user(self.env, login="ai_proposal_buyer_ui",
                              groups="base.group_user,purchase.group_purchase_user",
                              company_id=matriz.id, company_ids=(matriz | self.env.company).ids)
        buyer.password = buyer.login
        product = self.env["product.product"].create({"name": "ZQX UI PROPUESTA [LAB UI]", "detailed_type": "product"})
        session = self.env["pos.session"].create({
            "config_id": self.env["pos.config"].create({"name": "ZQX TPV UI"}).id, "user_id": self.env.uid})
        order = self.env["pos.order"].create({
            "session_id": session.id, "amount_tax": 0, "amount_total": 2, "amount_paid": 2, "amount_return": 0,
            # 2 unidades en una semana: no es atípica (con 5 sería venta esporádica y no habría faltante).
            "lines": [(0, 0, {"product_id": product.id, "qty": 2, "price_unit": 1,
                              "price_subtotal": 2, "price_subtotal_incl": 2})],
        })
        order.write({"state": "paid", "date_order": order.date_order - timedelta(weeks=1)})
        self.env.flush_all()
        proposal = self.env["ai.purchase.proposal"]._generate()
        self._open("action=dromax_asistente_compras.action_ai_purchase_proposal&id=%d&view_type=form" % proposal.id,
                   ".o_form_view .o_field_one2many .o_data_row td[name='cantidad_final']", login=buyer.login)
        self._open("action=dromax_asistente_compras.action_ai_purchase_proposal&id=%d&view_type=form" % proposal.id,
                   ".o_form_view .o_statusbar_buttons button[name='action_create_rfq']", login=buyer.login)
        self._open("action=dromax_asistente_compras.action_ai_purchase_proposal_line",
                   ".o_list_view .o_group_header", login=buyer.login)

    def test_proposal_change_drogueria(self):
        """Al elegir otra droguería en la línea, su precio y su stock se ven sin guardar."""
        from .test_oferta import _ean
        matriz = self.env["res.company"].create({"name": "ZQX MATRIZ UI CAMBIO"})
        self.env["ir.config_parameter"].sudo().set_param(
            "dromax_asistente_compras.matrix_company_id", str(matriz.id))
        buyer = new_test_user(
            self.env, login="ai_proposal_change_ui",
            groups="base.group_user,purchase.group_purchase_user,dromax_asistente_compras.group_ai_purchase_user",
            company_id=matriz.id, company_ids=(matriz | self.env.company).ids)
        buyer.password = buyer.login
        product = self.env["product.product"].create({
            "name": "ZQX UI CAMBIO [LAB UI]", "detailed_type": "product", "barcode": _ean("759999900097")})
        for name, price, stock in (("ZQX DROG UI A", 1.5, 100), ("ZQX DROG UI B", 1.25, 1)):
            self.env["supplier.product.staging"].create({
                "partner_id": self.env["res.partner"].create({"name": name, "is_company": True}).id,
                "codigo_barras": product.barcode, "descripcion_producto": product.name, "fecha_lote": "30/01/2031",
                "precio_unitario": price, "porcentaje_oferta_vigente": 0.0,
                "precio_unitario_final": price, "stock_disponible": stock})
        session = self.env["pos.session"].create({
            "config_id": self.env["pos.config"].create({"name": "ZQX TPV UI CAMBIO"}).id, "user_id": self.env.uid})
        order = self.env["pos.order"].create({
            "session_id": session.id, "amount_tax": 0, "amount_total": 2, "amount_paid": 2, "amount_return": 0,
            "lines": [(0, 0, {"product_id": product.id, "qty": 2, "price_unit": 1,
                              "price_subtotal": 2, "price_subtotal_incl": 2})],
        })
        order.write({"state": "paid", "date_order": order.date_order - timedelta(weeks=1)})
        self.env.flush_all()
        proposal = self.env["ai.purchase.proposal"]._generate()
        line = proposal.line_ids.filtered(lambda l: l.product_id == product)
        drog_a = self.env["res.partner"].search([("name", "=", "ZQX DROG UI A")])
        line.write({"drogueria_id": drog_a.id, "cantidad_final": 3})     # B solo tiene 1
        self.browser_js(
            "/web#action=dromax_asistente_compras.action_ai_purchase_proposal&id=%d&view_type=form" % proposal.id, """
            const until = (sel, test) => new Promise((resolve, reject) => {
                let n = 0;
                const t = setInterval(() => {
                    const el = [...document.querySelectorAll(sel)].find((e) => !test || test(e.textContent || e.value || ""));
                    if (el) { clearInterval(t); resolve(el); }
                    else if (++n > 150) { clearInterval(t); reject(new Error("No apareció " + sel)); }
                }, 100);
            });
            const has = (text) => (content) => content.includes(text);
            (async () => {
                try {
                    const row = await until(".o_form_view .o_field_one2many .o_data_row", has("ZQX UI CAMBIO"));
                    row.querySelector("td[name='drogueria_id']").click();
                    const input = await until(".o_data_row.o_selected_row td[name='drogueria_id'] input");
                    input.value = "ZQX DROG UI B";
                    input.dispatchEvent(new Event("input", { bubbles: true }));
                    (await until(".o-autocomplete--dropdown-item a", has("ZQX DROG UI B"))).click();
                    // Sin guardar: el aviso de stock, el precio y el stock de B, y la cantidad hasta su stock.
                    await until(".modal .modal-body", has("solo tiene 1"));
                    (await until(".modal .modal-footer button")).click();
                    await until(".o_selected_row td[name='precio']", (t) => /1[.,]25/.test(t));
                    await until(".o_selected_row td[name='stock_drogueria']", (t) => t.trim() === "1");
                    await until(".o_selected_row td[name='cantidad_final'] input", (v) => v.trim() === "1");
                    (await until(".o_form_button_cancel")).click();          // descartar: nada se guarda
                    await until(".o_form_view .o_form_saved");
                    console.log("test successful");
                } catch (e) {
                    console.error(e.message);
                }
            })();
        """, login=buyer.login)
        line.invalidate_recordset()
        self.assertEqual((line.drogueria_id, line.cantidad_final), (drog_a, 3))

    def test_barcode_report(self):
        buyer = new_test_user(self.env, login="ai_bm_buyer_ui",
                              groups="base.group_user,base.group_system")
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
        if (document.querySelector(".o_ai_proposal")) {
            throw new Error("Botones de propuesta en una respuesta que no la trató");
        }
        // Pregunta predefinida que pide un dato: primero el laboratorio, después se envía.
        (await until(".o_ai_presets_bar .o_ai_preset[data-preset='revisar_laboratorio']")).click();
        await until(".o_ai_input_preset", "Revisa un laboratorio");
        input.value = "LETI";
        input.dispatchEvent(new Event("input", { bubbles: true }));
        (await until(".o_ai_send:not([disabled])")).click();
        await until(".o_ai_msg_user .o_ai_bubble", "Revisa un laboratorio: LETI");
        await until(".o_ai_msg_assistant", "Laboratorio revisado.");
        if (document.querySelector(".o_ai_input_preset")) {
            throw new Error("La pregunta predefinida sigue esperando un dato");
        }
        console.log("test successful");
    } catch (e) {
        console.error(e.message);
    }
})();
"""

CHAT_PROPOSAL = """
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
        const input = await until(".o_ai_input");
        input.value = "Muéstrame la propuesta";
        input.dispatchEvent(new Event("input", { bubbles: true }));
        (await until(".o_ai_send:not([disabled])")).click();
        await until(".o_ai_msg_assistant", "Propuesta lista.");
        await until(".o_ai_open_proposal");
        (await until(".o_ai_generate_drafts", "Generar borradores de LAB UI")).click();
        (await until(".modal-footer .btn-primary", "Generar borradores")).click();
        await until(".o_form_view .o_data_row", "ZQX UI CHAT");
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
            text_response("| Producto | Unidades |\n|---|---|\n| Kilometraje | 3 |"),
            text_response("Laboratorio revisado."))
        fake_tool = lambda env, name, args: {"columns": ["name"], "rows": [["Kilometraje"]],
                                             "row_count": 1, "truncated": False, "elapsed_ms": 2}
        with patch.object(openrouter, "Transport", lambda: transport), \
                patch.object(agent_mod, "call_tool", fake_tool):
            self.browser_js("/web#action=dromax_asistente_compras.action_ai_purchase_chat",
                            CHAT_FLOW, login="ai_chat_ui_buyer")

    def test_chat_proposal_buttons(self):
        """Bajo la respuesta que trató la propuesta: Abrir propuesta y Generar borradores,
        solo del laboratorio que trató."""
        from unittest.mock import patch

        from ..services import agent as agent_mod
        from ..services import openrouter
        from .test_chat import ScriptedTransport, text_response
        from .test_proposal_chat import call_response

        new_test_user(self.env, login="ai_chat_ui_proposal",
                      groups="base.group_user,purchase.group_purchase_user")
        self.env["ir.config_parameter"].sudo().set_param(
            "dromax_asistente_compras.openrouter_api_key", "sk-or-test")
        product = self.env["product.product"].create({"name": "ZQX UI CHAT [LAB UI]", "detailed_type": "product"})
        drogueria = self.env["res.partner"].create({"name": "ZQX DROG UI", "is_company": True})
        other = self.env["product.product"].create({"name": "ZQX UI OTRO [OTRO UI]", "detailed_type": "product"})
        proposal = self.env["ai.purchase.proposal"].create({"name": "ZQX Propuesta UI", "line_ids": [(0, 0, {
            "company_id": self.env.company.id, "product_id": p.id, "laboratorio": lab, "cantidad_sugerida": 2,
            "cantidad_final": 2, "drogueria_id": drogueria.id, "drogueria_sugerida_id": drogueria.id,
            "precio": 1.0, "precio_sugerido": 1.0}) for p, lab in [(product, "LAB UI"), (other, "OTRO UI")]]})
        transport = ScriptedTransport(call_response("get_proposal", {"laboratorio": "LAB UI"}),
                                      text_response("Propuesta lista."))
        fake_tool = lambda env, name, args: {"propuesta": {"id": proposal.id}, "row_count": 1,
                                             "lineas": [{"laboratorio": "LAB UI"}]}
        with patch.object(openrouter, "Transport", lambda: transport), \
                patch.object(agent_mod, "call_tool", fake_tool):
            self.browser_js("/web#action=dromax_asistente_compras.action_ai_purchase_chat",
                            CHAT_PROPOSAL, login="ai_chat_ui_proposal")
        self.assertEqual(proposal.state, "in_review")
        self.assertEqual(proposal.purchase_order_ids.order_line.product_id, product)
