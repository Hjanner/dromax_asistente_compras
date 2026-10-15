# -*- coding: utf-8 -*-
"""Cliente de OpenRouter con tres niveles de modelo y registro de consumo.

Una sola llave y tres niveles, cada uno con su modelo configurable en Ajustes
(ir.config_parameter dromax_asistente_compras.model_basic / model_medium /
model_advanced). Lo que tiene que ver con compras (propuesta, ajustes, flujo
dinámico) va siempre al nivel avanzado: lo fija la tarea, no quien llama.

Cada llamada, buena o fallida, deja una fila en ai.usage.log con usuario,
nivel, modelo, tokens y el costo que informa OpenRouter (usage.cost, que la
API incluye siempre en la respuesta). La fila se guarda en una transacción
aparte: si la operación que llamó al modelo falla después y se deshace, el
gasto ya se produjo y tiene que quedar registrado.

La red se aísla en `Transport` para poder sustituirla en los tests. Usa urllib:
sin dependencias nuevas.
"""
import json
import logging
import time
import urllib.error
import urllib.request

from odoo.modules import module as odoo_module

from .config import get_int_setting, get_setting

_logger = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"

LEVELS = ("basic", "medium", "advanced")
DEFAULT_MODELS = {
    "basic": "deepseek/deepseek-v4-flash",
    "medium": "google/gemini-3.8-flash",
    "advanced": "anthropic/claude-opus-5.5",
}

# Tareas con nivel fijo. Las de compras siempre van al avanzado.
TASK_LEVELS = {
    "purchase_proposal": "advanced",
    "proposal_adjustment": "advanced",
    "dynamic_flow": "advanced",
}

DEFAULT_TIMEOUT_SECONDS = 120


class OpenRouterError(Exception):
    pass


class Transport:
    """HTTP mínimo. Sustituible en tests."""

    def request(self, method, url, headers, payload=None, timeout=60):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", "replace")
            try:
                detail = json.loads(body).get("error", {}).get("message") or body
            except (ValueError, AttributeError):
                detail = body
            raise OpenRouterError("OpenRouter respondió %s: %s" % (err.code, detail[:300]))
        except urllib.error.URLError as err:
            raise OpenRouterError("No se pudo contactar con OpenRouter: %s" % err.reason)
        except TimeoutError:
            raise OpenRouterError("OpenRouter no respondió a tiempo (%s s)." % timeout)
        except json.JSONDecodeError as err:
            raise OpenRouterError("OpenRouter devolvió una respuesta que no es JSON: %s" % err)


def get_model(env, level):
    if level not in LEVELS:
        raise OpenRouterError("Nivel de modelo desconocido: %s." % level)
    return get_setting(env, "model_" + level, DEFAULT_MODELS[level])


def resolve_level(level=None, task=None):
    """Nivel efectivo: el de la tarea si la tiene fijado; si no, el pedido;
    si no se pidió ninguno, el básico."""
    if task in TASK_LEVELS:
        return TASK_LEVELS[task]
    return level or "basic"


class OpenRouterClient:

    def __init__(self, env, transport=None):
        self.env = env
        self.transport = transport or Transport()

    # --- API pública ----------------------------------------------------
    def chat(self, messages, level=None, task=None, tools=None, tool_choice=None, **extra):
        """POST /chat/completions. Devuelve la respuesta de OpenRouter tal cual
        (dict con 'choices', 'usage', 'model'...).

        level: 'basic' | 'medium' | 'advanced'. task: si está en TASK_LEVELS,
        manda sobre level. extra: otros parámetros de la API (temperature,
        max_tokens, plugins...).
        """
        level = resolve_level(level, task)
        model = get_model(self.env, level)
        payload = {"model": model, "messages": messages, **extra}
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice

        started = time.monotonic()
        log = {"level": level, "model": model, "task": task or False}
        try:
            data = self.transport.request(
                "POST", BASE_URL + "/chat/completions", self._headers(), payload, self._timeout())
            if not isinstance(data, dict):
                raise OpenRouterError("Respuesta inesperada de OpenRouter.")
            if data.get("error"):
                # OpenRouter puede devolver 200 con un error del proveedor final.
                err = data["error"]
                raise OpenRouterError("Error del proveedor: %s" % (
                    err.get("message") if isinstance(err, dict) else err))
        except OpenRouterError as exc:
            log.update(state="error", error_message=str(exc))
            self._log(log, started)
            raise
        log.update(self._usage_values(data), state="ok")
        self._log(log, started)
        return data

    def key_info(self):
        """GET /key: comprueba la llave y devuelve su tope y consumo."""
        data = self.transport.request("GET", BASE_URL + "/key", self._headers(), None, 30)
        return (data or {}).get("data") or {}

    # --- Interno -----------------------------------------------------------
    def _headers(self):
        api_key = get_setting(self.env, "openrouter_api_key", env_aliases=("OPENROUTER_API_KEY",))
        if not api_key:
            raise OpenRouterError(
                "Falta la llave de OpenRouter (Ajustes → Asistente de Compras IA).")
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "http://localhost"
        return {
            "Authorization": "Bearer %s" % api_key,
            "Content-Type": "application/json",
            # Atribución que recomienda OpenRouter; no afecta a la respuesta.
            "HTTP-Referer": base_url,
            "X-Title": "Dromax Asistente de Compras",
        }

    def _timeout(self):
        return get_int_setting(self.env, "http_timeout_seconds", DEFAULT_TIMEOUT_SECONDS, 600)

    @staticmethod
    def _usage_values(data):
        usage = data.get("usage") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        return {
            "model_used": data.get("model") or False,
            "provider": data.get("provider") or False,
            "generation_id": data.get("id") or False,
            "prompt_tokens": usage.get("prompt_tokens") or 0,
            "completion_tokens": usage.get("completion_tokens") or 0,
            "cached_tokens": prompt_details.get("cached_tokens") or 0,
            "reasoning_tokens": completion_details.get("reasoning_tokens") or 0,
            "cost": usage.get("cost") or 0.0,
        }

    def _log(self, vals, started):
        vals = dict(vals, user_id=self.env.uid,
                    duration_ms=int((time.monotonic() - started) * 1000))
        try:
            if odoo_module.current_test:
                # En los tests no se abre otra conexión: no vería los datos
                # sin confirmar del test y dejaría filas en la base.
                self.env["ai.usage.log"].sudo().create(vals)
                return
            with self.env.registry.cursor() as cr:
                self.env(cr=cr)["ai.usage.log"].sudo().create(vals)
        except Exception:
            # Que falle el registro no puede tumbar la respuesta al usuario.
            _logger.exception("No se pudo registrar el consumo de OpenRouter: %s", vals)
