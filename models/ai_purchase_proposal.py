# -*- coding: utf-8 -*-
"""Propuesta de compra semanal.

Una propuesta por semana: toda compra la hace la sucursal matriz (Dromax)
para toda la red y después reparte a las farmacias con traslados (Agente IA
→ Reabastecimiento). Una línea por producto de ai_v_faltantes, guardada como
foto del momento (falta en cada farmacia, venta propia de la matriz, stock y
compras pendientes de la matriz, droguería y precio). Se organiza por laboratorio en las líneas, no
en propuestas separadas: con datos reales salen cerca de 200 laboratorios con
una mediana de 3 líneas cada uno.

El comprador solo cambia la cantidad final, la droguería y el motivo. La
cantidad y la droguería sugeridas quedan fijas, para medir cuánto corrige el
comprador al agente.

Borradores (action_create_rfq, botón del comprador, nunca del modelo de IA):
una orden de compra en borrador por droguería, a nombre de la matriz, con las
líneas que tienen cantidad final y droguería. Con el ajuste «Borradores de
compra automáticos», la tarea programada del lunes los crea sola al calcular
la propuesta (_cron_generate): antes cancela los borradores de propuestas
anteriores que nadie confirmó, deja fuera lo que ya está en otras solicitudes
sin confirmar, y le deja al responsable de compras una actividad para
revisarlos. Se puede hacer por partes (p. ej.
un laboratorio el lunes y otro el martes): lo nuevo se suma al borrador que
esta misma propuesta ya creó para esa droguería; los borradores hechos a
mano no se tocan. Las líneas marcadas para revisar la presentación no entran
hasta que el comprador cambie su cantidad o su droguería. Cuando no queda
nada por pedir, la propuesta pasa a "Convertida en borradores".

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
from datetime import datetime, time, timedelta

import pytz
from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services import automation
from ..services.config import get_int_setting, get_setting
from ..services.prompt import DEFAULT_COVERAGE_WEEKS, DEFAULT_WINDOW_WEEKS
from .ai_barcode_mismatch import GROUP_XMLID
from .ai_v_faltantes import needs_presentation_review

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


def _num(value):
    """Número corto para el motivo: 10 -> "10", 0.25 -> "0,25"."""
    return ("%.2f" % value).rstrip("0").rstrip(".").replace(".", ",")


class AiPurchaseProposal(models.Model):
    _name = "ai.purchase.proposal"
    _description = "Propuesta de compra"
    _inherit = ["mail.thread", "mail.activity.mixin"]
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
        Line = self.env["ai.purchase.proposal.line"].sudo()
        for proposal in self:
            # Todas las líneas, no solo las de las farmacias del usuario: line_ids puede venir
            # en caché leído por el usuario, ya filtrado por la regla de farmacias.
            lines = Line.search([("proposal_id", "=", proposal._origin.id)]) if proposal._origin.id \
                else proposal.line_ids
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
            raise AccessError(_("Solo los usuarios del Agente IA pueden calcular propuestas."))

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
        """Tarea programada del lunes: calcula la propuesta y, con el ajuste
        activo, crea los borradores de compra; avisa al responsable."""
        auto = automation.is_enabled(self.env, automation.AUTO_RFQ)
        cancelled = self._cancel_stale_drafts() if auto else self.env["purchase.order"]
        proposal = self._generate()
        orders, skipped = proposal._auto_create_rfqs() if auto else (self.env["purchase.order"], {})
        proposal._notify_purchase_responsible(auto, orders, skipped, cancelled)
        return proposal

    @api.model
    def _cancel_stale_drafts(self):
        """Cancela los borradores de propuestas anteriores que siguen sin confirmar.

        Los reemplaza la compra nueva (con la oferta y el stock de hoy): si se
        quedaran, la cantidad se pediría dos veces. Solo los de las propuestas
        (los hechos a mano en Compras no se tocan) y solo en borrador (una
        solicitud ya enviada a la droguería sigue su curso).
        """
        orders = self.search([]).purchase_order_ids.filtered(lambda o: o.state == "draft")
        if orders:
            orders.button_cancel()
            for order in orders:
                order.message_post(body=_(
                    "Cancelada por el Agente IA: la reemplazan los borradores de la compra de esta semana."))
            _logger.info("Borradores de compra anteriores cancelados: %s.", ", ".join(orders.mapped("name")))
        return orders

    def _auto_create_rfqs(self):
        """Borradores de todas las líneas por pedir, sin revisar permisos (tarea programada).

        Quedan fuera, y se informan: las líneas sin droguería, las marcadas
        para revisar la presentación y las de productos que ya están en otras
        solicitudes sin confirmar (se pedirían dos veces).
        """
        self.ensure_one()
        lines = self.env["ai.purchase.proposal.line"].sudo().search([
            ("proposal_id", "=", self.id), ("cantidad_final", ">", 0), ("purchase_line_id", "=", False)])
        skipped = {
            "sin_drogueria": lines.filtered(lambda l: not l.drogueria_id),
            "revisar": lines.filtered(lambda l: l.drogueria_id and l.revisar_presentacion),
            "en_solicitud": lines.filtered(
                lambda l: l.drogueria_id and not l.revisar_presentacion and l.en_solicitud > 0),
        }
        for group in skipped.values():
            lines -= group
        buyer = automation.responsible(self.env, automation.PURCHASE_RESPONSIBLE)
        orders = lines._create_orders(buyer, _("el Agente IA (tarea programada)")) if lines \
            else self.env["purchase.order"]
        return orders, skipped

    def _notify_purchase_responsible(self, auto, orders, skipped, cancelled):
        """Actividad «Por hacer» en la propuesta para el responsable de compras."""
        self.ensure_one()
        user = automation.responsible(self.env, automation.PURCHASE_RESPONSIBLE)
        if not user:
            return
        note = Markup()
        if auto:
            note += automation.html_list(
                _("Borradores de compra creados: %s.", len(orders)) if orders
                else _("No se creó ningún borrador de compra."),
                [_("%(order)s · %(partner)s · %(lines)s líneas · %(amount)s",
                   order=o.name, partner=o.partner_id.name, lines=len(o.order_line),
                   amount=o.currency_id.format(o.amount_untaxed)) for o in orders])
            labels = {
                "sin_drogueria": _("Sin droguería con oferta (comprar por otra vía): %s"),
                "revisar": _("Para revisar la presentación (el precio pasa del triple del costo): %s"),
                "en_solicitud": _("Ya están en solicitudes sin confirmar: %s"),
            }
            missing = [labels[key] % len(group) for key, group in skipped.items() if group]
            if missing:
                note += automation.html_list(_("Quedaron fuera, para decidir en la propuesta:"), missing)
            if cancelled:
                names = cancelled.mapped("name")
                note += automation.html_list(
                    _("Se cancelaron %s borradores de semanas anteriores sin confirmar:", len(names)),
                    names[:10] + ([_("y %s más", len(names) - 10)] if len(names) > 10 else []))
        else:
            note += automation.html_list(
                _("La propuesta tiene %s líneas. Revísala y pulsa «Generar borradores».", self.line_count), [])
        if not self.offer_date or self.offer_date < fields.Datetime.now() - timedelta(days=1):
            note += automation.html_list(_(
                "Atención: la oferta de las droguerías es del %s. Descárgala antes de confirmar los pedidos.",
                fields.Datetime.to_string(self.offer_date) if self.offer_date else _("(sin oferta)")), [])
        self.activity_schedule(
            "mail.mail_activity_data_todo", user_id=user.id,
            summary=_("Revisar la compra de la semana"), note=note)

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

    @api.model
    def _current(self):
        """Propuesta semanal vigente para el chat: la más reciente que no se descartó."""
        return self.search([("mode", "=", "daily"), ("state", "!=", "discarded")], limit=1)

    @api.model
    def _week_start(self):
        """Lunes de esta semana a las 00:00 en la zona del usuario, en UTC sin zona."""
        today = fields.Date.context_today(self)
        monday = datetime.combine(today - timedelta(days=today.weekday()), time.min)
        tz = pytz.timezone(self.env.context.get("tz") or self.env.user.tz or "UTC")
        return tz.localize(monday).astimezone(pytz.utc).replace(tzinfo=None)

    @api.model
    def _ensure_week_proposal(self):
        """La propuesta de esta semana; si el cron no la calculó, la calcula ahora."""
        self._check_buyer()
        current = self._current()
        if current and current.date_computed >= self._week_start():
            return current
        proposal = self.sudo()._generate().sudo(False)
        proposal.message_post(body=_("Calculada desde el chat a pedido de %s.", self.env.user.name))
        return proposal

    # -- acciones ---------------------------------------------------------------

    def action_create_rfq(self):
        self.ensure_one()
        # search y no line_ids: solo las líneas de las farmacias del usuario (regla), sin caché de otro usuario.
        return self.env["ai.purchase.proposal.line"].search([("proposal_id", "=", self.id)]).action_create_rfq()

    def _pending_lines(self):
        """Líneas por pedir de todas las farmacias, aunque el usuario no las vea."""
        return self.env["ai.purchase.proposal.line"].sudo().search([
            ("proposal_id", "=", self.id), ("cantidad_final", ">", 0),
            ("drogueria_id", "!=", False), ("purchase_line_id", "=", False)])

    def action_discard(self):
        if any(p.state == "done" for p in self):
            raise UserError(_("Una propuesta ya convertida en borradores no se puede descartar."))
        self.write({"state": "discarded"})

    def action_open_lines(self):
        """Las líneas de la propuesta, agrupadas por laboratorio."""
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
    company_id = fields.Many2one("res.company", "Compra", required=True, readonly=True, index=True,
                                 help="Sucursal matriz, que compra para toda la red.")
    product_id = fields.Many2one("product.product", "Producto", required=True, readonly=True, index=True)
    laboratorio = fields.Char("Laboratorio", readonly=True)
    demanda = fields.Float("Venta semanal de la red", readonly=True,
                           help="Demanda depurada de las farmacias más la de la matriz.")
    demanda_propia = fields.Float("Venta semanal de la matriz", readonly=True)
    faltas_farmacias = fields.Integer("Falta en las farmacias", readonly=True)
    detalle_farmacias = fields.Char("Falta por farmacia", readonly=True,
                                    help="Cuánto le falta a cada farmacia, de mayor a menor.")
    stock = fields.Float("Stock libre en la matriz", readonly=True)
    pendiente = fields.Float("Compras pendientes", readonly=True)
    en_solicitud = fields.Float("En solicitudes sin confirmar", readonly=True)
    cobertura_actual = fields.Float("Cobertura actual (semanas)", readonly=True)
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
        help="El precio de la droguería pasa del triple del costo o no llega a un tercio (sin costo: "
             "las ofertas difieren más del triple): puede ser otro empaque con el mismo código de barras.")
    motivo = fields.Text("Motivo")
    purchase_line_id = fields.Many2one("purchase.order.line", "Línea de compra", readonly=True, copy=False,
                                       index="btree_not_null")
    purchase_order_id = fields.Many2one(related="purchase_line_id.order_id", string="Orden de compra")
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
        parts = []
        if row["faltas_farmacias"]:
            parts.append(_("farmacias %(f)s (%(d)s)", f=row["faltas_farmacias"], d=row["detalle_farmacias"]))
        if row["demanda_propia"]:
            parts.append(_("venta propia %(d)s/sem × %(c)s sem",
                           d=_num(row["demanda_propia"]), c=row["semanas_cobertura"]))
        reason = _("Falta: %(partes)s − stock %(s)s − pendiente %(p)s = %(f)s.",
                   partes=" + ".join(parts), s=_num(row["stock"]), p=_num(row["pendiente"]),
                   f=_num(row["faltante"]))
        if row["en_solicitud"]:
            reason += " " + _("Ya hay %s en solicitudes sin confirmar.", _num(row["en_solicitud"]))
        if not row["drogueria_id"]:
            reason += " " + _("Ninguna droguería lo ofrece.")
        elif row["revisar_presentacion"] and row["costo"]:
            reason += " " + _("Revisar presentación: precio %(p)s frente a un costo de %(c)s.",
                              p=_num(row["precio"]), c=_num(row["costo"]))
        elif row["revisar_presentacion"]:
            reason += " " + _("Revisar presentación: hay droguerías con más del triple del precio de otras.")
        elif not row["stock_drogueria_suficiente"]:
            reason += " " + _("La droguería solo tiene %s.", row["stock_drogueria"])
        return {
            "company_id": row["company_id"],
            "product_id": row["product_id"],
            "laboratorio": lab or (row["laboratorio"] or "").strip().upper() or NO_LAB,
            "demanda": row["demanda_semanal"],
            "demanda_propia": row["demanda_propia"],
            "faltas_farmacias": row["faltas_farmacias"],
            "detalle_farmacias": row["detalle_farmacias"],
            "stock": row["stock"],
            "pendiente": row["pendiente"],
            "en_solicitud": row["en_solicitud"],
            "cobertura_actual": row["cobertura_actual"],
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
            SELECT precio_final, descuento_pct, stock_drogueria, fecha_vencimiento, precio_min, precio_max
              FROM (SELECT o.*, min(precio_final) OVER () AS precio_min, max(precio_final) OVER () AS precio_max
                      FROM ai_v_oferta o
                     WHERE disponible AND product_id = %s) o
             WHERE partner_id = %s
             ORDER BY precio_final, id
             LIMIT 1
        """, (self.product_id.id, partner.id))
        row = self.env.cr.fetchone()
        if not row:
            raise UserError(_("%(drogueria)s no tiene oferta disponible de %(producto)s.",
                              drogueria=partner.display_name, producto=self.product_id.display_name))
        price, discount, stock, expiry, min_price, max_price = row
        return {
            "precio": price,
            "descuento": discount or 0.0,
            "stock_drogueria": stock or 0,
            "fecha_vencimiento": expiry,
            "revisar_presentacion": needs_presentation_review(price, self.costo, min_price, max_price),
        }

    def _stock_warning(self):
        return {"warning": {
            "title": _("Stock de la droguería"),
            "message": _("%(drogueria)s solo tiene %(stock)s de %(producto)s: la cantidad final quedó en %(stock)s.",
                         drogueria=self.drogueria_id.display_name, stock=self.stock_drogueria,
                         producto=self.product_id.display_name),
        }}

    @api.onchange("drogueria_id")
    def _onchange_drogueria_id(self):
        """Muestra el precio y el stock de la droguería elegida sin tener que guardar."""
        if not self.drogueria_id:
            return
        self.update(self._offer_values(self.drogueria_id))
        if self.cantidad_final > self.stock_drogueria:
            self.cantidad_final = self.stock_drogueria
            return self._stock_warning()

    @api.onchange("cantidad_final")
    def _onchange_cantidad_final(self):
        if self.drogueria_id and self.cantidad_final > self.stock_drogueria:
            self.cantidad_final = self.stock_drogueria
            return self._stock_warning()

    def _check_stock_drogueria(self):
        for line in self:
            if line.drogueria_id and line.cantidad_final > line.stock_drogueria:
                raise UserError(_(
                    "%(producto)s: %(drogueria)s solo tiene %(stock)s y la cantidad final es %(cantidad)s. "
                    "Baja la cantidad o elige otra droguería.",
                    producto=line.product_id.display_name, drogueria=line.drogueria_id.display_name,
                    stock=line.stock_drogueria, cantidad=line.cantidad_final))

    def write(self, vals):
        if any(line.state in CLOSED_STATES for line in self):
            raise UserError(_("La propuesta ya está cerrada: no se puede modificar."))
        if {"cantidad_final", "drogueria_id"} & set(vals) and self.filtered("purchase_line_id"):
            raise UserError(_("La línea ya está en una orden de compra: cámbiala en la orden."))
        if "drogueria_id" in vals:
            partner = self.env["res.partner"].browse(vals["drogueria_id"])
            for line in self:
                super(AiPurchaseProposalLine, line).write(dict(vals, **line._offer_values(partner)))
            result = True
        else:
            result = super().write(vals)
        if {"cantidad_final", "drogueria_id"} & set(vals):
            # Lo que cambia el comprador no puede pasar del stock de la droguería.
            self._check_stock_drogueria()
        if {"cantidad_final", "drogueria_id", "motivo"} & set(vals):
            self.proposal_id.filtered(lambda p: p.state == "ready").write({"state": "in_review"})
        return result

    @api.constrains("cantidad_final")
    def _check_cantidad_final(self):
        if any(line.cantidad_final < 0 for line in self):
            raise ValidationError(_("La cantidad final no puede ser negativa."))

    # -- borradores -------------------------------------------------------------

    @api.model
    def _purchase_description(self, product, partner):
        product = product.with_context(lang=partner.lang or self.env.lang, partner_id=partner.id)
        name = product.display_name
        if product.description_purchase:
            name += "\n" + product.description_purchase
        return name

    def action_create_rfq(self):
        """Crea las órdenes de compra en borrador de estas líneas y las abre."""
        if not self.env.user.has_group("purchase.group_purchase_user"):
            raise AccessError(_("Para generar órdenes de compra hace falta el permiso de Compras."))
        if any(p.state in CLOSED_STATES for p in self.proposal_id):
            raise UserError(_("La propuesta ya está cerrada."))
        lines = self.filtered(lambda l: l.cantidad_final > 0 and l.drogueria_id and not l.purchase_line_id)
        review = lines.filtered(lambda l: l.revisar_presentacion and not l.ajustada)
        lines -= review
        if not lines:
            raise UserError(_(
                "No hay nada que pedir: hacen falta líneas con cantidad final y droguería que no estén "
                "ya en una orden. Las marcadas para revisar la presentación (%s) entran cuando se cambia "
                "su cantidad o su droguería.", len(review)))
        missing = lines.company_id - self.env.user.company_ids
        if missing:
            raise UserError(_("No tienes acceso a: %s. Pide acceso a esa sucursal para generar las órdenes.",
                              ", ".join(missing.mapped("name"))))
        orders = lines._create_orders(self.env.user, self.env.user.name)
        action = self.env["ir.actions.act_window"]._for_xml_id("purchase.purchase_rfq")
        action["domain"] = [("id", "in", orders.ids)]
        action["context"] = {}
        if len(orders) == 1:
            action.update({"view_mode": "form", "views": [(False, "form")], "res_id": orders.id})
        return action

    def _create_orders(self, buyer, author):
        """Crea o completa las órdenes en borrador de estas líneas (sin revisar permisos).

        Una por propuesta, matriz y droguería; lo nuevo se suma al borrador
        que la propuesta ya creó para esa droguería. `buyer` queda como
        comprador de las órdenes nuevas; `author` firma el mensaje.
        """
        lines = self
        groups = {}
        for line in lines:
            groups.setdefault((line.proposal_id, line.company_id, line.drogueria_id), self.browse())
            groups[(line.proposal_id, line.company_id, line.drogueria_id)] |= line
        orders = self.env["purchase.order"]
        for (proposal, company, partner), group in groups.items():
            order = proposal.purchase_order_ids.filtered(
                lambda o: o.state == "draft" and o.company_id == company and o.partner_id == partner)[:1]
            if not order:
                order = self.env["purchase.order"].with_company(company).create({
                    "partner_id": partner.id,
                    "company_id": company.id,
                    "origin": proposal.name,
                    "user_id": buyer.id or self.env.uid,
                })
                proposal.purchase_order_ids = [Command.link(order.id)]
            POLine = self.env["purchase.order.line"].with_company(company)
            fpos = order.fiscal_position_id or order.fiscal_position_id._get_fiscal_position(partner)
            date_planned = POLine._get_date_planned(False, po=order)
            # Con todos los campos dados, Odoo no ejecuta el onchange de producto por línea
            # (lo más lento). Mismos valores que ese onchange, salvo el precio: el precio
            # final de la oferta, que ya trae los descuentos de la droguería.
            po_lines = POLine.create([{
                "order_id": order.id,
                "product_id": line.product_id.id,
                "name": self._purchase_description(line.product_id, partner),
                "product_qty": line.cantidad_final,
                "product_uom": line.product_id.uom_id.id,
                "price_unit": line.precio,
                "taxes_id": [Command.set(fpos.map_tax(
                    line.product_id.supplier_taxes_id._filter_taxes_by_company(company)).ids)],
                "date_planned": date_planned,
            } for line in group])
            for line, po_line in zip(group, po_lines):
                line.purchase_line_id = po_line
            orders |= order

        for proposal in lines.proposal_id:
            done = not proposal._pending_lines()
            proposal.state = "done" if done else "in_review"
            proposal.message_post(body=_(
                "%(user)s generó %(lines)s líneas en borradores: %(orders)s.",
                user=author, lines=len(lines.filtered(lambda l: l.proposal_id == proposal)),
                orders=", ".join(orders.filtered(lambda o: o in proposal.purchase_order_ids).mapped("name"))))
        return orders
