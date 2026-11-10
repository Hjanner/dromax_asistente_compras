# -*- coding: utf-8 -*-
"""Preguntas predefinidas del chat.

Son el disparador habitual del agente: el comprador pulsa un botón y se envía
un prompt fijo. Cada una fija su nivel de modelo; la de armar la compra lleva
además la tarea purchase_proposal, que la manda siempre al nivel avanzado, y
ensure_proposal: calcula la propuesta de la semana si todavía no existe.

Las que llevan "input" piden un dato antes de enviarse (p. ej. el laboratorio):
la interfaz lo pide en el cuadro de texto y el servidor lo pone en {input}.
"""

PRESETS = [
    {
        "key": "compra_semana",
        "label": "Arma la compra de esta semana",
        "level": "advanced",
        "task": "purchase_proposal",
        "ensure_proposal": True,
        "prompt": (
            "Muéstrame la propuesta de compra de esta semana con su resumen (get_proposal sin "
            "filtros; no hace falta consultar nada más): monto, líneas y farmacias, los "
            "laboratorios de mayor monto y lo que necesita mi atención antes de generar los "
            "borradores: productos a revisar por presentación y productos sin droguería. "
            "Sé breve y termina diciéndome cómo seguir."
        ),
    },
    {
        "key": "revisar_laboratorio",
        "label": "Revisa un laboratorio",
        "level": "advanced",
        "task": "lab_review",
        "ensure_proposal": True,
        "input": "Laboratorio, p. ej. LETI",
        "prompt": (
            "Revisa el laboratorio «{input}» en la propuesta de compra de esta semana "
            "(get_proposal con ese laboratorio). Muestra sus líneas, cuánto le falta a cada "
            "farmacia y el total. Señala las que hay que revisar por presentación, las que no "
            "tienen droguería, las que ya están en solicitudes sin "
            "confirmar y las que tienen otra droguería más barata o con más stock. Si el "
            "nombre no coincide con ningún laboratorio, dime los parecidos. No cambies nada: "
            "sugiere y espera mi respuesta."
        ),
    },
    {
        "key": "reabastecer",
        "label": "¿A quién abastezco desde Dromax?",
        "level": "medium",
        "task": "replenishment",
        "prompt": (
            "¿Qué tiene que mandar la sucursal matriz a cada farmacia? Consulta "
            "ai_v_reabastecimiento: total por farmacia (líneas, unidades a trasladar y valor), "
            "separando «En cero» de «Por agotarse», y los 10 productos de mayor valor en cero. "
            "Di cuántas líneas son parciales porque la matriz no alcanza. Calcula los totales "
            "(por farmacia y el general) en la misma consulta SQL, con GROUP BY ROLLUP, y "
            "cópialos tal cual: no sumes tú ni inventes cantidades. Termina diciendo que los borradores se generan "
            "en Compras IA → Reabastecimiento, eligiendo las líneas."
        ),
    },
    {
        "key": "por_agotarse",
        "label": "Productos por agotarse",
        "level": "medium",
        "task": "stockout_check",
        "prompt": (
            "¿Qué productos se van a agotar antes del próximo reabastecimiento en cada "
            "farmacia? Compara la demanda semanal con el stock actual y lo pendiente de recibir."
        ),
    },
    {
        "key": "mas_vendidos",
        "label": "Más vendidos de las últimas 4 semanas",
        "level": "basic",
        "task": "top_sellers",
        "prompt": (
            "Dame los 20 productos más vendidos en unidades de las últimas 4 semanas, "
            "sumando TPV y pedidos de venta, con el total por farmacia."
        ),
    },
    {
        "key": "pendientes",
        "label": "Compras pendientes de recibir",
        "level": "basic",
        "task": "pending_purchases",
        "prompt": (
            "¿Qué compras confirmadas siguen pendientes de recibir? Muestra producto, "
            "cantidad pendiente, orden y fecha prevista de llegada."
        ),
    },
    {
        "key": "comparar_droguerias",
        "label": "Compara precios entre droguerías",
        "level": "medium",
        "task": "supplier_comparison",
        "prompt": (
            "Compara la oferta vigente de las droguerías con ai_v_comparador: de los productos "
            "con 2 o más droguerías con stock y sin precios_dispares, muestra los 20 con mayor "
            "ahorro_pct, con la droguería más barata, sus descuentos, su stock, el vencimiento "
            "del lote y si está en nuestro catálogo; marca los que vencen pronto o tienen poco "
            "stock. Di aparte cuántos tienen precios_dispares (probable otra presentación). Al "
            "final, cuántos productos del comparador están en nuestro catálogo y cuántos no. "
            "Calcula los conteos en SQL, no los sumes tú."
        ),
    },
    {
        "key": "sin_rotacion",
        "label": "Stock sin rotación",
        "level": "medium",
        "task": "overstock_check",
        "prompt": (
            "¿Qué productos tienen stock pero no se han vendido en las últimas 8 semanas? "
            "Ordénalos por unidades en existencia, para detectar sobre-stock."
        ),
    },
]

_BY_KEY = {p["key"]: p for p in PRESETS}


def get_preset(key):
    return _BY_KEY.get(key)


def public_presets():
    """Lo que necesita la interfaz (sin el prompt completo)."""
    return [{"key": p["key"], "label": p["label"], "level": p["level"], "input": p.get("input") or False}
            for p in PRESETS]
