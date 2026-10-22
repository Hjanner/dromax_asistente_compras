# -*- coding: utf-8 -*-
"""Lectura de la configuración del módulo.

Fuentes, de mayor a menor prioridad:
  1. ir.config_parameter 'dromax_asistente_compras.<nombre>' (Ajustes)
  2. opción 'ai_purchase_<nombre>' en odoo.conf
  3. variable de entorno 'AI_PURCHASE_<NOMBRE>' (o los alias indicados)
  4. valor por defecto
"""
import os

from odoo.tools import config as odoo_config

PARAM_PREFIX = "dromax_asistente_compras."


def get_setting(env, name, default=None, env_aliases=()):
    value = env["ir.config_parameter"].sudo().get_param(PARAM_PREFIX + name)
    if not value:
        value = odoo_config.get("ai_purchase_" + name)
    if not value:
        for var in ("AI_PURCHASE_" + name.upper(),) + tuple(env_aliases):
            value = os.environ.get(var)
            if value:
                break
    return value if value not in (None, "", False) else default


def get_int_setting(env, name, default, maximum=None):
    try:
        value = int(get_setting(env, name, default))
    except (TypeError, ValueError):
        value = default
    value = max(1, value)
    return min(value, maximum) if maximum else value


# Consultas (rondas de herramientas) que puede hacer el agente por respuesta.
DEFAULT_AGENT_MAX_STEPS = 8


def get_agent_max_steps(env):
    return get_int_setting(env, "agent_max_steps", DEFAULT_AGENT_MAX_STEPS, 20)
