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
    'version': '17.0.0.11.0',
    'license': 'LGPL-3',
    'application': True,
    'installable': True,
    'depends': [
        'base',
        'purchase_stock',
        # Ventas del TPV (pos_sale: liquidación de pedidos de venta en el TPV) y pedidos de venta.
        'pos_sale',
        # Oferta de las droguerías (tabla supplier_product_staging) cargada por FTP.
        'advanced_interbranch_transfer',
    ],
    'data': [
        'security/ai_purchase_groups.xml',
        'security/ir.model.access.csv',
        'security/ai_purchase_security.xml',
        'data/ir_config_parameter.xml',
        'data/ir_cron.xml',
        'views/ai_usage_log_views.xml',
        'views/res_config_settings_views.xml',
        'views/ai_purchase_chat_views.xml',
        'views/ai_v_oferta_views.xml',
        'views/ai_barcode_mismatch_views.xml',
        'views/ai_v_analisis_views.xml',
        'views/ai_purchase_proposal_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'dromax_asistente_compras/static/src/chat/*.js',
            'dromax_asistente_compras/static/src/chat/*.xml',
            'dromax_asistente_compras/static/src/chat/*.scss',
        ],
    },
}
