# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AiUsageLog(models.Model):
    """Una fila por llamada a OpenRouter, buena o fallida.

    Sirve para fijar el presupuesto mensual con datos reales y para ver quién
    consume y con qué nivel. Solo la escribe el cliente (con sudo); en la
    interfaz es de solo lectura.
    """
    _name = "ai.usage.log"
    _description = "Consumo de IA (OpenRouter)"
    _order = "create_date desc, id desc"
    _rec_name = "model"

    user_id = fields.Many2one("res.users", string="Usuario", required=True, index=True,
                              ondelete="restrict", default=lambda self: self.env.uid)
    level = fields.Selection([
        ("basic", "Básico"),
        ("medium", "Medio"),
        ("advanced", "Avanzado"),
    ], string="Nivel", required=True, index=True)
    model = fields.Char("Modelo pedido", required=True,
                        help="Identificador configurado en Ajustes para ese nivel.")
    model_used = fields.Char("Modelo que respondió",
                             help="El que informa OpenRouter; puede llevar la versión exacta.")
    provider = fields.Char("Proveedor")
    task = fields.Char("Tarea", index=True,
                       help="Para qué se llamó al modelo (p. ej. purchase_proposal).")
    state = fields.Selection([("ok", "Correcta"), ("error", "Error")],
                             string="Estado", required=True, default="ok")
    error_message = fields.Text("Error")
    prompt_tokens = fields.Integer("Tokens de entrada")
    completion_tokens = fields.Integer("Tokens de salida")
    cached_tokens = fields.Integer("Tokens en caché", help="Parte de la entrada leída de caché.")
    reasoning_tokens = fields.Integer("Tokens de razonamiento", help="Parte de la salida.")
    total_tokens = fields.Integer("Tokens totales", compute="_compute_total_tokens", store=True)
    cost = fields.Float("Costo (USD)", digits=(12, 6),
                        help="Costo que informa OpenRouter para la llamada.")
    duration_ms = fields.Integer("Duración (ms)")
    generation_id = fields.Char("ID de generación", index=True,
                                help="Permite consultar el detalle en OpenRouter.")

    @api.depends("prompt_tokens", "completion_tokens")
    def _compute_total_tokens(self):
        for rec in self:
            rec.total_tokens = rec.prompt_tokens + rec.completion_tokens
