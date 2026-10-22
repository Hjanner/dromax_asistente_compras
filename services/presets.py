# -*- coding: utf-8 -*-
"""Preguntas predefinidas del chat.

Son el disparador habitual del agente: el comprador pulsa un botón y se envía
un prompt fijo. Cada una fija su nivel de modelo; la de armar la compra lleva
además la tarea purchase_proposal, que la manda siempre al nivel avanzado.
"""

PRESETS = [
    {
        "key": "compra_semana",
        "label": "Arma la compra de esta semana",
        "level": "advanced",
        "task": "purchase_proposal",
        "prompt": (
            "Arma la propuesta de compra de esta semana para cada farmacia: los productos "
            "cuya demanda depurada de la próxima semana supera el stock actual más lo "
            "pendiente de recibir. Aplica las reglas contra el sobre-stock y ordena por "
            "farmacia y cantidad sugerida."
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
            "Compara la oferta vigente de las droguerías: de los productos disponibles "
            "en más de una droguería, muestra los 20 con mayor ahorro porcentual del mejor "
            "precio final frente al segundo, con la droguería más barata, su descuento, su "
            "stock, el vencimiento del lote y si el producto está en nuestro catálogo. "
            "Deja fuera los casos en que el segundo precio supera el triple del mejor "
            "(probable presentación distinta) y di solo cuántos son. Al final, di cuántos "
            "de nuestros productos aparecen en la oferta y cuántos no."
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
    return [{"key": p["key"], "label": p["label"], "level": p["level"]} for p in PRESETS]
