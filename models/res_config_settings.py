# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError

from ..services import openrouter
from ..services.config import PARAM_PREFIX
from ..services import prompt
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

    ai_purchase_rotation_window_weeks = fields.Integer(
        "Semanas de historial", config_parameter=PARAM_PREFIX + "rotation_window_weeks",
        default=prompt.DEFAULT_WINDOW_WEEKS)
    ai_purchase_outlier_mad_factor = fields.Integer(
        "Umbral de atípicos (× MAD)", config_parameter=PARAM_PREFIX + "outlier_mad_factor",
        default=prompt.DEFAULT_OUTLIER_MAD)
    ai_purchase_coverage_weeks = fields.Integer(
        "Semanas de cobertura", config_parameter=PARAM_PREFIX + "coverage_weeks",
        default=prompt.DEFAULT_COVERAGE_WEEKS)
    ai_purchase_reorder_point_pct = fields.Integer(
        "Punto de pedido (%)", config_parameter=PARAM_PREFIX + "reorder_point_pct",
        default=prompt.DEFAULT_REORDER_POINT_PCT,
        help="Una farmacia (o la matriz, para su propia venta) entra en el reabastecimiento y en la "
             "necesidad de compra cuando su stock, más lo pendiente y lo que viene en camino, baja a "
             "este % de la venta de las semanas de cobertura. 100 = apenas le falte algo. De 1 a 100.")
    ai_purchase_safety_margin_pct = fields.Integer(
        "Margen de seguridad (%)", config_parameter=PARAM_PREFIX + "safety_margin_pct",
        default=prompt.DEFAULT_SAFETY_MARGIN_PCT,
        help="Al reponer se pide hasta la venta de las semanas de cobertura más este %, para no "
             "quedarse corto en una semana alta. 0 = sin margen. De 0 a 200.")
    ai_purchase_laboratory_field = fields.Char(
        "Campo de laboratorio", config_parameter=PARAM_PREFIX + "laboratory_field",
        help="Nombre técnico del campo del producto con el laboratorio (p. ej. el del módulo de "
             "laboratorios de Dromax). Vacío: se toma del texto entre corchetes al final del "
             "nombre o, si no hay, del que informa la droguería.")
    ai_purchase_matrix_company_id = fields.Many2one(
        "res.company", "Sucursal matriz", config_parameter=PARAM_PREFIX + "matrix_company_id",
        help="Sucursal que recibe todas las compras y reabastece a las farmacias con traslados "
             "(Agente IA → Reabastecimiento). Vacío: la que tenga DROMAX en el nombre.")

    ai_purchase_auto_rfq = fields.Boolean(
        "Borradores de compra automáticos", config_parameter=PARAM_PREFIX + "auto_rfq",
        help="El lunes, al calcular la propuesta semanal, crea también las órdenes de compra en "
             "borrador (una por droguería) y cancela las de semanas anteriores que nadie confirmó.")
    ai_purchase_responsible_id = fields.Many2one(
        "res.users", "Responsable de compras", config_parameter=PARAM_PREFIX + "purchase_responsible_id",
        help="Recibe la actividad para revisar la compra de la semana y queda como comprador de las "
             "órdenes creadas por la tarea programada.")
    ai_purchase_auto_transfers = fields.Boolean(
        "Borradores de traslado automáticos", config_parameter=PARAM_PREFIX + "auto_transfers",
        help="Cada día crea los borradores de traslado de la matriz a las farmacias que llegaron a su "
             "punto de pedido (Agente IA → Reabastecimiento).")
    ai_purchase_transfer_responsible_id = fields.Many2one(
        "res.users", "Responsable de despacho", config_parameter=PARAM_PREFIX + "transfer_responsible_id",
        help="Recibe una actividad en cada borrador de traslado para generar el traslado definitivo.")

    def set_values(self):
        super().set_values()
        # Odoo guarda un entero 0 como vacío (y se volvería al valor por defecto):
        # el margen 0 es válido, así que se guarda explícito.
        if not self.ai_purchase_safety_margin_pct:
            self.env["ir.config_parameter"].sudo().set_param(PARAM_PREFIX + "safety_margin_pct", "0")

    def action_ai_run_weekly_purchase(self):
        """Ejecuta ahora la tarea del lunes (lo mismo que hará sola) y abre la propuesta."""
        self.ensure_one()
        self.execute()   # guarda lo que se haya cambiado en la pantalla antes de ejecutar
        proposal = self.env["ai.purchase.proposal"].sudo()._cron_generate()
        return {"type": "ir.actions.act_window", "res_model": "ai.purchase.proposal",
                "res_id": proposal.id, "view_mode": "form", "target": "current"}

    def action_ai_run_replenishment(self):
        """Ejecuta ahora la tarea diaria de reabastecimiento y abre los borradores creados."""
        self.ensure_one()
        self.execute()
        if not self.ai_purchase_auto_transfers:
            raise UserError(_("Activa «Borradores de traslado automáticos» para ejecutarla."))
        pickings = self.env["ai.v.reabastecimiento"].sudo()._cron_generate_transfers()
        if not pickings:
            return {"type": "ir.actions.client", "tag": "display_notification", "params": {
                "title": _("Reabastecimiento"), "type": "info", "sticky": False,
                "message": _("No se creó ningún borrador: ninguna farmacia llegó a su punto de pedido "
                             "con mercancía disponible en la matriz (o ya está en camino).")}}
        return {"type": "ir.actions.act_window", "name": _("Borradores de traslado"),
                "res_model": "stock.picking", "view_mode": "tree,form",
                "domain": [("id", "in", pickings.ids)], "context": {"create": False}}

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
