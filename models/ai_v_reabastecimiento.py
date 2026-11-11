# -*- coding: utf-8 -*-
import logging
from collections import defaultdict

from odoo import _, api, fields, models, tools
from odoo.exceptions import AccessError, UserError

from ..services import automation
from ..services.network_sql import MATRIX_PARAM, network_ctes  # noqa: F401 (MATRIX_PARAM lo usan los tests)
from .ai_v_oferta import grant_readonly
from .ai_v_rotacion import network_params_sql

_logger = logging.getLogger(__name__)


class AiVReabastecimiento(models.Model):
    """Lo que la sucursal matriz (Dromax) puede mandar a cada farmacia.

    Por producto y farmacia (que no sea la matriz), lo que le falta para volver
    al objetivo: demanda depurada × semanas de cobertura más el margen de
    seguridad − stock − compras pendientes − lo que ya viene en camino por
    traslado (borradores y traslados sin terminar que llegan a la farmacia
    desde otra sucursal o desde tránsito). Solo entra cuando lo que tiene baja
    al punto de pedido (un % de la venta de esas semanas), para no mover una o
    dos unidades. Cobertura, punto y margen se leen de Ajustes. Estado: «En
    cero» si no tiene stock, «Por agotarse» si tiene pero llegó al punto. Con
    stock en cero también entra un producto de venta
    esporádica (demanda depurada 0 pero con venta en el período): se repone su
    venta media.

    Disponible en la matriz = su stock − lo que ya sale hacia otras sucursales
    (borradores y traslados sin terminar) − su propia demanda de las mismas
    semanas (no se queda sin mercancía para su venta). Si no alcanza para
    todas, se reparte primero a la farmacia más urgente (menor cobertura).
    Solo productos almacenables, en la unidad del producto. El valor es al
    costo de la matriz, que es el que pasa el traslado.

    id = product_id × 1000 + company_id: estable entre consultas, para que las
    líneas elegidas en la pantalla sean las mismas al generar los borradores.
    """
    _name = "ai.v.reabastecimiento"
    _description = "Reabastecimiento desde la sucursal matriz"
    _auto = False
    _order = "estado, cobertura_actual, valor desc, company_id"
    _rec_name = "product_id"

    company_id = fields.Many2one("res.company", "Farmacia", readonly=True,
                                 help="Farmacia que recibe la mercancía.")
    farmacia = fields.Char("Nombre de la farmacia", readonly=True)
    matriz_id = fields.Many2one("res.company", "Origen", readonly=True,
                                help="Sucursal matriz que manda la mercancía (Ajustes).")
    product_id = fields.Many2one("product.product", "Producto", readonly=True)
    product_tmpl_id = fields.Many2one("product.template", "Plantilla", readonly=True)
    producto = fields.Char("Nombre del producto", readonly=True)
    codigo = fields.Char("Código interno", readonly=True)
    estado = fields.Selection([("cero", "En cero"), ("por_agotarse", "Por agotarse")],
                              "Estado", readonly=True)
    demanda_semanal = fields.Float("Venta semanal", readonly=True,
                                   help="Demanda depurada de la farmacia (o su venta media si es esporádica).")
    semanas_cobertura = fields.Integer("Semanas de cobertura", readonly=True)
    stock = fields.Float("Stock en la farmacia", readonly=True, help="El negativo cuenta como 0.")
    pendiente_compra = fields.Float("Compras pendientes", readonly=True)
    en_camino = fields.Float("En camino por traslado", readonly=True,
                             help="Borradores y traslados sin terminar que llegan a la farmacia.")
    cobertura_actual = fields.Float("Cobertura actual (semanas)", readonly=True,
                                    help="(Stock + compras pendientes + en camino) / venta semanal.")
    falta = fields.Integer("Falta", readonly=True,
                           help="Objetivo (venta semanal × semanas de cobertura + margen de seguridad) − stock − "
                                "pendiente − en camino. Solo si llegó al punto de pedido.")
    cantidad = fields.Integer("A trasladar", readonly=True,
                              help="Lo que la matriz puede mandar: la falta, o lo que le quede si no alcanza.")
    parcial = fields.Boolean("Parcial", readonly=True, help="La matriz no alcanza para toda la falta.")
    stock_matriz = fields.Float("Stock en la matriz", readonly=True)
    disponible_matriz = fields.Float(
        "Disponible en la matriz", readonly=True,
        help="Stock de la matriz − lo que ya sale a otras sucursales − su propia venta de las "
             "semanas de cobertura. Se reparte entre las farmacias, primero la más urgente.")
    costo = fields.Float("Costo", readonly=True, help="Costo del producto en la matriz.")
    valor = fields.Float("Valor", readonly=True, help="A trasladar × costo.")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE VIEW ai_v_reabastecimiento AS
            WITH %(red)s
            disponible AS (
                -- Lo que la matriz puede repartir: lo libre menos su propia venta de las semanas.
                SELECT m.product_id, m.stock,
                       floor(m.libre - ceil(m.demanda * params.semanas)) AS disponible
                  FROM matriz m
                 CROSS JOIN params
            ),
            reparto AS (
                SELECT f.*, m.stock AS stock_matriz, m.disponible,
                       sum(f.falta) OVER (PARTITION BY f.product_id
                                          ORDER BY f.cobertura, f.demanda DESC, f.company_id
                                          ROWS UNBOUNDED PRECEDING) AS acumulado
                  FROM faltas f
                  JOIN disponible m ON m.product_id = f.product_id AND m.disponible >= 1
            ),
            lineas AS (
                SELECT r.*, least(r.falta, greatest(r.disponible - (r.acumulado - r.falta), 0))::int AS cantidad
                  FROM reparto r
            ),
            costos AS (
                -- standard_price depende de la empresa: está en ir_property (el de
                -- la matriz o, si no tiene, el valor por defecto sin empresa).
                SELECT DISTINCT ON (l.product_id) l.product_id, ip.value_float AS costo
                  FROM (SELECT DISTINCT product_id FROM lineas) l
                 CROSS JOIN params
                  JOIN ir_property ip ON ip.name = 'standard_price'
                                     AND ip.res_id = 'product.product,' || l.product_id
                                     AND (ip.company_id = params.matriz_id OR ip.company_id IS NULL)
                 ORDER BY l.product_id, ip.company_id NULLS LAST
            )
            SELECT (l.product_id::bigint * 1000 + l.company_id)::int AS id,
                   l.company_id,
                   c.name AS farmacia,
                   params.matriz_id,
                   l.product_id,
                   pp.product_tmpl_id,
                   COALESCE(pt.name->>'es_VE', pt.name->>'en_US') AS producto,
                   pp.default_code AS codigo,
                   CASE WHEN l.stock <= 0 THEN 'cero' ELSE 'por_agotarse' END AS estado,
                   round(l.demanda::numeric, 2) AS demanda_semanal,
                   params.semanas AS semanas_cobertura,
                   round(l.stock::numeric, 2) AS stock,
                   round(l.pendiente::numeric, 2) AS pendiente_compra,
                   round(l.en_camino::numeric, 2) AS en_camino,
                   round(l.cobertura::numeric, 2) AS cobertura_actual,
                   l.falta,
                   l.cantidad,
                   l.cantidad < l.falta AS parcial,
                   round(l.stock_matriz::numeric, 2) AS stock_matriz,
                   l.disponible AS disponible_matriz,
                   round(COALESCE(k.costo, 0)::numeric, 2) AS costo,
                   round((l.cantidad * COALESCE(k.costo, 0))::numeric, 2) AS valor
              FROM lineas l
             CROSS JOIN params
              JOIN res_company c ON c.id = l.company_id
              JOIN product_product pp ON pp.id = l.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
              LEFT JOIN costos k ON k.product_id = l.product_id
             WHERE l.cantidad > 0 AND pp.active AND pt.type = 'product'
        """ % {"red": network_ctes(*network_params_sql())})
        grant_readonly(self.env, self._table)

    # -- borradores de traslado ------------------------------------------------

    def action_generate_transfers(self):
        """Un Traslado interno en borrador de la matriz a cada farmacia con las líneas elegidas.

        Es el mismo borrador que se arma a mano para el módulo de traslados
        (advanced_interbranch_transfer): tipo Traslado interno del almacén de la
        matriz, de sus existencias a las de la farmacia. Después, en cada uno,
        «Generar Traslados Definitivos» arma la cadena por tránsito con el costo
        de origen, los lotes y los pasos de cada almacén. Las cantidades se
        vuelven a leer al pulsar (lo ya enviado o en borrador se descuenta solo).
        """
        user = self.env.user
        if not user.has_group("stock.group_stock_user"):
            raise AccessError(_("Para crear traslados hace falta el permiso de Inventario."))
        self.env.flush_all()
        self.invalidate_model()
        lines = self.browse(self.ids).exists().filtered(lambda l: l.cantidad > 0)
        if not lines:
            raise UserError(_("No hay nada que trasladar en las líneas elegidas "
                              "(puede que ya estén en un borrador o en camino)."))
        # sudo solo para nombrar las sucursales: quien no tiene acceso no puede leerlas.
        missing = (lines.sudo().matriz_id | lines.sudo().company_id) - user.sudo().company_ids
        if missing:
            raise UserError(_("No tienes acceso a: %s. Pide acceso a esas sucursales o quita sus líneas.",
                              ", ".join(missing.sudo().mapped("name"))))
        pickings = lines._create_transfers(user.company_ids)
        return {
            "type": "ir.actions.act_window",
            "name": _("Borradores de traslado"),
            "res_model": "stock.picking",
            "view_mode": "tree,form",
            "domain": [("id", "in", pickings.ids)],
            "context": {"create": False},
        }

    def _create_transfers(self, companies):
        """Crea los borradores de estas líneas (sin revisar permisos): uno por farmacia.

        `companies`: las sucursales del contexto, además de la matriz.
        """
        lines = self
        by_route = defaultdict(lambda: self.browse())
        for line in lines:
            by_route[(line.matriz_id, line.company_id)] |= line

        origin = _("Reabastecimiento %s", fields.Date.context_today(self).strftime("%d/%m/%Y"))
        Warehouse = self.env["stock.warehouse"].sudo()
        pickings = self.env["stock.picking"]
        for (matriz, destino), route_lines in sorted(by_route.items(), key=lambda kv: kv[0][1].name):
            src_wh = Warehouse.search([("company_id", "=", matriz.id)], order="sequence, id", limit=1)
            dest_wh = Warehouse.search([("company_id", "=", destino.id)], order="sequence, id", limit=1)
            if not src_wh or not dest_wh:
                raise UserError(_("%s no tiene almacén.", (matriz if not src_wh else destino).name))
            pick_type = src_wh.int_type_id
            if not pick_type or not pick_type.active:
                raise UserError(_(
                    "El almacén %(wh)s de %(company)s no tiene activo el tipo «Traslados internos». "
                    "Actívalo en Inventario → Configuración → Tipos de operación (filtro Archivado) "
                    "y, si no aparece, activa Ajustes → Inventario → Ubicaciones de almacenamiento.",
                    wh=src_wh.name, company=matriz.name))
            src_loc, dest_loc = src_wh.lot_stock_id, dest_wh.lot_stock_id
            Picking = self.env["stock.picking"].with_context(
                allowed_company_ids=(matriz | companies).ids).with_company(matriz)
            pickings |= Picking.create({
                "picking_type_id": pick_type.id,
                "location_id": src_loc.id,
                "location_dest_id": dest_loc.id,
                "origin": origin,
                "move_ids_without_package": [(0, 0, {
                    "name": line.product_id.display_name,
                    "product_id": line.product_id.id,
                    "product_uom_qty": line.cantidad,
                    "product_uom": line.product_id.uom_id.id,
                    "location_id": src_loc.id,
                    "location_dest_id": dest_loc.id,
                }) for line in route_lines.sorted(lambda l: l.producto or "")],
            })
        return pickings

    @api.model
    def _cron_generate_transfers(self):
        """Tarea programada diaria: con el ajuste activo, crea los borradores de
        traslado de todas las líneas y le deja una actividad al responsable en
        cada uno. Lo que ya va en borrador cuenta como en camino: no se duplica."""
        if not automation.is_enabled(self.env, automation.AUTO_TRANSFERS):
            return self.env["stock.picking"]
        self.env.flush_all()
        lines = self.sudo().search([("cantidad", ">", 0)])
        if not lines:
            _logger.info("Reabastecimiento automático: nada que trasladar.")
            return self.env["stock.picking"]
        try:
            pickings = lines._create_transfers(lines.matriz_id | lines.company_id)
        except UserError as exc:
            # Falta un almacén o el tipo de traslado: se registra y la tarea no se reintenta en bucle.
            _logger.error("Reabastecimiento automático: %s", exc)
            return self.env["stock.picking"]
        user = automation.responsible(self.env, automation.TRANSFER_RESPONSIBLE)
        for picking in pickings:
            picking.message_post(body=_("Borrador creado por el Agente IA (reabastecimiento diario)."))
            if user:
                picking.activity_schedule(
                    "mail.mail_activity_data_todo", user_id=user.id,
                    summary=_("Despachar reabastecimiento a %s", picking.location_dest_id.company_id.name),
                    note=_("Revisa las cantidades, pulsa «Generar Traslados Definitivos» y valida la salida."))
        _logger.info("Reabastecimiento automático: %s borradores (%s).", len(pickings),
                     ", ".join(pickings.mapped("name")))
        return pickings
