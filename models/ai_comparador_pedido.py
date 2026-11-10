# -*- coding: utf-8 -*-
"""Pedir desde el comparador de precios.

El comprador marca uno o varios productos en el comparador (o pulsa el botón
del visor de uno) y confirma, por producto, la droguería y la cantidad. Se
agregan a la orden de compra en borrador de la sucursal matriz con esa
droguería que haya creado el comparador (origen «Comparador de precios»); si
no hay, se crea. Así se pueden ir sumando productos en varias búsquedas sin
recordar sus códigos. Las órdenes hechas a mano o desde la propuesta no se
tocan.

Solo se pueden pedir productos de nuestro catálogo (la línea de compra
necesita el producto); los demás se avisan y se dejan fuera.
"""
from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services.network_sql import matrix_company

ORIGIN = "Comparador de precios"


class AiComparadorPedido(models.TransientModel):
    _name = "ai.comparador.pedido"
    _description = "Pedido desde el comparador de precios"

    company_id = fields.Many2one("res.company", "Compra", readonly=True,
                                 help="Sucursal matriz: toda compra entra por ella.")
    line_ids = fields.One2many("ai.comparador.pedido.line", "wizard_id", "Productos")
    sin_catalogo = fields.Text("No están en el catálogo", readonly=True)
    amount_total = fields.Float("Total", compute="_compute_amount_total")

    @api.depends("line_ids.subtotal")
    def _compute_amount_total(self):
        for wizard in self:
            wizard.amount_total = sum(wizard.line_ids.mapped("subtotal"))

    @api.model
    def create_from_comparador(self, rows):
        """Asistente con una línea por producto del catálogo; los demás quedan en sin_catalogo."""
        company = matrix_company(self.env)
        if not company:
            raise UserError(_("No hay sucursal matriz: elígela en Compras IA → Configuración → Ajustes."))
        in_catalog = rows.filtered("product_id")
        self.env.cr.execute("SELECT product_id, cantidad_sugerida FROM ai_v_faltantes WHERE product_id = ANY(%s)",
                            (in_catalog.product_id.ids,))
        suggested = dict(self.env.cr.fetchall())
        Line = self.env["ai.comparador.pedido.line"]
        wizard = self.create({
            "company_id": company.id,
            "sin_catalogo": "\n".join((rows - in_catalog).mapped("nombre")) or False,
        })
        for row in in_catalog:
            line = Line.create({"wizard_id": wizard.id, "product_id": row.product_id.id,
                                "drogueria_id": row.drogueria_id.id,
                                "cantidad": suggested.get(row.product_id.id) or 1})
            line.write(line._offer_values())
        return wizard

    def action_confirm(self):
        """Agrega las líneas a los borradores del comparador (uno por droguería) y avisa cuáles."""
        self.ensure_one()
        user = self.env.user
        if not user.has_group("purchase.group_purchase_user"):
            raise AccessError(_("Para crear órdenes de compra hace falta el permiso de Compras."))
        # sudo solo para nombrar la sucursal: quien no tiene acceso no puede leerla.
        company = self.sudo().company_id
        if company not in user.sudo().company_ids:
            raise UserError(_("No tienes acceso a %s, la sucursal que compra.", company.name))
        company = self.company_id
        lines = self.line_ids.filtered(lambda l: l.cantidad > 0)
        if not lines:
            raise UserError(_("No hay nada que pedir: pon una cantidad mayor que 0."))
        if lines.filtered(lambda l: not l.drogueria_id):
            raise UserError(_("Elige la droguería de: %s.",
                              ", ".join(lines.filtered(lambda l: not l.drogueria_id).product_id.mapped("display_name"))))

        Order = self.env["purchase.order"].with_company(company)
        POLine = self.env["purchase.order.line"].with_company(company)
        describe = self.env["ai.purchase.proposal.line"]._purchase_description
        orders = Order.browse()
        for partner in lines.drogueria_id:
            order = Order.search([("state", "=", "draft"), ("company_id", "=", company.id),
                                  ("partner_id", "=", partner.id), ("origin", "=", ORIGIN),
                                  ("user_id", "=", user.id)], order="id desc", limit=1)
            if not order:
                order = Order.create({"partner_id": partner.id, "company_id": company.id,
                                      "origin": ORIGIN, "user_id": user.id})
            fpos = order.fiscal_position_id or order.fiscal_position_id._get_fiscal_position(partner)
            date_planned = POLine._get_date_planned(False, po=order)
            for line in lines.filtered(lambda l: l.drogueria_id == partner):
                existing = order.order_line.filtered(lambda pl: pl.product_id == line.product_id)[:1]
                if existing:
                    existing.write({"product_qty": existing.product_qty + line.cantidad, "price_unit": line.precio})
                    continue
                # Mismos valores que el onchange del producto, salvo el precio: el final de la oferta.
                POLine.create({
                    "order_id": order.id,
                    "product_id": line.product_id.id,
                    "name": describe(line.product_id, partner),
                    "product_qty": line.cantidad,
                    "product_uom": line.product_id.uom_id.id,
                    "price_unit": line.precio,
                    "taxes_id": [Command.set(fpos.map_tax(
                        line.product_id.supplier_taxes_id._filter_taxes_by_company(company)).ids)],
                    "date_planned": date_planned,
                })
            orders |= order
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Agregado al borrador de compra"),
                "message": _("%(n)s producto(s) en: %(orders)s.", n=len(lines),
                             orders=", ".join("%s (%s)" % (o.name, o.partner_id.name) for o in orders)),
                "links": [{"label": o.name, "url": "/web#id=%s&model=purchase.order&view_type=form" % o.id}
                          for o in orders],
                "type": "success",
                "sticky": True,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }


class AiComparadorPedidoLine(models.TransientModel):
    _name = "ai.comparador.pedido.line"
    _description = "Producto a pedir desde el comparador"

    wizard_id = fields.Many2one("ai.comparador.pedido", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", "Producto", required=True, readonly=True)
    drogueria_id = fields.Many2one("res.partner", "Droguería", domain="[('id', 'in', drogueria_opciones_ids)]")
    drogueria_opciones_ids = fields.Many2many("res.partner", compute="_compute_drogueria_opciones")
    cantidad = fields.Integer("Cantidad", default=1,
                              help="Por defecto, lo que sugiere la necesidad de compra de la red.")
    precio = fields.Float("Precio final", readonly=True)
    descuentos = fields.Char("Descuentos", readonly=True)
    stock_drogueria = fields.Integer("Stock en la droguería", readonly=True)
    fecha_vencimiento = fields.Date("Vencimiento", readonly=True)
    subtotal = fields.Float("Subtotal", compute="_compute_subtotal")

    @api.depends("cantidad", "precio")
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.cantidad * line.precio

    @api.depends("product_id")
    def _compute_drogueria_opciones(self):
        options = {}
        if self.product_id:
            self.env.cr.execute("""
                SELECT product_id, array_agg(DISTINCT partner_id) FROM ai_v_oferta
                 WHERE disponible AND product_id = ANY(%s) GROUP BY product_id
            """, (self.product_id.ids,))
            options = dict(self.env.cr.fetchall())
        for line in self:
            line.drogueria_opciones_ids = [Command.set(options.get(line.product_id.id, []))]

    def _offer_values(self):
        """Precio y datos de la mejor oferta disponible de la droguería elegida."""
        self.ensure_one()
        empty = {"precio": 0.0, "descuentos": False, "stock_drogueria": 0, "fecha_vencimiento": False}
        if not self.product_id or not self.drogueria_id:
            return empty
        self.env.cr.execute("""
            SELECT precio_final, descuento_texto, stock_drogueria, fecha_vencimiento FROM ai_v_oferta
             WHERE disponible AND product_id = %s AND partner_id = %s
             ORDER BY precio_final, id LIMIT 1
        """, (self.product_id.id, self.drogueria_id.id))
        row = self.env.cr.fetchone()
        if not row:
            return empty
        return {"precio": row[0], "descuentos": row[1], "stock_drogueria": row[2] or 0,
                "fecha_vencimiento": row[3]}

    @api.onchange("drogueria_id")
    def _onchange_drogueria_id(self):
        for line in self:
            line.update(line._offer_values())
