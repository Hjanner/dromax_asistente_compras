# -*- coding: utf-8 -*-
"""Herramientas que el módulo expone al modelo.

Cada herramienta tiene su definición (formato de funciones de OpenAI Chat
Completions, el que habla OpenRouter) y su implementación. `call_tool` es la
única puerta de entrada: comprueba el grupo del usuario y convierte cualquier
fallo en un resultado {'error': ...} que el modelo puede leer y corregir, en
vez de una excepción que cortaría la conversación.

Crear la orden de compra en borrador NO es una herramienta: lo hace el botón
del comprador.
"""
import logging

from .readonly_sql import ReadonlySqlError, get_whitelist, run_readonly_sql
from .sql_validator import SqlValidationError

_logger = logging.getLogger(__name__)

GROUP_XMLID = "dromax_asistente_compras.group_ai_purchase_user"

RUN_READONLY_SQL = {
    "type": "function",
    "function": {
        "name": "run_readonly_sql",
        "description": (
            "Ejecuta una consulta SQL de solo lectura sobre la base de datos de Odoo "
            "(PostgreSQL) y devuelve las filas. Una sola sentencia SELECT o "
            "WITH ... SELECT, solo sobre las tablas y vistas permitidas. Agrega y "
            "filtra en SQL (SUM, GROUP BY, WHERE) en vez de traer filas sueltas: "
            "el resultado se corta en un máximo de filas e indica 'truncated'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Consulta SELECT de PostgreSQL.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

_IMPLEMENTATIONS = {
    "run_readonly_sql": lambda env, args: run_readonly_sql(env, args.get("query")),
}


def _existing_relations(env, names):
    """Las de `names` que existen en el esquema public (tablas o vistas)."""
    env.cr.execute("""
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relname = ANY(%s)
    """, (list(names),))
    return {row[0] for row in env.cr.fetchall()}


def tool_definitions(env):
    """Definiciones para enviar al modelo, con la lista blanca vigente.

    Solo anuncia las tablas que existen en esta base: las vistas ai_v_* que
    aún no se crearon le harían gastar consultas en errores.
    """
    spec = {**RUN_READONLY_SQL, "function": dict(RUN_READONLY_SQL["function"])}
    tables = _existing_relations(env, get_whitelist(env))
    spec["function"]["description"] += " Tablas permitidas: %s." % ", ".join(sorted(tables))
    return [spec]


def call_tool(env, name, args):
    """Ejecuta la herramienta `name` con `args` (dict) en nombre de env.user."""
    if not env.user.has_group(GROUP_XMLID):
        return {"error": "El usuario no tiene acceso al Asistente de Compras IA."}
    impl = _IMPLEMENTATIONS.get(name)
    if impl is None:
        return {"error": "Herramienta desconocida: %s." % name}
    if not isinstance(args, dict):
        return {"error": "Los argumentos deben ser un objeto JSON."}
    try:
        return impl(env, args)
    except SqlValidationError as exc:
        return {"error": "Consulta rechazada: %s" % exc}
    except ReadonlySqlError as exc:
        return {"error": str(exc)}
    except Exception:
        _logger.exception("Fallo inesperado en la herramienta %s", name)
        return {"error": "Error interno al ejecutar la herramienta."}
