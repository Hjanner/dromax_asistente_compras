# -*- coding: utf-8 -*-
"""Bucle del agente: modelo ↔ herramientas hasta tener una respuesta.

1. system prompt + historial reciente + mensaje del comprador
2. si el modelo pide herramientas, se ejecutan (call_tool comprueba el grupo
   y nunca lanza excepciones) y se le devuelven los resultados
3. se repite hasta que responda texto o se agoten los pasos; en ese caso se
   le pide una última respuesta sin herramientas con lo que ya tiene

Devuelve siempre un dict (nunca lanza por un fallo del modelo), para que el
chat guarde también las respuestas fallidas con su motivo.
"""
import json
import logging
import time

from .config import get_int_setting
from .openrouter import OpenRouterClient, OpenRouterError, resolve_level
from .prompt import build_system_prompt
from .tools import call_tool, tool_definitions

_logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 8
# Mensajes previos de la conversación que se reenvían como contexto.
HISTORY_MESSAGES = 20
# Tope de caracteres de un resultado de herramienta antes de enviarlo al
# modelo (run_readonly_sql ya recorta filas y celdas; esto es la última red).
MAX_TOOL_RESULT_CHARS = 60000


def _tool_content(result):
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + ' …(resultado recortado: agrega o filtra más en SQL)'
    return text


def _trace_entry(name, args, result):
    """Lo que se guarda para que el comprador vea qué consultó el modelo."""
    entry = {"tool": name, "query": args.get("query") if isinstance(args, dict) else None}
    if "error" in result:
        entry["error"] = result["error"]
    else:
        entry.update(row_count=result.get("row_count"), truncated=result.get("truncated"),
                     elapsed_ms=result.get("elapsed_ms"))
    return entry


def run_agent(env, user_text, history=(), level=None, task=None, transport=None):
    """Responde a `user_text` en nombre de env.user.

    history: [{'role': 'user'|'assistant', 'content': str}, ...] en orden.
    Devuelve {'state', 'content', 'level', 'model', 'tool_trace', 'prompt_tokens',
    'completion_tokens', 'cost', 'duration_ms'}.
    """
    started = time.monotonic()
    level = resolve_level(level, task)
    client = OpenRouterClient(env, transport)
    tools = tool_definitions(env)
    max_steps = get_int_setting(env, "agent_max_steps", DEFAULT_MAX_STEPS, 20)

    messages = [{"role": "system", "content": build_system_prompt(env)}]
    for item in list(history)[-HISTORY_MESSAGES:]:
        if item.get("role") in ("user", "assistant") and item.get("content"):
            messages.append({"role": item["role"], "content": item["content"]})
    messages.append({"role": "user", "content": user_text})

    out = {"state": "ok", "content": "", "level": level, "model": False, "tool_trace": [],
           "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}

    def ask(**kw):
        data = client.chat(messages, level=level, task=task, **kw)
        usage = data.get("usage") or {}
        out["prompt_tokens"] += usage.get("prompt_tokens") or 0
        out["completion_tokens"] += usage.get("completion_tokens") or 0
        out["cost"] += usage.get("cost") or 0.0
        out["model"] = data.get("model") or out["model"]
        return ((data.get("choices") or [{}])[0].get("message")) or {}

    try:
        for _step in range(max_steps):
            msg = ask(tools=tools, tool_choice="auto")
            calls = msg.get("tool_calls") or []
            if not calls:
                out["content"] = (msg.get("content") or "").strip()
                break
            messages.append({"role": "assistant", "content": msg.get("content") or "",
                             "tool_calls": calls})
            for call in calls:
                fn = call.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = None
                result = (call_tool(env, fn.get("name"), args) if args is not None
                          else {"error": "Los argumentos no son JSON válido."})
                out["tool_trace"].append(_trace_entry(fn.get("name"), args, result))
                messages.append({"role": "tool", "tool_call_id": call.get("id"),
                                 "content": _tool_content(result)})
        else:
            # Sin pasos: que redacte con lo que ya consultó, sin más herramientas.
            msg = ask(tools=tools, tool_choice="none")
            out["content"] = (msg.get("content") or "").strip()
        if not out["content"]:
            out.update(state="error", content="El modelo no devolvió ninguna respuesta. Intenta de nuevo.")
    except OpenRouterError as exc:
        out.update(state="error", content="No se pudo completar la respuesta: %s" % exc)
    except Exception:
        _logger.exception("Fallo inesperado en el agente de compras")
        out.update(state="error", content="Error interno del asistente. Revisa el registro del servidor.")

    out["duration_ms"] = int((time.monotonic() - started) * 1000)
    return out
