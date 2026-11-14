# -*- coding: utf-8 -*-
from odoo import fields, models


class StockPicking(models.Model):
    """Marca de los borradores que crea la tarea diaria de reabastecimiento.

    El botón de la pantalla Reabastecimiento y la tarea ponen el mismo origen
    («Reabastecimiento <fecha>»); solo los de la tarea llevan esta fecha. Al
    día siguiente, la tarea cancela los suyos que siguen en borrador antes de
    crear los nuevos (ai.v.reabastecimiento._cancel_stale_auto_transfers);
    los creados a mano no se tocan.
    """
    _inherit = "stock.picking"

    ai_auto_replenishment_date = fields.Date(
        "Reabastecimiento automático del", copy=False, readonly=True, index="btree_not_null",
        help="Día en que lo creó la tarea diaria de reabastecimiento del Agente IA. Si al día "
             "siguiente sigue en borrador, la tarea lo cancela y crea uno nuevo con las "
             "cantidades del día.")
