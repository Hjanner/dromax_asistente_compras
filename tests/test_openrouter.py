# -*- coding: utf-8 -*-
"""Niveles de modelo, cliente de OpenRouter y registro de consumo.

Sin red: un transporte falso devuelve respuestas con la forma real de la API.
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from ..services import openrouter
from ..services.openrouter import OpenRouterClient, OpenRouterError

P = "dromax_asistente_compras."

RESPONSE = {
    "id": "gen-123",
    "model": "anthropic/claude-opus-5.5-20260901",
    "provider": "Anthropic",
    "choices": [{"message": {"role": "assistant", "content": "Hola"}}],
    "usage": {
        "prompt_tokens": 1200,
        "completion_tokens": 300,
        "total_tokens": 1500,
        "cost": 0.0108,
        "prompt_tokens_details": {"cached_tokens": 1000},
        "completion_tokens_details": {"reasoning_tokens": 50},
    },
}


class FakeTransport:

    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def request(self, method, url, headers, payload=None, timeout=60):
        self.calls.append({"method": method, "url": url, "headers": headers, "payload": payload})
        if self.error:
            raise self.error
        return self.response


@tagged("post_install", "-at_install", "dromax_asistente_compras")
class TestOpenRouter(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        cls.ICP.set_param(P + "openrouter_api_key", "sk-or-test")
        cls.buyer = new_test_user(
            cls.env, login="ai_purchase_buyer_or",
            groups="base.group_user,dromax_asistente_compras.group_ai_purchase_user")
        cls.Log = cls.env["ai.usage.log"]

    def _client(self, **kw):
        transport = FakeTransport(**kw)
        return OpenRouterClient(self.env(user=self.buyer), transport), transport

    def test_default_models_in_parameters(self):
        for level, model in openrouter.DEFAULT_MODELS.items():
            self.assertEqual(self.ICP.get_param(P + "model_" + level), model)

    def test_level_picks_configured_model(self):
        self.ICP.set_param(P + "model_medium", "google/otro-modelo")
        client, transport = self._client(response=RESPONSE)
        client.chat([{"role": "user", "content": "hola"}], level="medium")
        self.assertEqual(transport.calls[0]["payload"]["model"], "google/otro-modelo")

    def test_purchase_tasks_always_advanced(self):
        client, transport = self._client(response=RESPONSE)
        client.chat([{"role": "user", "content": "x"}], level="basic", task="purchase_proposal")
        self.assertEqual(transport.calls[0]["payload"]["model"], openrouter.DEFAULT_MODELS["advanced"])
        self.assertEqual(openrouter.resolve_level(), "basic")
        self.assertEqual(openrouter.resolve_level("medium", task="otra"), "medium")

    def test_request_shape(self):
        tools = [{"type": "function", "function": {"name": "run_readonly_sql", "parameters": {}}}]
        client, transport = self._client(response=RESPONSE)
        client.chat([{"role": "user", "content": "x"}], level="basic", tools=tools,
                    tool_choice="auto", temperature=0)
        call = transport.calls[0]
        self.assertEqual(call["url"], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], "Bearer sk-or-test")
        self.assertEqual(call["payload"]["tools"], tools)
        self.assertEqual(call["payload"]["tool_choice"], "auto")
        self.assertEqual(call["payload"]["temperature"], 0)

    def test_usage_logged(self):
        client, _t = self._client(response=RESPONSE)
        data = client.chat([{"role": "user", "content": "x"}], task="purchase_proposal")
        self.assertEqual(data["choices"][0]["message"]["content"], "Hola")
        log = self.Log.search([("generation_id", "=", "gen-123")])
        self.assertEqual(len(log), 1)
        self.assertRecordValues(log, [{
            "user_id": self.buyer.id,
            "level": "advanced",
            "model": openrouter.DEFAULT_MODELS["advanced"],
            "model_used": "anthropic/claude-opus-5.5-20260901",
            "provider": "Anthropic",
            "task": "purchase_proposal",
            "state": "ok",
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "total_tokens": 1500,
            "cached_tokens": 1000,
            "reasoning_tokens": 50,
            "cost": 0.0108,
        }])

    def test_error_logged_and_raised(self):
        client, _t = self._client(error=OpenRouterError("OpenRouter respondió 402: sin crédito"))
        with self.assertRaisesRegex(OpenRouterError, "402"):
            client.chat([{"role": "user", "content": "x"}], level="medium")
        log = self.Log.search([("user_id", "=", self.buyer.id)], limit=1)
        self.assertEqual(log.state, "error")
        self.assertIn("402", log.error_message)
        self.assertEqual(log.cost, 0)

    def test_provider_error_inside_200(self):
        client, _t = self._client(response={"error": {"message": "proveedor caído"}})
        with self.assertRaisesRegex(OpenRouterError, "proveedor caído"):
            client.chat([{"role": "user", "content": "x"}])

    def test_missing_key(self):
        self.ICP.set_param(P + "openrouter_api_key", False)
        client, transport = self._client(response=RESPONSE)
        with self.assertRaisesRegex(OpenRouterError, "llave"):
            client.chat([{"role": "user", "content": "x"}])
        self.assertFalse(transport.calls)

    def test_unknown_level(self):
        client, _t = self._client(response=RESPONSE)
        with self.assertRaises(OpenRouterError):
            client.chat([{"role": "user", "content": "x"}], level="ultra")

    def test_log_is_read_only_for_buyers(self):
        with self.assertRaises(AccessError):
            self.Log.with_user(self.buyer).search([])
        admin = self.Log.with_user(self.env.ref("base.user_admin"))
        self.assertTrue(admin.check_access_rights("read", raise_exception=False))
        self.assertFalse(admin.check_access_rights("create", raise_exception=False))
        self.assertFalse(admin.check_access_rights("write", raise_exception=False))

    def test_settings_write_parameters(self):
        settings = self.env["res.config.settings"].create({
            "ai_purchase_model_advanced": "anthropic/otro-opus",
            "ai_purchase_sql_max_rows": 50,
        })
        settings.execute()
        self.assertEqual(self.ICP.get_param(P + "model_advanced"), "anthropic/otro-opus")
        self.assertEqual(self.ICP.get_param(P + "sql_max_rows"), "50")
        self.assertEqual(openrouter.get_model(self.env, "advanced"), "anthropic/otro-opus")

    def test_settings_test_key(self):
        settings = self.env["res.config.settings"].create({})
        transport = FakeTransport(response={"data": {"usage": 1.25, "limit": 20}})
        orig = openrouter.Transport
        openrouter.Transport = lambda: transport
        try:
            action = settings.action_ai_purchase_test_openrouter()
            self.assertEqual(action["params"]["type"], "success")
            self.assertIn("1.25", action["params"]["message"])
            transport.error = OpenRouterError("OpenRouter respondió 401: clave inválida")
            with self.assertRaisesRegex(UserError, "401"):
                settings.action_ai_purchase_test_openrouter()
        finally:
            openrouter.Transport = orig
        self.assertEqual(transport.calls[0]["url"], "https://openrouter.ai/api/v1/key")
