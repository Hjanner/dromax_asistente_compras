# -*- coding: utf-8 -*-
"""Ejecución de SQL de solo lectura con el rol dedicado (dromax_ai_ro).

La conexión es propia, con psycopg2, y no comparte nada con el cursor de Odoo:
el agente nunca escribe por aquí aunque el validador fallara, porque

  1. el rol solo tiene SELECT sobre la lista blanca y
     default_transaction_read_only = on;
  2. la sesión se abre además en modo readonly desde el cliente;
  3. statement_timeout y el tope de filas acotan lo que puede costar.

Configuración (ir.config_parameter > opción en odoo.conf > variable de entorno):
  dromax_asistente_compras.ro_user      / ai_purchase_ro_user      / AI_PURCHASE_RO_USER
  dromax_asistente_compras.ro_password  / ai_purchase_ro_password  / AI_PURCHASE_RO_PASSWORD
  dromax_asistente_compras.sql_whitelist        (opcional, nombres separados por comas)
  dromax_asistente_compras.sql_max_rows         (opcional, defecto 200)
  dromax_asistente_compras.sql_timeout_seconds  (opcional, defecto 30)
Host, puerto y base de datos son los de la propia instancia de Odoo.
"""
import datetime
import decimal
import logging
import time
import uuid

import psycopg2
import psycopg2.extensions

from odoo.tools import config as odoo_config

from .config import PARAM_PREFIX, get_int_setting, get_setting
from .sql_validator import DEFAULT_WHITELIST, SqlValidationError, validate_select

_logger = logging.getLogger(__name__)

DEFAULT_MAX_ROWS = 200
HARD_MAX_ROWS = 2000
DEFAULT_TIMEOUT_SECONDS = 30
# Texto largo (descripciones HTML, notas) no aporta al análisis y gasta tokens.
MAX_CELL_CHARS = 500


class ReadonlySqlError(Exception):
    """Fallo al ejecutar (configuración, permisos, timeout, error de SQL)."""


def get_whitelist(env):
    raw = get_setting(env, "sql_whitelist")
    if not raw:
        return DEFAULT_WHITELIST
    return frozenset(t.strip().lower() for t in raw.split(",") if t.strip())


def _connect(env):
    user = get_setting(env, "ro_user")
    password = get_setting(env, "ro_password")
    if not user:
        raise ReadonlySqlError(
            "Falta configurar el usuario de solo lectura (parámetro %sro_user)." % PARAM_PREFIX)
    if user == odoo_config.get("db_user"):
        # Con el usuario de Odoo se perdería la segunda barrera.
        raise ReadonlySqlError(
            "El usuario de solo lectura no puede ser el mismo con el que se conecta Odoo.")
    params = {
        "dbname": env.cr.dbname,
        "user": user,
        "password": password or None,
        "host": odoo_config.get("db_host") or None,
        "port": odoo_config.get("db_port") or None,
        "application_name": "dromax_asistente_compras",
        "connect_timeout": 10,
    }
    try:
        conn = psycopg2.connect(**{k: v for k, v in params.items() if v is not None})
    except psycopg2.Error as exc:
        _logger.warning("No se pudo conectar con el rol de solo lectura: %s", exc)
        raise ReadonlySqlError("No se pudo conectar con el usuario de solo lectura.") from exc
    conn.set_session(readonly=True, autocommit=False)
    return conn


def _json_value(value):
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, memoryview):
        return "<binario>"
    if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
        return value[:MAX_CELL_CHARS] + "…"
    return value


def _check_cte_shadowing(cr, ctes, whitelist):
    """Una CTE con el nombre de una tabla real fuera de la lista blanca haría
    que el validador diera por buena una referencia a esa tabla en otro ámbito
    de la consulta. El rol lo pararía igualmente; aquí se corta antes."""
    for name in ctes:
        if name in whitelist:
            continue
        cr.execute("SELECT to_regclass(%s) IS NOT NULL", (psycopg2.extensions.quote_ident(name, cr),))
        if cr.fetchone()[0]:
            raise SqlValidationError(
                "La CTE '%s' tiene el nombre de una tabla existente; usa otro nombre." % name)


def run_readonly_sql(env, query, max_rows=None):
    """Valida y ejecuta `query`. Devuelve un dict serializable a JSON:

        {'columns': [...], 'rows': [[...], ...], 'row_count': n,
         'truncated': bool, 'elapsed_ms': n}

    Lanza SqlValidationError si la consulta no pasa el validador y
    ReadonlySqlError si falla al ejecutarse.
    """
    whitelist = get_whitelist(env)
    checked = validate_select(query, whitelist)
    limit = get_int_setting(env, "sql_max_rows", DEFAULT_MAX_ROWS, HARD_MAX_ROWS)
    if max_rows:
        limit = max(1, min(int(max_rows), limit))
    timeout_ms = get_int_setting(env, "sql_timeout_seconds", DEFAULT_TIMEOUT_SECONDS, 300) * 1000

    started = time.monotonic()
    conn = _connect(env)
    try:
        with conn.cursor() as cr:
            # SET LOCAL: solo dura esta transacción. El rol ya trae su propio
            # statement_timeout; este es el del módulo, configurable.
            cr.execute("SET LOCAL statement_timeout = %s", (timeout_ms,))
            cr.execute("SET LOCAL search_path = public")
            _check_cte_shadowing(cr, checked["ctes"], whitelist)
        # Cursor con nombre (del lado del servidor): se traen limit+1 filas y
        # no el resultado entero, así una consulta enorme no llena la memoria.
        with conn.cursor(name="ai_ro_%s" % uuid.uuid4().hex) as cr:
            cr.itersize = limit + 1
            cr.execute(checked["sql"])
            rows = cr.fetchmany(limit + 1)
            columns = [d[0] for d in cr.description] if cr.description else []
    except psycopg2.errors.QueryCanceled as exc:
        raise ReadonlySqlError(
            "La consulta superó el tiempo máximo (%d s); simplifícala o filtra más."
            % (timeout_ms // 1000)) from exc
    except psycopg2.errors.InsufficientPrivilege as exc:
        raise ReadonlySqlError("Sin permiso de lectura: %s" % exc.diag.message_primary) from exc
    except psycopg2.Error as exc:
        msg = exc.diag.message_primary if exc.diag and exc.diag.message_primary else str(exc)
        raise ReadonlySqlError("Error de SQL: %s" % msg) from exc
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()

    truncated = len(rows) > limit
    rows = rows[:limit]
    elapsed_ms = int((time.monotonic() - started) * 1000)
    _logger.info(
        "run_readonly_sql uid=%s tablas=%s filas=%s%s %sms",
        env.uid, ",".join(sorted(checked["tables"])) or "-", len(rows),
        "+" if truncated else "", elapsed_ms)
    return {
        "columns": columns,
        "rows": [[_json_value(v) for v in row] for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": elapsed_ms,
    }
