# -*- coding: utf-8 -*-
{
    'name': "Dromax - Asistente de Compras IA",
    'summary': "Propuesta de compra semanal por laboratorio, ajustable en el chat "
               "y convertible en solicitudes de presupuesto en borrador.",
    'description': """
Agente de Compras IA para Dromax.

- Flujo cotidiano: un cron precalcula los faltantes el lunes en la madrugada
  y deja una propuesta por laboratorio (ai.purchase.proposal).
- El comprador la revisa y ajusta en lenguaje natural desde el chat.
- Un botón (nunca el modelo) la convierte en purchase.order en borrador,
  una por droguería dentro de cada laboratorio.
- Flujo dinámico separado: la misma propuesta con contexto externo
  (clima, brotes, epidemias).

El agente lee Postgres solo con SQL validado (SELECT sobre lista blanca) y un
rol de solo lectura. La única escritura es la orden de compra en borrador.
    """,
    'author': "Hjanner",
    'category': 'Inventory/Purchase',
    'version': '17.0.0.1.0',
    'license': 'LGPL-3',
    'application': True,
    'installable': True,
    'depends': [
        'base',
        'purchase',
        'stock',
    ],
    'data': [
        'security/ai_purchase_groups.xml',
    ],
}
