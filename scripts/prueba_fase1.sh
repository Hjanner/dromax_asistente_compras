#!/usr/bin/env bash
# Lanza scripts/prueba_fase1.py con el odoo shell, sin tener que copiar el
# comando largo. Uso, desde cualquier carpeta:
#
#   bash custom_addons/dromax_asistente_compras/scripts/prueba_fase1.sh [BASE]
#
# BASE: base de datos (defecto DROMAX_AI_DEV). Admite NIVEL y PREGUNTA:
#   NIVEL=advanced PREGUNTA="¿Qué almacenes hay?" bash .../prueba_fase1.sh
#
# Rutas por defecto del entorno de desarrollo; se cambian con ODOO_DEV,
# ODOO_PYTHON y ODOO_CONF.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODOO_DEV="${ODOO_DEV:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
ODOO_PYTHON="${ODOO_PYTHON:-$ODOO_DEV/env/bin/python3}"
ODOO_CONF="${ODOO_CONF:-$ODOO_DEV/odoo/debian/odoo.conf}"
DB="${1:-DROMAX_AI_DEV}"

# -W ignore::SyntaxWarning: Python 3.13 avisa al leer los manifiestos de
# algunos módulos enterprise y Odoo imprime cada aviso con su traza completa.
exec "$ODOO_PYTHON" -W ignore::SyntaxWarning "$ODOO_DEV/odoo/odoo-bin" shell \
    -c "$ODOO_CONF" -d "$DB" --no-http --log-level=error \
    < "$SCRIPT_DIR/prueba_fase1.py"
