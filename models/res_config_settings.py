# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError

from ..services import openrouter
from ..services.config import PARAM_PREFIX
from ..services.readonly_sql import DEFAULT_MAX_ROWS, DEFAULT_TIMEOUT_SECONDS


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Ajustes es solo para administradores, así que la llave no la ve nadie más.
    ai_purchase_openrouter_api_key = fields.Char(
        "Llave de OpenRouter", config_parameter=PARAM_PREFIX + "openrouter_api_key")
    ai_purchase_model_basic = fields.Char(
        "Modelo básico", config_parameter=PARAM_PREFIX + "model_basic",
        default=openrouter.DEFAULT_MODELS["basic"])
    ai_purchase_model_medium = fields.Char(
        "Modelo medio", config_parameter=PARAM_PREFIX + "model_medium",
        default=openrouter.DEFAULT_MODELS["medium"])
    ai_purchase_model_advanced = fields.Char(
        "Modelo avanzado", config_parameter=PARAM_PREFIX + "model_advanced",
        default=openrouter.DEFAULT_MODELS["advanced"])
    ai_purchase_sql_max_rows = fields.Integer(
        "Filas máximas por consulta", config_parameter=PARAM_PREFIX + "sql_max_rows",
        default=DEFAULT_MAX_ROWS)
    ai_purchase_sql_timeout_seconds = fields.Integer(
        "Tiempo máximo por consulta (s)", config_parameter=PARAM_PREFIX + "sql_timeout_seconds",
        default=DEFAULT_TIMEOUT_SECONDS)

    def action_ai_purchase_test_openrouter(self):
        """Comprueba la llave guardada y muestra su tope de crédito y consumo."""
        self.ensure_one()
        try:
            info = openrouter.OpenRouterClient(self.env).key_info()
        except openrouter.OpenRouterError as exc:
            raise UserError(str(exc)) from exc
        limit = info.get("limit")
        message = _("Llave válida. Consumido: %(usage).2f USD. Tope: %(limit)s.",
                    usage=info.get("usage") or 0.0,
                    limit=_("%.2f USD", limit) if limit is not None else _("sin tope (conviene ponerle uno)"))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("OpenRouter"),
                "message": message,
                "type": "success" if limit is not None else "warning",
                "sticky": False,
            },
        }
