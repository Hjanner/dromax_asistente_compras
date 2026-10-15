# -*- coding: utf-8 -*-
"""Prueba manual de la Fase 1, contra la base y OpenRouter de verdad.

Uso: bash scripts/prueba_fase1.sh (lanza este archivo con el comando de abajo).

Comando completo, desde ~/odoo-dev con el venv activado:

    python3 -W ignore::SyntaxWarning odoo/odoo-bin shell -c odoo/debian/odoo.conf \
        -d DROMAX_AI_DEV --no-http --log-level=error \
        < custom_addons/dromax_asistente_compras/scripts/prueba_fase1.py

`-W ignore::SyntaxWarning` silencia los avisos que Python 3.13 da al leer los
manifiestos de algunos módulos enterprise (account_bank_statement_import_*):
Odoo los imprime con la traza completa y tapan la salida de la prueba.

Variables opcionales: NIVEL=basic|medium|advanced (defecto basic, el más barato)
y PREGUNTA="..." para cambiar la pregunta al modelo.

Gasta crédito de OpenRouter: con el nivel básico, una fracción de centavo.
Las llamadas quedan en Compras IA → Configuración → Consumo de IA.
"""
import json
import os

from odoo.addons.dromax_asistente_compras.services.openrouter import OpenRouterClient, OpenRouterError
from odoo.addons.dromax_asistente_compras.services.tools import call_tool, tool_definitions

NIVEL = os.environ.get("NIVEL", "basic")
PREGUNTA = os.environ.get(
    "PREGUNTA", "¿Cuántos productos hay registrados y cuáles son sus nombres?")


def titulo(texto):
    print("\n=== %s ===" % texto)


# El script corre como el superusuario; las herramientas exigen el grupo, así
# que se usa el primer administrador que lo tenga.
user = env["res.users"].search([
    ("groups_id", "in", env.ref("dromax_asistente_compras.group_ai_purchase_user").id),
], limit=1)
if not user:
    raise SystemExit("Ningún usuario tiene el grupo Asistente de Compras IA / Usuario.")
uenv = env(user=user)
print("Usuario:", user.login, "| nivel:", NIVEL)

titulo("1. Consulta válida con run_readonly_sql")
print(json.dumps(call_tool(uenv, "run_readonly_sql", {
    "query": "SELECT count(*) AS productos FROM product_template"}), ensure_ascii=False))

titulo("2. Consultas que deben rechazarse")
for q in ("DELETE FROM sale_order",
          "SELECT login, password FROM res_users",
          "SELECT pg_sleep(60)",
          "SELECT 1; DROP TABLE sale_order"):
    print("%-40s -> %s" % (q, call_tool(uenv, "run_readonly_sql", {"query": q}).get("error")))

titulo("3. Pregunta al modelo con la herramienta SQL")
client = OpenRouterClient(uenv)
messages = [
    {"role": "system", "content": (
        "Eres el asistente de compras de una droguería. Responde en español, breve. "
        "Para datos usa la herramienta run_readonly_sql (PostgreSQL de Odoo 17; "
        "product_template.name es JSONB con traducciones, usa name->>'es_VE' o name->>'en_US').")},
    {"role": "user", "content": PREGUNTA},
]
tools = tool_definitions(uenv)
try:
    for paso in range(4):
        data = client.chat(messages, level=NIVEL, tools=tools, task="prueba_fase1")
        msg = data["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            print("Respuesta del modelo (%s):\n%s" % (data.get("model"), msg.get("content")))
            break
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for call in calls:
            args = json.loads(call["function"].get("arguments") or "{}")
            print("El modelo consulta:", args.get("query"))
            result = call_tool(uenv, call["function"]["name"], args)
            print("  ->", "error: " + result["error"] if "error" in result
                  else "%s filas" % result["row_count"])
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, ensure_ascii=False)})
    else:
        print("El modelo no terminó en 4 pasos.")
except OpenRouterError as exc:
    print("ERROR de OpenRouter:", exc)

titulo("4. Consumo registrado en esta prueba")
# El cliente guarda el consumo en su propia transacción; esta sesión no lo ve
# hasta abrir un cursor nuevo.
with env.registry.cursor() as cr:
    for log in env(cr=cr)["ai.usage.log"].search([("task", "=", "prueba_fase1")], limit=5):
        print("%s | %s | %s | entrada %s / salida %s tokens | %.6f USD | %s ms | %s" % (
            log.create_date, log.level, log.model_used or log.model, log.prompt_tokens,
            log.completion_tokens, log.cost, log.duration_ms, log.state))
