# -*- coding: utf-8 -*-
"""Marca los borradores de traslado que ya había creado la tarea diaria.

Desde esta versión la tarea cancela sus borradores de días anteriores que
siguen sin despachar; los reconoce por ai_auto_replenishment_date. Los que
creó antes de existir el campo se reconocen por el mensaje que la tarea deja
en su historial («… (reabastecimiento diario).»). Los creados con el botón o
a mano no tienen ese mensaje y quedan sin marca.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE stock_picking p
           SET ai_auto_replenishment_date = (p.create_date AT TIME ZONE 'UTC'
                                             AT TIME ZONE 'America/Caracas')::date
         WHERE p.ai_auto_replenishment_date IS NULL
           AND EXISTS (SELECT 1 FROM mail_message m
                        WHERE m.model = 'stock.picking' AND m.res_id = p.id
                          AND m.body ILIKE '%(reabastecimiento diario)%')
    """)
