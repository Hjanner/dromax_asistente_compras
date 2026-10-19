# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services import presets
from ..services.agent import run_agent

MAX_USER_TEXT = 4000
LEVELS = [("basic", "Básico"), ("medium", "Medio"), ("advanced", "Avanzado")]


class AiPurchaseChatSession(models.Model):
    """Una conversación del comprador con el agente. Cada uno ve solo las suyas."""
    _name = "ai.purchase.chat.session"
    _description = "Conversación con el Asistente de Compras IA"
    _order = "last_message_date desc, id desc"

    name = fields.Char("Título", default=lambda self: _("Nueva conversación"), required=True)
    user_id = fields.Many2one("res.users", string="Usuario", required=True, index=True,
                              default=lambda self: self.env.uid, ondelete="cascade")
    message_ids = fields.One2many("ai.purchase.chat.message", "session_id", string="Mensajes")
    last_message_date = fields.Datetime("Último mensaje", default=fields.Datetime.now, index=True)
    total_cost = fields.Float("Costo (USD)", digits=(12, 6), compute="_compute_total_cost")

    @api.depends("message_ids.cost")
    def _compute_total_cost(self):
        for session in self:
            session.total_cost = sum(session.message_ids.mapped("cost"))

    # --- API del chat (llamada desde el cliente web con orm.call) ------------
    @api.model
    def chat_bootstrap(self):
        """Datos iniciales de la pantalla: conversaciones y preguntas predefinidas."""
        sessions = self.search([("user_id", "=", self.env.uid)], limit=50)
        return {
            "sessions": [s._chat_session_dict() for s in sessions],
            "presets": presets.public_presets(),
            "levels": [{"key": k, "label": v} for k, v in LEVELS],
        }

    @api.model
    def chat_new_session(self):
        return self.create({})._chat_session_dict()

    def chat_messages(self):
        self.ensure_one()
        return [m._chat_message_dict() for m in self.message_ids.sorted("id")]

    def chat_send(self, text=None, level=None, preset=None):
        """Envía un mensaje (texto libre o pregunta predefinida) y devuelve el
        mensaje del comprador y la respuesta del agente."""
        self.ensure_one()
        preset_def = presets.get_preset(preset) if preset else None
        if preset and not preset_def:
            raise UserError(_("Pregunta predefinida desconocida."))
        if preset_def:
            text, level, task = preset_def["prompt"], preset_def["level"], preset_def["task"]
        else:
            text = (text or "").strip()
            task = False
            if not text:
                raise UserError(_("Escribe una pregunta."))
            if len(text) > MAX_USER_TEXT:
                raise UserError(_("La pregunta es demasiado larga (máximo %s caracteres).", MAX_USER_TEXT))
            if level not in dict(LEVELS):
                level = "medium"

        history = [{"role": m.role, "content": m.content}
                   for m in self.message_ids.sorted("id") if m.state == "ok"]
        Message = self.env["ai.purchase.chat.message"]
        user_msg = Message.create({
            "session_id": self.id, "role": "user", "content": text,
            "preset_key": preset_def["key"] if preset_def else False,
            "preset_label": preset_def["label"] if preset_def else False,
        })
        if self.name == _("Nueva conversación"):
            title = preset_def["label"] if preset_def else text
            self.name = title if len(title) <= 60 else title[:57] + "…"

        result = run_agent(self.env, text, history=history, level=level, task=task)
        answer = Message.create({
            "session_id": self.id,
            "role": "assistant",
            "content": result["content"],
            "state": result["state"],
            "level": result["level"],
            "model_used": result["model"],
            "tool_trace": result["tool_trace"],
            "prompt_tokens": result["prompt_tokens"],
            "completion_tokens": result["completion_tokens"],
            "cost": result["cost"],
            "duration_ms": result["duration_ms"],
        })
        self.last_message_date = fields.Datetime.now()
        return {
            "session": self._chat_session_dict(),
            "messages": [user_msg._chat_message_dict(), answer._chat_message_dict()],
        }

    def chat_delete(self):
        self.unlink()
        return True

    def _chat_session_dict(self):
        self.ensure_one()
        return {"id": self.id, "name": self.name,
                "last_message_date": fields.Datetime.to_string(self.last_message_date)}


class AiPurchaseChatMessage(models.Model):
    _name = "ai.purchase.chat.message"
    _description = "Mensaje del Asistente de Compras IA"
    _order = "id"

    session_id = fields.Many2one("ai.purchase.chat.session", required=True,
                                 ondelete="cascade", index=True)
    user_id = fields.Many2one(related="session_id.user_id", store=True, index=True)
    role = fields.Selection([("user", "Comprador"), ("assistant", "Asistente")],
                            required=True)
    content = fields.Text("Contenido")
    preset_key = fields.Char("Pregunta predefinida")
    preset_label = fields.Char("Texto del botón")
    state = fields.Selection([("ok", "Correcta"), ("error", "Error")], default="ok", required=True)
    level = fields.Selection(LEVELS, string="Nivel")
    model_used = fields.Char("Modelo")
    tool_trace = fields.Json("Consultas realizadas")
    prompt_tokens = fields.Integer("Tokens de entrada")
    completion_tokens = fields.Integer("Tokens de salida")
    cost = fields.Float("Costo (USD)", digits=(12, 6))
    duration_ms = fields.Integer("Duración (ms)")

    def _chat_message_dict(self):
        self.ensure_one()
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content or "",
            "preset_label": self.preset_label or False,
            "state": self.state,
            "level": self.level or False,
            "model": self.model_used or False,
            "tool_trace": self.tool_trace or [],
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost": self.cost,
            "duration_ms": self.duration_ms,
        }
