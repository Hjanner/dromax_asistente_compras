# -*- coding: utf-8 -*-
"""El módulo pasa a llamarse «Agente IA» en la interfaz.

La tarea programada está en un bloque noupdate (para no pisar la hora o la
frecuencia que se cambien en producción), así que la actualización no le
cambia el nombre: se renombra aquí, solo si conserva el nombre anterior.
"""
from odoo import SUPERUSER_ID, api

OLD = "Compras IA: propuesta de compra semanal"
NEW = "Agente IA: propuesta de compra semanal"


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref("dromax_asistente_compras.cron_ai_purchase_proposal", raise_if_not_found=False)
    if cron and cron.name == OLD:
        cron.name = NEW
