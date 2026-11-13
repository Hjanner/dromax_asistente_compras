# -*- coding: utf-8 -*-
from odoo import _, fields, models, tools

from .ai_v_oferta import grant_readonly


class AiVComparador(models.Model):
    """Comparador de precios: una fila por producto (código de barras válido).

    Agrupa las ofertas de todas las droguerías de un mismo código, aunque
    cada una lo escriba distinto, y muestra la mejor, la segunda y el ahorro.
    Al abrirla, `oferta_ids` es el visor con la oferta de cada droguería
    (precio de lista, descuentos, precio final, stock y vencimiento).

    Sale de ai_v_oferta, así que comparte sus reglas: solo cuentan las ofertas
    disponibles (con stock, precio, lote no vencido y código válido) y cada
    droguería entra con su mejor lote. precios_dispares avisa cuando el precio
    más caro pasa del triple del más barato: casi siempre es otra presentación
    con el mismo código (un blíster frente a la caja de 30 blísteres), y
    entonces el ahorro no es real.

    id = el de la fila más antigua del código en la oferta (comparador_id de
    ai_v_oferta), estable mientras no se vuelva a cargar la droguería.
    """
    _name = "ai.v.comparador"
    _description = "Oferta de proveedores (comparación de precios)"
    _auto = False
    _order = "nombre, codigo_barras"
    _rec_name = "nombre"

    nombre = fields.Char("Producto", readonly=True,
                         help="El nombre de nuestro catálogo o, si no lo tenemos, el de la droguería más barata.")
    codigo_barras = fields.Char("Código de barras", readonly=True)
    product_id = fields.Many2one("product.product", "Producto propio", readonly=True)
    product_tmpl_id = fields.Many2one("product.template", "Plantilla", readonly=True)
    en_catalogo = fields.Boolean("En nuestro catálogo", readonly=True)
    laboratorio = fields.Char("Laboratorio", readonly=True, help="El que informan más droguerías.")
    droguerias_total = fields.Integer("Droguerías que lo ofrecen", readonly=True)
    droguerias_con_stock = fields.Integer("Droguerías con stock", readonly=True)
    drogueria_id = fields.Many2one("res.partner", "Más barata", readonly=True)
    descripcion = fields.Char("Descripción en la más barata", readonly=True)
    mejor_precio = fields.Float("Mejor precio", readonly=True, help="Precio final, con todos los descuentos.")
    descuentos = fields.Char("Descuentos de la más barata", readonly=True)
    stock_mejor = fields.Integer("Stock de la más barata", readonly=True)
    vencimiento_mejor = fields.Date("Vencimiento de la más barata", readonly=True)
    vence_pronto = fields.Boolean("La más barata vence pronto", readonly=True,
                                  help="Su lote vence en menos de 90 días.")
    stock_bajo = fields.Boolean("La más barata tiene poco stock", readonly=True,
                                help="La droguería más barata tiene 1 o 2 unidades.")
    segunda_drogueria_id = fields.Many2one("res.partner", "Segunda", readonly=True)
    segundo_precio = fields.Float("Segundo precio", readonly=True)
    ahorro = fields.Float("Ahorro por unidad", readonly=True, help="Segundo precio − mejor precio.")
    ahorro_pct = fields.Float("Ahorro (%)", readonly=True, help="Ahorro frente a la segunda droguería.")
    precio_max = fields.Float("Precio más alto", readonly=True)
    precios_dispares = fields.Boolean(
        "Posible otra presentación", readonly=True,
        help="El precio más alto pasa del triple del más bajo: probablemente una droguería vende "
             "otro empaque con el mismo código. Compara las descripciones antes de decidir.")
    stock_total = fields.Integer("Stock entre todas", readonly=True)
    fecha_carga = fields.Datetime("Oferta cargada el", readonly=True)
    oferta_ids = fields.One2many("ai.v.oferta", "comparador_id", "Ofertas", readonly=True)
    # La mejor oferta disponible de cada droguería (la que lleva puesto), de la más barata a la más cara.
    mejores_ofertas_ids = fields.One2many("ai.v.oferta", "comparador_id", "Precio en cada droguería",
                                          readonly=True, domain=[("orden_precio", "!=", False)])

    def action_open_offers(self):
        """Clic en una fila de la lista: el precio de cada droguería en una ventana emergente."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.nombre,
            "res_model": self._name,
            "res_id": self.id,
            "views": [(self.env.ref("dromax_asistente_compras.view_ai_v_comparador_form_modal").id, "form")],
            "target": "new",
            "context": {"create": False, "edit": False},
        }

    def action_add_to_rfq(self):
        """Productos elegidos → ventana para confirmar droguería y cantidad antes de pedirlos."""
        if not self:
            return False
        wizard = self.env["ai.comparador.pedido"].create_from_comparador(self)
        return {
            "type": "ir.actions.act_window",
            "name": _("Añadir a borrador de compra"),
            "res_model": wizard._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE VIEW ai_v_comparador AS
            WITH o AS MATERIALIZED (
                SELECT * FROM ai_v_oferta WHERE comparador_id IS NOT NULL
            ),
            mejor AS (
                SELECT DISTINCT ON (comparador_id) comparador_id, partner_id, descripcion, precio_final,
                       descuento_texto, stock_drogueria, fecha_vencimiento, vence_pronto, stock_bajo
                  FROM o
                 WHERE orden_precio = 1
                 ORDER BY comparador_id, id
            ),
            segunda AS (
                SELECT DISTINCT ON (comparador_id) comparador_id, partner_id
                  FROM o
                 WHERE orden_precio = 2
                 ORDER BY comparador_id, id
            ),
            agrupado AS (
                SELECT comparador_id AS id,
                       max(codigo_barras) AS codigo_barras,
                       max(product_id) AS product_id,
                       max(product_tmpl_id) AS product_tmpl_id,
                       max(producto) AS producto,
                       max(descripcion) AS cualquier_descripcion,
                       mode() WITHIN GROUP (ORDER BY laboratorio) FILTER (WHERE laboratorio IS NOT NULL) AS laboratorio,
                       count(DISTINCT partner_id) AS droguerias_total,
                       max(droguerias_con_stock) AS droguerias_con_stock,
                       max(mejor_precio) AS mejor_precio,
                       max(segundo_precio) AS segundo_precio,
                       max(precio_final) FILTER (WHERE disponible) AS precio_max,
                       sum(stock_drogueria) FILTER (WHERE disponible) AS stock_total,
                       max(fecha_carga) AS fecha_carga
                  FROM o
                 GROUP BY comparador_id
            )
            SELECT a.id,
                   COALESCE(a.producto, m.descripcion, a.cualquier_descripcion) AS nombre,
                   a.codigo_barras,
                   a.product_id,
                   a.product_tmpl_id,
                   a.product_id IS NOT NULL AS en_catalogo,
                   a.laboratorio,
                   a.droguerias_total,
                   a.droguerias_con_stock,
                   m.partner_id AS drogueria_id,
                   m.descripcion,
                   a.mejor_precio,
                   m.descuento_texto AS descuentos,
                   m.stock_drogueria AS stock_mejor,
                   m.fecha_vencimiento AS vencimiento_mejor,
                   COALESCE(m.vence_pronto, false) AS vence_pronto,
                   COALESCE(m.stock_bajo, false) AS stock_bajo,
                   s.partner_id AS segunda_drogueria_id,
                   a.segundo_precio,
                   round((a.segundo_precio - a.mejor_precio)::numeric, 2) AS ahorro,
                   round((100 * (a.segundo_precio - a.mejor_precio) / nullif(a.segundo_precio, 0))::numeric, 1)
                       AS ahorro_pct,
                   a.precio_max,
                   COALESCE(a.precio_max > 3 * a.mejor_precio, false) AS precios_dispares,
                   COALESCE(a.stock_total, 0) AS stock_total,
                   a.fecha_carga
              FROM agrupado a
              LEFT JOIN mejor m ON m.comparador_id = a.id
              LEFT JOIN segunda s ON s.comparador_id = a.id
        """)
        grant_readonly(self.env, self._table)
