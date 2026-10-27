# -*- coding: utf-8 -*-
from odoo import fields, models, tools

from ..services.prompt import DEFAULT_COVERAGE_WEEKS, DEFAULT_EXCESS_WEEKS
from .ai_v_oferta import grant_readonly
from .ai_v_rotacion import _int_param


class AiVFaltantes(models.Model):
    """Lo que hace falta comprar por producto y farmacia.

    Faltante = demanda depurada × semanas de cobertura − stock − pendiente de
    recibir (ai_v_rotacion, ai_v_stock y ai_v_pendiente). El stock negativo
    cuenta como 0: no se compra para tapar ventas hechas sin existencia. Una
    fila por producto y farmacia con faltante > 0; la cantidad sugerida es el
    faltante redondeado hacia arriba. Las solicitudes sin confirmar no se
    restan (en_solicitud solo avisa).

    Como cualquier farmacia puede comprar o pedir para otra, informa también
    el excedente en las demás: el stock que pasa de N semanas de su propia
    demanda (contando lo que tienen pendiente; N de Ajustes, por defecto 4 y
    nunca menos que la cobertura). No se descuenta: trasladar o comprar lo
    decide el comprador.

    Droguería sugerida: entre las ofertas disponibles del producto, primero
    las de precio coherente con el costo de la farmacia (hasta el triple:
    algunas droguerías usan el mismo código de barras para un empaque mayor,
    p. ej. un dispensador de 50 blísteres), luego las que tienen stock para
    toda la cantidad y, entre ellas, la más barata. Si la elegida pasa del
    triple del costo, revisar_presentacion lo avisa. Los parámetros se leen de
    Ajustes en cada consulta.
    """
    _name = "ai.v.faltantes"
    _description = "Faltantes por producto y farmacia"
    _auto = False
    _order = "company_id, cobertura_actual, demanda_semanal desc, product_id"
    _rec_name = "product_id"

    company_id = fields.Many2one("res.company", "Farmacia", readonly=True)
    farmacia = fields.Char("Nombre de la farmacia", readonly=True)
    product_id = fields.Many2one("product.product", "Producto", readonly=True)
    product_tmpl_id = fields.Many2one("product.template", "Plantilla", readonly=True)
    producto = fields.Char("Nombre del producto", readonly=True)
    codigo = fields.Char("Código interno", readonly=True)
    demanda_semanal = fields.Float("Demanda semanal", readonly=True,
                                   help="Demanda depurada de la rotación.")
    semanas_cobertura = fields.Integer("Semanas de cobertura", readonly=True)
    necesidad = fields.Float("Necesidad", readonly=True,
                             help="Demanda semanal × semanas de cobertura.")
    stock = fields.Float("Stock", readonly=True, help="Existencia actual; la negativa cuenta como 0.")
    pendiente = fields.Float("Pendiente de recibir", readonly=True)
    atrasado = fields.Float("Atrasado", readonly=True, help="Parte de lo pendiente con la fecha prevista vencida.")
    en_solicitud = fields.Float("En solicitudes sin confirmar", readonly=True,
                                help="No se resta: avisa de que ya se está pidiendo.")
    cobertura_actual = fields.Float("Cobertura actual (semanas)", readonly=True,
                                    help="(Stock + pendiente) / demanda semanal.")
    faltante = fields.Float("Faltante", readonly=True, help="Necesidad − stock − pendiente.")
    cantidad_sugerida = fields.Integer("Cantidad sugerida", readonly=True,
                                       help="Faltante redondeado hacia arriba.")
    excedente_otras = fields.Float("Excedente en otras farmacias", readonly=True,
                                   help="Stock de las demás farmacias que pasa de las semanas de "
                                        "excedente de su propia demanda: se podría trasladar en vez de comprar.")
    farmacias_con_excedente = fields.Char("Farmacias con excedente", readonly=True)
    drogueria_id = fields.Many2one("res.partner", "Droguería sugerida", readonly=True)
    drogueria = fields.Char("Nombre de la droguería", readonly=True)
    precio = fields.Float("Precio final", readonly=True, help="Precio con descuentos de la droguería sugerida.")
    descuento = fields.Float("% Descuento", readonly=True,
                             help="Descuento de la droguería sugerida, ya incluido en el precio final.")
    mejor_precio = fields.Float("Mejor precio", readonly=True,
                                help="Precio final más bajo entre las droguerías con stock.")
    monto = fields.Float("Monto", readonly=True, help="Cantidad sugerida × precio final.")
    stock_drogueria = fields.Integer("Stock en la droguería", readonly=True)
    stock_drogueria_suficiente = fields.Boolean("Alcanza el stock de la droguería", readonly=True)
    droguerias_con_oferta = fields.Integer("Droguerías con oferta", readonly=True)
    fecha_vencimiento = fields.Date("Vencimiento", readonly=True, help="Vencimiento del lote ofrecido.")
    laboratorio = fields.Char("Laboratorio (droguería)", readonly=True,
                              help="Laboratorio que informa la droguería, si lo informa.")
    costo = fields.Float("Costo actual", readonly=True, help="Costo del producto en la farmacia.")
    revisar_presentacion = fields.Boolean(
        "Revisar presentación", readonly=True,
        help="El precio de la droguería pasa del triple del costo: puede ser otro empaque "
             "con el mismo código de barras (p. ej. un dispensador en vez de un blíster).")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE VIEW ai_v_faltantes AS
            WITH params AS (
                SELECT %(cobertura)s AS semanas, %(excedente)s AS semanas_excedente
            ),
            estado AS (
                -- Todo producto con venta o con stock en alguna farmacia.
                SELECT COALESCE(r.company_id, s.company_id) AS company_id,
                       COALESCE(r.product_id, s.product_id) AS product_id,
                       COALESCE(r.demanda_depurada, 0) AS demanda,
                       greatest(COALESCE(s.stock, 0), 0) AS stock,
                       COALESCE(p.pendiente, 0) AS pendiente,
                       COALESCE(p.atrasado, 0) AS atrasado,
                       COALESCE(p.en_solicitud, 0) AS en_solicitud,
                       COALESCE(r.demanda_depurada, 0) * params.semanas AS necesidad,
                       COALESCE(r.demanda_depurada, 0)
                           * greatest(params.semanas_excedente, params.semanas) AS reserva
                  FROM ai_v_rotacion r
                  FULL JOIN ai_v_stock s ON s.company_id = r.company_id AND s.product_id = r.product_id
                  LEFT JOIN ai_v_pendiente p ON p.company_id = COALESCE(r.company_id, s.company_id)
                                            AND p.product_id = COALESCE(r.product_id, s.product_id)
                 CROSS JOIN params
            ),
            faltantes AS (
                SELECT e.*, e.necesidad - e.stock - e.pendiente AS faltante,
                       ceil(e.necesidad - e.stock - e.pendiente)::int AS cantidad
                  FROM estado e
                 WHERE e.demanda > 0 AND e.necesidad - e.stock - e.pendiente > 0
            ),
            excedentes AS (
                SELECT e.company_id, e.product_id,
                       floor(least(e.stock, e.stock + e.pendiente - e.reserva)) AS excedente
                  FROM estado e
                 WHERE floor(least(e.stock, e.stock + e.pendiente - e.reserva)) >= 1
            ),
            otras AS (
                SELECT f.company_id, f.product_id,
                       sum(x.excedente) AS excedente,
                       string_agg(c.name || ' (' || x.excedente::int || ')', ', '
                                  ORDER BY x.excedente DESC, c.name) AS farmacias
                  FROM faltantes f
                  JOIN excedentes x ON x.product_id = f.product_id AND x.company_id <> f.company_id
                  JOIN res_company c ON c.id = x.company_id
                 GROUP BY 1, 2
            ),
            costos AS (
                -- standard_price depende de la empresa: está en ir_property.
                SELECT f.company_id, f.product_id, nullif(ip.value_float, 0) AS costo
                  FROM faltantes f
                  JOIN ir_property ip ON ip.name = 'standard_price'
                                     AND ip.res_id = 'product.product,' || f.product_id
                                     AND ip.company_id = f.company_id
            ),
            ofertas AS (
                SELECT f.company_id, f.product_id, o.partner_id, o.drogueria, o.precio_final,
                       o.descuento_pct, o.stock_drogueria, o.fecha_vencimiento, o.laboratorio,
                       o.stock_drogueria >= f.cantidad AS suficiente,
                       o.precio_final > 3 * k.costo AS revisar,
                       o.mejor_precio, o.droguerias_con_stock AS droguerias,
                       row_number() OVER (PARTITION BY f.company_id, f.product_id
                                          ORDER BY o.precio_final > 3 * k.costo IS TRUE,
                                                   o.stock_drogueria >= f.cantidad DESC,
                                                   o.precio_final, o.partner_id, o.id) AS puesto
                  FROM faltantes f
                  JOIN ai_v_oferta o ON o.product_id = f.product_id AND o.disponible
                  LEFT JOIN costos k ON k.company_id = f.company_id AND k.product_id = f.product_id
            )
            SELECT row_number() OVER (ORDER BY f.company_id, f.product_id) AS id,
                   f.company_id,
                   c.name AS farmacia,
                   f.product_id,
                   pp.product_tmpl_id,
                   COALESCE(pt.name->>'es_VE', pt.name->>'en_US') AS producto,
                   pp.default_code AS codigo,
                   round(f.demanda::numeric, 2) AS demanda_semanal,
                   params.semanas AS semanas_cobertura,
                   round(f.necesidad::numeric, 2) AS necesidad,
                   round(f.stock::numeric, 2) AS stock,
                   round(f.pendiente::numeric, 2) AS pendiente,
                   round(f.atrasado::numeric, 2) AS atrasado,
                   round(f.en_solicitud::numeric, 2) AS en_solicitud,
                   round(((f.stock + f.pendiente) / f.demanda)::numeric, 2) AS cobertura_actual,
                   round(f.faltante::numeric, 2) AS faltante,
                   f.cantidad AS cantidad_sugerida,
                   round(COALESCE(x.excedente, 0)::numeric, 2) AS excedente_otras,
                   x.farmacias AS farmacias_con_excedente,
                   o.partner_id AS drogueria_id,
                   o.drogueria,
                   o.precio_final AS precio,
                   o.descuento_pct AS descuento,
                   o.mejor_precio,
                   round((f.cantidad * o.precio_final)::numeric, 2) AS monto,
                   o.stock_drogueria,
                   o.suficiente AS stock_drogueria_suficiente,
                   COALESCE(o.droguerias, 0) AS droguerias_con_oferta,
                   o.fecha_vencimiento,
                   o.laboratorio,
                   round(k.costo::numeric, 2) AS costo,
                   COALESCE(o.revisar, false) AS revisar_presentacion
              FROM faltantes f
              JOIN res_company c ON c.id = f.company_id
              JOIN product_product pp ON pp.id = f.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
              LEFT JOIN otras x ON x.company_id = f.company_id AND x.product_id = f.product_id
              LEFT JOIN costos k ON k.company_id = f.company_id AND k.product_id = f.product_id
              LEFT JOIN ofertas o ON o.company_id = f.company_id AND o.product_id = f.product_id
                                 AND o.puesto = 1
             CROSS JOIN params
             WHERE pp.active AND pt.purchase_ok
        """ % {
            "cobertura": _int_param("coverage_weeks", DEFAULT_COVERAGE_WEEKS, 52),
            "excedente": _int_param("excess_weeks", DEFAULT_EXCESS_WEEKS, 52),
        })
        grant_readonly(self.env, self._table)
