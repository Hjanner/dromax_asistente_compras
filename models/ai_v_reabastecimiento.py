# -*- coding: utf-8 -*-
from collections import defaultdict

from odoo import _, fields, models, tools
from odoo.exceptions import AccessError, UserError

from ..services.config import PARAM_PREFIX
from ..services.prompt import DEFAULT_COVERAGE_WEEKS
from .ai_v_oferta import grant_readonly
from .ai_v_rotacion import _int_param

MATRIX_PARAM = PARAM_PREFIX + "matrix_company_id"

# Sucursal matriz: la del ajuste si existe; si no, la primera empresa con
# «DROMAX» en el nombre (la droguería que distribuye a las farmacias).
MATRIX_SQL = """COALESCE(
    (SELECT c.id FROM res_company c
      WHERE c.id::text = (SELECT trim(value) FROM ir_config_parameter WHERE key = '%s')),
    (SELECT c.id FROM res_company c WHERE c.name ILIKE '%%DROMAX%%' ORDER BY c.id LIMIT 1))""" % MATRIX_PARAM


class AiVReabastecimiento(models.Model):
    """Lo que la sucursal matriz (Dromax) puede mandar a cada farmacia.

    Por producto y farmacia (que no sea la matriz), lo que le falta para cubrir
    las semanas de cobertura de Ajustes: demanda depurada × semanas − stock −
    compras pendientes − lo que ya viene en camino por traslado (borradores y
    traslados sin terminar que llegan a la farmacia desde otra sucursal o desde
    tránsito). Estado: «En cero» si no tiene stock, «Por agotarse» si tiene
    pero no le alcanza. Con stock en cero también entra un producto de venta
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
                           help="Venta semanal × semanas de cobertura − stock − pendiente − en camino.")
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
            WITH params AS MATERIALIZED (
                SELECT %(cobertura)s AS semanas, %(matriz)s AS matriz_id
            ),
            -- Materializadas: se usan dos veces y, si no, Postgres las recalcula
            -- por cada fila de la unión (ai_v_rotacion es la parte cara).
            rot AS MATERIALIZED (
                SELECT company_id, product_id, demanda_depurada, demanda_media FROM ai_v_rotacion
            ),
            stk AS MATERIALIZED (
                SELECT company_id, product_id, stock FROM ai_v_stock
            ),
            en_camino AS (
                -- Llega a una ubicación interna de la farmacia desde otra sucursal o
                -- desde tránsito (no cuenta los pasos internos de la propia farmacia
                -- ni las recepciones de compra, que ya están en ai_v_pendiente).
                SELECT ld.company_id, m.product_id, sum(m.product_qty) AS cantidad
                  FROM stock_move m
                  JOIN stock_location ld ON ld.id = m.location_dest_id AND ld.usage = 'internal'
                  JOIN stock_location ls ON ls.id = m.location_id
                 WHERE m.state NOT IN ('done', 'cancel')
                   AND (ls.usage = 'transit' OR (ls.usage = 'internal' AND ls.company_id <> ld.company_id))
                 GROUP BY 1, 2
            ),
            sale AS (
                -- Sale de una ubicación interna hacia tránsito u otra sucursal.
                SELECT ls.company_id, m.product_id, sum(m.product_qty) AS cantidad
                  FROM stock_move m
                  JOIN stock_location ls ON ls.id = m.location_id AND ls.usage = 'internal'
                  JOIN stock_location ld ON ld.id = m.location_dest_id
                 WHERE m.state NOT IN ('done', 'cancel')
                   AND (ld.usage = 'transit' OR (ld.usage = 'internal' AND ld.company_id <> ls.company_id))
                 GROUP BY 1, 2
            ),
            destinos AS (
                SELECT r.company_id, r.product_id,
                       greatest(COALESCE(s.stock, 0), 0) AS stock,
                       COALESCE(p.pendiente, 0) AS pendiente,
                       COALESCE(c.cantidad, 0) AS en_camino,
                       CASE WHEN r.demanda_depurada > 0 THEN r.demanda_depurada
                            WHEN COALESCE(s.stock, 0) <= 0 THEN r.demanda_media
                            ELSE 0 END AS demanda
                  FROM rot r
                 CROSS JOIN params
                  LEFT JOIN stk s ON s.company_id = r.company_id AND s.product_id = r.product_id
                  LEFT JOIN ai_v_pendiente p ON p.company_id = r.company_id AND p.product_id = r.product_id
                  LEFT JOIN en_camino c ON c.company_id = r.company_id AND c.product_id = r.product_id
                 WHERE r.company_id <> params.matriz_id
            ),
            faltas AS (
                SELECT d.*, ceil(d.demanda * params.semanas - d.stock - d.pendiente - d.en_camino)::int AS falta,
                       (d.stock + d.pendiente + d.en_camino) / d.demanda AS cobertura
                  FROM destinos d
                 CROSS JOIN params
                 WHERE d.demanda > 0 AND d.demanda * params.semanas - d.stock - d.pendiente - d.en_camino > 0
            ),
            matriz AS (
                SELECT s.product_id, s.stock,
                       floor(greatest(s.stock, 0) - COALESCE(x.cantidad, 0)
                             - ceil(COALESCE(r.demanda_depurada, 0) * params.semanas)) AS disponible
                  FROM stk s
                 CROSS JOIN params
                  LEFT JOIN rot r ON r.company_id = s.company_id AND r.product_id = s.product_id
                  LEFT JOIN sale x ON x.company_id = s.company_id AND x.product_id = s.product_id
                 WHERE s.company_id = params.matriz_id
            ),
            reparto AS (
                SELECT f.*, m.stock AS stock_matriz, m.disponible,
                       sum(f.falta) OVER (PARTITION BY f.product_id
                                          ORDER BY f.cobertura, f.demanda DESC, f.company_id
                                          ROWS UNBOUNDED PRECEDING) AS acumulado
                  FROM faltas f
                  JOIN matriz m ON m.product_id = f.product_id AND m.disponible >= 1
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
        """ % {
            "cobertura": _int_param("coverage_weeks", DEFAULT_COVERAGE_WEEKS, 52),
            "matriz": MATRIX_SQL,
        })
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
                allowed_company_ids=(matriz | user.company_ids).ids).with_company(matriz)
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
        return {
            "type": "ir.actions.act_window",
            "name": _("Borradores de traslado"),
            "res_model": "stock.picking",
            "view_mode": "tree,form",
            "domain": [("id", "in", pickings.ids)],
            "context": {"create": False},
        }
