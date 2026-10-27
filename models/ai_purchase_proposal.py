# -*- coding: utf-8 -*-
"""Propuesta de compra semanal.

Una propuesta por semana con todas las farmacias: una línea por producto y
farmacia de ai_v_faltantes, guardada como foto del momento (demanda, stock,
pendiente, droguería y precio). Se organiza por laboratorio en las líneas, no
en propuestas separadas: con datos reales salen cerca de 200 laboratorios con
una mediana de 3 líneas cada uno.

El comprador solo cambia la cantidad final, la droguería y el motivo. La
cantidad y la droguería sugeridas quedan fijas, para medir cuánto corrige el
comprador al agente.

Laboratorio de cada línea, en este orden:
1. el campo del producto indicado en Ajustes (laboratory_field), para el
   módulo de laboratorios de Dromax cuando esté instalado;
2. el texto entre corchetes al final del nombre ("... [LETI]"), como vienen
   los nombres del inventario de Dromax;
3. el laboratorio que informa la droguería sugerida;
4. "SIN LABORATORIO".
"""
import logging
import re
from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services.config import get_int_setting, get_setting
from ..services.prompt import DEFAULT_COVERAGE_WEEKS, DEFAULT_WINDOW_WEEKS
from .ai_barcode_mismatch import GROUP_XMLID

_logger = logging.getLogger(__name__)

STATES = [
    ("ready", "Lista"),
    ("in_review", "En revisión"),
    ("done", "Convertida en borradores"),
    ("discarded", "Descartada"),
]
CLOSED_STATES = ("done", "discarded")
NO_LAB = "SIN LABORATORIO"
LAB_IN_NAME = re.compile(r"\[([^\[\]]+)\][^\[]*$")
# Un precio de droguería por encima de este múltiplo del costo puede ser otro empaque.
PRESENTATION_RATIO = 3


def _num(value):
    """Número corto para el motivo: 10 -> "10", 0.25 -> "0,25"."""
    return ("%.2f" % value).rstrip("0").rstrip(".").replace(".", ",")


class AiPurchaseProposal(models.Model):
    _name = "ai.purchase.proposal"
    _description = "Propuesta de compra"
    _inherit = ["mail.thread"]
    _order = "date_computed desc, id desc"

    name = fields.Char("Propuesta", required=True, readonly=True)
    mode = fields.Selection([("daily", "Semanal"), ("dynamic", "Con contexto externo")], "Modo",
                            default="daily", required=True, readonly=True)
    state = fields.Selection(STATES, "Estado", default="ready", required=True, readonly=True, tracking=True)
    date_computed = fields.Datetime("Calculada el", readonly=True, default=fields.Datetime.now)
    offer_date = fields.Datetime("Oferta cargada el", readonly=True,
                                 help="Carga más reciente de la oferta de las droguerías al calcular.")
    window_weeks = fields.Integer("Semanas de historial", readonly=True)
    coverage_weeks = fields.Integer("Semanas de cobertura", readonly=True)
    line_ids = fields.One2many("ai.purchase.proposal.line", "proposal_id", "Líneas")
    purchase_order_ids = fields.Many2many("purchase.order", string="Órdenes generadas", readonly=True)
    line_count = fields.Integer("Líneas", compute="_compute_totals", store=True)
    lines_changed = fields.Integer("Líneas ajustadas", compute="_compute_totals", store=True,
                                   help="Líneas en las que el comprador cambió la cantidad o la droguería.")
    adjustment_pct = fields.Float("Ajuste del comprador (%)", compute="_compute_totals", store=True,
                                  help="Suma de las diferencias entre cantidad final y sugerida, "
                                       "sobre el total sugerido.")
    amount_suggested = fields.Float("Monto sugerido", compute="_compute_totals", store=True)
    amount_total = fields.Float("Monto final", compute="_compute_totals", store=True)

    @api.depends("line_ids.cantidad_sugerida", "line_ids.cantidad_final", "line_ids.ajustada",
                 "line_ids.monto", "line_ids.monto_sugerido")
    def _compute_totals(self):
        for proposal in self:
            lines = proposal.line_ids
            suggested = sum(lines.mapped("cantidad_sugerida"))
            diff = sum(abs(l.cantidad_final - l.cantidad_sugerida) for l in lines)
            proposal.line_count = len(lines)
            proposal.lines_changed = len(lines.filtered("ajustada"))
            proposal.adjustment_pct = 100.0 * diff / suggested if suggested else 0.0
            proposal.amount_suggested = sum(lines.mapped("monto_sugerido"))
            proposal.amount_total = sum(lines.mapped("monto"))

    # -- generación -----------------------------------------------------------

    def _check_buyer(self):
        if not self.env.user.has_group(GROUP_XMLID):
            raise AccessError(_("Solo los usuarios del Asistente de Compras IA pueden calcular propuestas."))

    def action_generate(self):
        """Botón de la lista: calcula una propuesta nueva y la abre.

        Sin @api.model: el botón de la cabecera de la lista lo llama con los
        registros seleccionados (o ninguno), que aquí no se usan.
        """
        self._check_buyer()
        # La propuesta es de todas las farmacias, aunque el usuario solo tenga acceso a algunas.
        proposal = self.sudo()._generate()
        proposal.message_post(body=_("Calculada a pedido de %s.", self.env.user.name))
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": proposal.id,
            "view_mode": "form",
        }

    @api.model
    def _cron_generate(self):
        self._generate()

    @api.model
    def _generate(self, mode="daily"):
        """Crea la propuesta a partir de ai_v_faltantes y descarta las anteriores
        del mismo modo que seguían abiertas."""
        self.env.flush_all()
        cr = self.env.cr
        cr.execute("SELECT * FROM ai_v_faltantes ORDER BY company_id, product_id")
        rows = cr.dictfetchall()
        cr.execute("SELECT max(fecha_carga) FROM ai_v_oferta")
        offer_date = cr.fetchone()[0]
        labs = self._laboratories({r["product_id"] for r in rows})

        today = fields.Date.context_today(self)
        monday = today - timedelta(days=today.weekday())
        previous = self.search([("mode", "=", mode), ("state", "not in", CLOSED_STATES)])
        proposal = self.create({
            "name": _("Compra de la semana del %s", monday.strftime("%d/%m/%Y")),
            "mode": mode,
            "offer_date": offer_date,
            "window_weeks": get_int_setting(self.env, "rotation_window_weeks", DEFAULT_WINDOW_WEEKS, 104),
            "coverage_weeks": get_int_setting(self.env, "coverage_weeks", DEFAULT_COVERAGE_WEEKS, 52),
            "line_ids": [Command.create(self.env["ai.purchase.proposal.line"]._values_from_shortage(
                row, labs.get(row["product_id"]))) for row in rows],
        })
        if previous:
            previous.write({"state": "discarded"})
            for old in previous:
                old.message_post(body=_("Reemplazada por %s.", proposal.name))
        _logger.info("Propuesta %s: %s líneas.", proposal.name, len(rows))
        return proposal

    @api.model
    def _laboratories(self, product_ids):
        """Laboratorio de cada producto según Ajustes o el nombre; None si no se sabe."""
        field = get_setting(self.env, "laboratory_field")
        products = self.env["product.product"].with_context(active_test=False).browse(product_ids)
        if field and field not in products._fields:
            _logger.warning("El campo de laboratorio %s no existe en product.product; se ignora.", field)
            field = None
        labs = {}
        for product in products:
            lab = None
            if field:
                value = product[field]
                if isinstance(value, models.BaseModel):
                    lab = ", ".join(value.mapped("display_name"))
                elif value:
                    lab = str(value)
            if not lab:
                match = LAB_IN_NAME.search(product.name or "")
                lab = match and match.group(1)
            labs[product.id] = lab.strip().upper() if lab and lab.strip() else None
        return labs

    # -- acciones ---------------------------------------------------------------

    def action_discard(self):
        if any(p.state == "done" for p in self):
            raise UserError(_("Una propuesta ya convertida en borradores no se puede descartar."))
        self.write({"state": "discarded"})

    def action_open_lines(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "dromax_asistente_compras.action_ai_purchase_proposal_line")
        action["domain"] = [("proposal_id", "=", self.id)]
        action["context"] = {"search_default_group_laboratorio": 1, "default_proposal_id": self.id}
        action["name"] = self.name
        return action


class AiPurchaseProposalLine(models.Model):
    _name = "ai.purchase.proposal.line"
    _description = "Línea de propuesta de compra"
    _order = "proposal_id, laboratorio, company_id, product_id"
    _rec_name = "product_id"

    proposal_id = fields.Many2one("ai.purchase.proposal", "Propuesta", required=True, ondelete="cascade",
                                  index=True, readonly=True)
    state = fields.Selection(related="proposal_id.state")
    company_id = fields.Many2one("res.company", "Farmacia", required=True, readonly=True, index=True)
    product_id = fields.Many2one("product.product", "Producto", required=True, readonly=True, index=True)
    laboratorio = fields.Char("Laboratorio", readonly=True)
    demanda = fields.Float("Demanda semanal", readonly=True, help="Demanda depurada de la rotación.")
    stock = fields.Float("Stock", readonly=True)
    pendiente = fields.Float("Pendiente de recibir", readonly=True)
    en_solicitud = fields.Float("En solicitudes sin confirmar", readonly=True)
    cobertura_actual = fields.Float("Cobertura actual (semanas)", readonly=True)
    excedente_otras = fields.Float("Excedente en otras farmacias", readonly=True)
    farmacias_con_excedente = fields.Char("Farmacias con excedente", readonly=True)
    costo = fields.Float("Costo actual", readonly=True)
    cantidad_sugerida = fields.Integer("Cantidad sugerida", readonly=True)
    cantidad_final = fields.Integer("Cantidad final", help="Lo que se va a pedir; 0 = no pedir.")
    drogueria_sugerida_id = fields.Many2one("res.partner", "Droguería sugerida", readonly=True)
    precio_sugerido = fields.Float("Precio sugerido", readonly=True)
    drogueria_id = fields.Many2one("res.partner", "Droguería",
                                   domain="[('id', 'in', drogueria_opciones_ids)]")
    drogueria_opciones_ids = fields.Many2many("res.partner", compute="_compute_drogueria_opciones",
                                              string="Droguerías con oferta")
    precio = fields.Float("Precio final", readonly=True, help="Precio con descuentos de la droguería elegida.")
    descuento = fields.Float("% Descuento", readonly=True)
    stock_drogueria = fields.Integer("Stock en la droguería", readonly=True)
    fecha_vencimiento = fields.Date("Vencimiento", readonly=True)
    revisar_presentacion = fields.Boolean(
        "Revisar presentación", readonly=True,
        help="El precio de la droguería pasa del triple del costo: puede ser otro empaque con el "
             "mismo código de barras.")
    motivo = fields.Text("Motivo")
    monto = fields.Float("Monto", compute="_compute_montos", store=True)
    monto_sugerido = fields.Float("Monto sugerido", compute="_compute_montos", store=True)
    ajustada = fields.Boolean("Ajustada", compute="_compute_montos", store=True,
                              help="El comprador cambió la cantidad o la droguería.")

    @api.depends("cantidad_final", "precio", "cantidad_sugerida", "precio_sugerido",
                 "drogueria_id", "drogueria_sugerida_id")
    def _compute_montos(self):
        for line in self:
            line.monto = line.cantidad_final * line.precio
            line.monto_sugerido = line.cantidad_sugerida * line.precio_sugerido
            line.ajustada = (line.cantidad_final != line.cantidad_sugerida
                             or line.drogueria_id != line.drogueria_sugerida_id)

    @api.depends("product_id")
    def _compute_drogueria_opciones(self):
        product_ids = self.product_id.ids
        options = {}
        if product_ids:
            self.env.cr.execute("""
                SELECT product_id, array_agg(DISTINCT partner_id)
                  FROM ai_v_oferta
                 WHERE disponible AND product_id = ANY(%s)
                 GROUP BY product_id
            """, (product_ids,))
            options = dict(self.env.cr.fetchall())
        for line in self:
            line.drogueria_opciones_ids = [Command.set(options.get(line.product_id.id, []))]

    @api.model
    def _values_from_shortage(self, row, lab):
        """Valores de una línea a partir de una fila de ai_v_faltantes."""
        reason = _("Demanda %(d)s/sem × %(c)s sem − stock %(s)s − pendiente %(p)s = %(f)s.",
                   d=_num(row["demanda_semanal"]), c=row["semanas_cobertura"], s=_num(row["stock"]),
                   p=_num(row["pendiente"]), f=_num(row["faltante"]))
        if row["en_solicitud"]:
            reason += " " + _("Ya hay %s en solicitudes sin confirmar.", _num(row["en_solicitud"]))
        if row["farmacias_con_excedente"]:
            reason += " " + _("Excedente en otras farmacias: %s.", row["farmacias_con_excedente"])
        if not row["drogueria_id"]:
            reason += " " + _("Ninguna droguería lo ofrece.")
        elif row["revisar_presentacion"]:
            reason += " " + _("Revisar presentación: precio %(p)s frente a un costo de %(c)s.",
                              p=_num(row["precio"]), c=_num(row["costo"]))
        elif not row["stock_drogueria_suficiente"]:
            reason += " " + _("La droguería solo tiene %s.", row["stock_drogueria"])
        return {
            "company_id": row["company_id"],
            "product_id": row["product_id"],
            "laboratorio": lab or (row["laboratorio"] or "").strip().upper() or NO_LAB,
            "demanda": row["demanda_semanal"],
            "stock": row["stock"],
            "pendiente": row["pendiente"],
            "en_solicitud": row["en_solicitud"],
            "cobertura_actual": row["cobertura_actual"],
            "excedente_otras": row["excedente_otras"],
            "farmacias_con_excedente": row["farmacias_con_excedente"],
            "costo": row["costo"] or 0.0,
            "cantidad_sugerida": row["cantidad_sugerida"],
            "cantidad_final": row["cantidad_sugerida"],
            "drogueria_sugerida_id": row["drogueria_id"],
            "precio_sugerido": row["precio"] or 0.0,
            "drogueria_id": row["drogueria_id"],
            "precio": row["precio"] or 0.0,
            "descuento": row["descuento"] or 0.0,
            "stock_drogueria": row["stock_drogueria"] or 0,
            "fecha_vencimiento": row["fecha_vencimiento"],
            "revisar_presentacion": row["revisar_presentacion"],
            "motivo": reason,
        }

    def _offer_values(self, partner):
        """Precio y datos de la mejor oferta disponible de `partner` para el producto."""
        self.ensure_one()
        if not partner:
            return {"precio": 0.0, "descuento": 0.0, "stock_drogueria": 0, "fecha_vencimiento": False,
                    "revisar_presentacion": False}
        self.env.cr.execute("""
            SELECT precio_final, descuento_pct, stock_drogueria, fecha_vencimiento
              FROM ai_v_oferta
             WHERE disponible AND product_id = %s AND partner_id = %s
             ORDER BY precio_final, id
             LIMIT 1
        """, (self.product_id.id, partner.id))
        row = self.env.cr.fetchone()
        if not row:
            raise UserError(_("%(drogueria)s no tiene oferta disponible de %(producto)s.",
                              drogueria=partner.display_name, producto=self.product_id.display_name))
        price, discount, stock, expiry = row
        return {
            "precio": price,
            "descuento": discount or 0.0,
            "stock_drogueria": stock or 0,
            "fecha_vencimiento": expiry,
            "revisar_presentacion": bool(self.costo and price > PRESENTATION_RATIO * self.costo),
        }

    def write(self, vals):
        if any(line.state in CLOSED_STATES for line in self):
            raise UserError(_("La propuesta ya está cerrada: no se puede modificar."))
        if "drogueria_id" in vals:
            partner = self.env["res.partner"].browse(vals["drogueria_id"])
            for line in self:
                super(AiPurchaseProposalLine, line).write(dict(vals, **line._offer_values(partner)))
            result = True
        else:
            result = super().write(vals)
        if {"cantidad_final", "drogueria_id", "motivo"} & set(vals):
            self.proposal_id.filtered(lambda p: p.state == "ready").write({"state": "in_review"})
        return result

    @api.constrains("cantidad_final")
    def _check_cantidad_final(self):
        if any(line.cantidad_final < 0 for line in self):
            raise ValidationError(_("La cantidad final no puede ser negativa."))
