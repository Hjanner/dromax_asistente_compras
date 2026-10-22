# -*- coding: utf-8 -*-
"""Agente y chat, sin red: un transporte falso hace de OpenRouter."""
import json
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from ..services import agent as agent_mod
from ..services import openrouter
from ..services.agent import run_agent
from ..services.openrouter import OpenRouterError
from ..services.prompt import build_system_prompt

P = "dromax_asistente_compras."
GROUPS = "base.group_user,dromax_asistente_compras.group_ai_purchase_user"


def tool_call_response(query, call_id="call_1"):
    return {
        "id": "gen-tool", "model": "fake/model",
        "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": "run_readonly_sql", "arguments": json.dumps({"query": query})},
        }]}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "cost": 0.002},
    }


def text_response(text):
    return {
        "id": "gen-text", "model": "fake/model",
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 100, "cost": 0.003},
    }


class ScriptedTransport:
    """Devuelve las respuestas en orden y guarda lo que se le envió."""

    def __init__(self, *responses):
        self.responses, self.payloads = list(responses), []

    def request(self, method, url, headers, payload=None, timeout=60):
        self.payloads.append(payload)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestChat(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(P + "openrouter_api_key", "sk-or-test")
        cls.buyer = new_test_user(cls.env, login="ai_chat_buyer", groups=GROUPS)
        cls.other = new_test_user(cls.env, login="ai_chat_other", groups=GROUPS)
        cls.Session = cls.env["ai.purchase.chat.session"]

    def setUp(self):
        super().setUp()
        # La herramienta real necesita el rol de solo lectura; aquí se simula.
        self.tool_calls = []

        def fake_call_tool(env, name, args):
            self.tool_calls.append((env.uid, name, args))
            return {"columns": ["n"], "rows": [[11]], "row_count": 1, "truncated": False, "elapsed_ms": 3}

        patcher = patch.object(agent_mod, "call_tool", fake_call_tool)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _transport(self, *responses):
        transport = ScriptedTransport(*responses)
        patcher = patch.object(openrouter, "Transport", lambda: transport)
        patcher.start()
        self.addCleanup(patcher.stop)
        return transport

    # --- Agente --------------------------------------------------------------
    def test_agent_tool_loop(self):
        transport = self._transport(
            tool_call_response("SELECT count(*) AS n FROM product_template"),
            text_response("Hay **11** productos."))
        env = self.env(user=self.buyer)
        res = run_agent(env, "¿Cuántos productos hay?", level="basic")
        self.assertEqual(res["state"], "ok")
        self.assertEqual(res["content"], "Hay **11** productos.")
        self.assertEqual(self.tool_calls, [(self.buyer.id, "run_readonly_sql",
                                            {"query": "SELECT count(*) AS n FROM product_template"})])
        self.assertEqual(res["tool_trace"][0]["row_count"], 1)
        self.assertEqual(res["prompt_tokens"], 2200)
        self.assertAlmostEqual(res["cost"], 0.005)
        # Segunda llamada: el resultado de la herramienta va con su tool_call_id.
        second = transport.payloads[1]["messages"]
        self.assertEqual(second[-1]["role"], "tool")
        self.assertEqual(second[-1]["tool_call_id"], "call_1")
        self.assertEqual(second[0]["role"], "system")

    def test_agent_out_of_steps_forces_answer(self):
        env = self.env(user=self.buyer)
        env["ir.config_parameter"].sudo().set_param(P + "agent_max_steps", "2")
        transport = self._transport(
            tool_call_response("SELECT 1", "c1"), tool_call_response("SELECT 2", "c2"),
            text_response("Con lo consultado: ..."))
        res = run_agent(env, "pregunta", level="basic")
        self.assertEqual(res["state"], "ok")
        self.assertEqual(res["content"], "Con lo consultado: ...")
        # Antes de la última consulta se le avisa que no le quedan más.
        results = [m["content"] for m in transport.payloads[1]["messages"] if m["role"] == "tool"]
        self.assertIn("una sola consulta", results[0])
        self.assertNotIn("una sola consulta", results[1])
        # La última llamada va sin herramientas y con las consultas en texto.
        final = transport.payloads[-1]
        self.assertNotIn("tools", final)
        self.assertNotIn("tool_choice", final)
        roles = [m["role"] for m in final["messages"]]
        self.assertNotIn("tool", roles)
        self.assertEqual(roles[-3:], ["user", "assistant", "user"])
        self.assertFalse(any(m.get("tool_calls") for m in final["messages"]))
        summary = final["messages"][-2]["content"]
        self.assertIn("SELECT 1", summary)
        self.assertIn("SELECT 2", summary)
        self.assertIn('"rows": [[11]]', summary)
        self.assertEqual(final["messages"][-1]["content"], agent_mod.FINAL_INSTRUCTION)

    def test_agent_out_of_steps_model_keeps_asking(self):
        """El modelo pide otra consulta en vez de responder (pasó con Gemini)."""
        env = self.env(user=self.buyer)
        env["ir.config_parameter"].sudo().set_param(P + "agent_max_steps", "1")
        self._transport(tool_call_response("SELECT 1", "c1"), tool_call_response("SELECT 2", "c2"))
        res = run_agent(env, "pregunta", level="basic")
        self.assertEqual(res["state"], "error")
        self.assertIn("1 consultas que tiene permitidas", res["content"])
        self.assertEqual(len(res["tool_trace"]), 1)

    def test_agent_openrouter_error(self):
        self._transport(OpenRouterError("OpenRouter respondió 402: sin crédito"))
        res = run_agent(self.env(user=self.buyer), "hola", level="basic")
        self.assertEqual(res["state"], "error")
        self.assertIn("402", res["content"])

    def test_agent_bad_tool_arguments(self):
        bad = tool_call_response("x")
        bad["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{no json"
        self._transport(bad, text_response("ok"))
        res = run_agent(self.env(user=self.buyer), "hola", level="basic")
        self.assertIn("JSON", res["tool_trace"][0]["error"])
        self.assertFalse(self.tool_calls)

    def test_system_prompt(self):
        text = build_system_prompt(self.env)
        self.assertIn("product_template", text)
        self.assertIn("res_company", text)
        self.assertNotIn("res_partner", text.split("Tablas que puedes consultar")[1])
        # Las vistas ai_v_* aún no existen: no se anuncian.
        self.assertNotIn("ai_v_rotacion", text)
        self.assertIn("últimas 8 semanas", text)
        self.assertIn("como máximo 8 consultas", text)
        self.assertIn("presentación distinta", text)
        self.env["ir.config_parameter"].sudo().set_param(P + "rotation_window_weeks", "12")
        self.assertIn("últimas 12 semanas", build_system_prompt(self.env))

    # --- Conversaciones ------------------------------------------------------
    def test_send_free_text(self):
        transport = self._transport(text_response("Respuesta"))
        session = self.Session.with_user(self.buyer).create({})
        res = session.chat_send(text="¿Qué se vendió ayer?", level="basic")
        user_msg, answer = res["messages"]
        self.assertEqual(user_msg["role"], "user")
        self.assertEqual(answer["content"], "Respuesta")
        self.assertEqual(answer["level"], "basic")
        self.assertAlmostEqual(answer["cost"], 0.003)
        self.assertEqual(session.name, "¿Qué se vendió ayer?")
        self.assertEqual(transport.payloads[0]["model"], openrouter.DEFAULT_MODELS["basic"])

    def test_preset_forces_level_and_prompt(self):
        transport = self._transport(text_response("Propuesta"))
        session = self.Session.with_user(self.buyer).create({})
        res = session.chat_send(preset="compra_semana", level="basic")
        self.assertEqual(res["messages"][0]["preset_label"], "Arma la compra de esta semana")
        self.assertEqual(res["messages"][1]["level"], "advanced")
        self.assertEqual(transport.payloads[0]["model"], openrouter.DEFAULT_MODELS["advanced"])
        self.assertIn("propuesta de compra", transport.payloads[0]["messages"][-1]["content"])
        log = self.env["ai.usage.log"].search([("user_id", "=", self.buyer.id)], limit=1)
        self.assertEqual(log.task, "purchase_proposal")

    def test_history_is_sent(self):
        transport = self._transport(text_response("Primera"), text_response("Segunda"))
        session = self.Session.with_user(self.buyer).create({})
        session.chat_send(text="uno", level="basic")
        session.chat_send(text="dos", level="basic")
        sent = [(m["role"], m["content"]) for m in transport.payloads[1]["messages"][1:]]
        self.assertEqual(sent, [("user", "uno"), ("assistant", "Primera"), ("user", "dos")])

    def test_invalid_input(self):
        session = self.Session.with_user(self.buyer).create({})
        with self.assertRaises(UserError):
            session.chat_send(text="   ")
        with self.assertRaises(UserError):
            session.chat_send(preset="no_existe")
        with self.assertRaises(UserError):
            session.chat_send(text="x" * 5000)

    def test_sessions_are_private(self):
        self._transport(text_response("hola"))
        mine = self.Session.with_user(self.buyer).create({})
        mine.chat_send(text="hola", level="basic")
        others = self.Session.with_user(self.other)
        self.assertFalse(others.search([("id", "=", mine.id)]))
        with self.assertRaises(AccessError):
            mine.with_user(self.other).chat_messages()
        self.assertFalse(self.env["ai.purchase.chat.message"].with_user(self.other).search(
            [("session_id", "=", mine.id)]))
        boot = others.chat_bootstrap()
        self.assertFalse(boot["sessions"])
        self.assertTrue(boot["presets"])

    def test_no_group_no_access(self):
        outsider = new_test_user(self.env, login="ai_chat_outsider", groups="base.group_user")
        with self.assertRaises(AccessError):
            self.Session.with_user(outsider).chat_bootstrap()

    def test_delete(self):
        session = self.Session.with_user(self.buyer).create({})
        self.assertTrue(session.chat_delete())
        self.assertFalse(session.exists())
