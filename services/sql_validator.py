# -*- coding: utf-8 -*-
"""Validador del SQL que genera el modelo.

Primera barrera antes de tocar Postgres. La segunda es el rol de solo lectura
(`dromax_ai_ro`), que solo tiene SELECT sobre la lista blanca: si algo se cuela
por aquí, Postgres lo rechaza igualmente.

Se acepta únicamente:
  - una sola sentencia, que empiece por SELECT o WITH;
  - sin palabras de escritura, DDL, bloqueo ni control de transacción;
  - sin funciones de sistema (pg_*, lo_*, dblink, *_to_xml, set_config...);
  - leyendo solo tablas/vistas de la lista blanca o CTE definidas en la
    propia consulta, del esquema public.

No es un parser de SQL completo: es un analizador léxico que entiende lo
suficiente (comentarios, literales, identificadores entre comillas,
paréntesis) para que esas reglas no se puedan esquivar escondiendo palabras
en comentarios o cadenas. Ante la duda, rechaza.

Python puro, sin dependencias de Odoo: se prueba de forma aislada.
"""
import re

# Lista blanca por defecto. Se puede sustituir con el parámetro de sistema
# 'dromax_asistente_compras.sql_whitelist' (nombres separados por comas).
# res_partner NO está a propósito: mezcla droguerías con clientes finales, y a
# OpenRouter no deben salir datos de clientes. Las droguerías se expondrán con
# una vista propia.
DEFAULT_WHITELIST = frozenset({
    # Vistas de la capa de datos del módulo
    "ai_v_rotacion", "ai_v_stock", "ai_v_pendiente", "ai_v_faltantes", "ai_v_oferta",
    # Ventas
    "sale_order", "sale_order_line", "pos_order", "pos_order_line",
    # Compras
    "purchase_order", "purchase_order_line",
    # Inventario
    "stock_quant", "stock_location", "stock_warehouse",
    # Productos
    "product_product", "product_template", "product_category", "uom_uom",
    # Empresas: cada farmacia es una sucursal (empresa hija). Sin datos de clientes.
    "res_company",
})

# Palabras que no tienen cabida en una consulta de lectura. Se comparan como
# palabras sueltas fuera de literales y comentarios: `date_update` o
# 'pendiente de update' no las activan.
FORBIDDEN_KEYWORDS = frozenset({
    # Escritura (también como CTE: WITH x AS (DELETE ... RETURNING ...))
    "insert", "update", "delete", "merge", "upsert", "truncate", "copy",
    # SELECT ... INTO crea una tabla
    "into",
    # DDL y permisos
    "create", "alter", "drop", "rename", "grant", "revoke", "comment",
    "reindex", "cluster", "vacuum", "refresh", "security", "owner",
    # Ejecución y sesión
    "call", "do", "execute", "prepare", "deallocate", "set", "reset",
    "discard", "load", "listen", "notify", "unlisten", "checkpoint",
    # Transacción y bloqueo (FOR UPDATE ya cae por 'update')
    "begin", "commit", "rollback", "savepoint", "release", "lock", "share",
    "nowait",
    # `TABLE x` es un SELECT * FROM x camuflado que esquivaría la lista blanca
    "table",
})

# Esquemas que no se pueden nombrar en ningún sitio.
FORBIDDEN_SCHEMAS = frozenset({"pg_catalog", "information_schema", "pg_toast"})

# Funciones de sistema o que ejecutan SQL arbitrario desde una cadena
# (query_to_xml('select * from res_users', ...) saltaría la lista blanca).
_FORBIDDEN_FUNC_RE = re.compile(
    r"^(pg_|lo_|dblink|txid_|query_to_|cursor_to_|table_to_|schema_to_|database_to_)"
    r"|_to_xml|xmlschema$"
    r"|^(set_config|current_setting|nextval|setval|currval|lastval|version|inet_server_addr"
    r"|inet_server_port|inet_client_addr|current_user|session_user|current_database)$"
)

# Palabras que pueden seguir a una tabla y que NO son su alias.
_NOT_ALIAS = frozenset({
    "where", "join", "inner", "left", "right", "full", "cross", "natural", "on",
    "using", "group", "order", "having", "limit", "offset", "fetch", "union",
    "intersect", "except", "window", "lateral", "tablesample", "for", "returning",
})

MAX_QUERY_LENGTH = 20000


class SqlValidationError(ValueError):
    """Consulta rechazada. El mensaje va dirigido al modelo para que corrija."""


class _Tok:
    __slots__ = ("kind", "value", "pos", "end")

    # kind: word (palabra sin comillas, en minúsculas), ident ("entre comillas"),
    # string, number, punct. pos/end: tramo que ocupa en el SQL original.
    def __init__(self, kind, value, pos, end):
        self.kind, self.value, self.pos, self.end = kind, value, pos, end

    def is_word(self, *values):
        return self.kind == "word" and (not values or self.value in values)

    def is_punct(self, value):
        return self.kind == "punct" and self.value == value

    def __repr__(self):
        return "%s:%s" % (self.kind, self.value)


_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_NUM_RE = re.compile(r"(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")
_DOLLAR_TAG_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")


def tokenize(sql):
    """Convierte el SQL en tokens, descartando comentarios.

    Lanza SqlValidationError ante comentarios, cadenas o identificadores sin
    cerrar: lo que no se entiende entero no se ejecuta.
    """
    toks = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c.isspace():
            i += 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j + 1
        elif sql.startswith("/*", i):
            # Postgres admite comentarios anidados.
            depth, j = 1, i + 2
            while j < n and depth:
                if sql.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif sql.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            if depth:
                raise SqlValidationError("Comentario /* sin cerrar.")
            i = j
        elif c == "'" or (c in "eEbBxXnN" and sql.startswith("'", i + 1)) \
                or (c in "uU" and sql.startswith("&'", i + 1)):
            start = i
            backslash = c in "eE"
            i = sql.index("'", i) + 1
            while True:
                if i >= n:
                    raise SqlValidationError("Cadena de texto sin cerrar.")
                if backslash and sql[i] == "\\":
                    i += 2
                    continue
                if sql[i] == "'":
                    if sql.startswith("''", i):
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            toks.append(_Tok("string", sql[start:i], start, i))
        elif c == "$" and _DOLLAR_TAG_RE.match(sql, i):
            tag = _DOLLAR_TAG_RE.match(sql, i).group(0)
            j = sql.find(tag, i + len(tag))
            if j < 0:
                raise SqlValidationError("Cadena con $$ sin cerrar.")
            toks.append(_Tok("string", sql[i:j + len(tag)], i, j + len(tag)))
            i = j + len(tag)
        elif c == '"' or (c in "uU" and sql.startswith('&"', i + 1)):
            start = i
            i = sql.index('"', i) + 1
            buf = []
            while True:
                if i >= n:
                    raise SqlValidationError("Identificador entre comillas sin cerrar.")
                if sql[i] == '"':
                    if sql.startswith('""', i):
                        buf.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(sql[i])
                i += 1
            toks.append(_Tok("ident", "".join(buf), start, i))
        elif _WORD_RE.match(sql, i):
            m = _WORD_RE.match(sql, i)
            toks.append(_Tok("word", m.group(0).lower(), i, m.end()))
            i = m.end()
        elif c.isdigit() or (c == "." and i + 1 < n and sql[i + 1].isdigit()):
            m = _NUM_RE.match(sql, i)
            toks.append(_Tok("number", m.group(0), i, m.end()))
            i = m.end()
        else:
            # Operadores y puntuación: basta con un carácter por token.
            toks.append(_Tok("punct", c, i, i + 1))
            i += 1
    return toks


def _is_name(tok):
    return tok is not None and tok.kind in ("word", "ident")


def _matching_paren(toks, i):
    """Índice del ')' que cierra el '(' en la posición i."""
    depth = 0
    for j in range(i, len(toks)):
        if toks[j].is_punct("("):
            depth += 1
        elif toks[j].is_punct(")"):
            depth -= 1
            if depth == 0:
                return j
    raise SqlValidationError("Paréntesis sin cerrar.")


def _collect_ctes(toks):
    """Nombres de las CTE: WITH [RECURSIVE] nombre [(cols)] AS [NOT] [MATERIALIZED] (...)"""
    names = set()
    for i, tok in enumerate(toks):
        if not tok.is_word("with"):
            continue
        j = i + 1
        if j < len(toks) and toks[j].is_word("recursive"):
            j += 1
        while j < len(toks) and _is_name(toks[j]):
            name = toks[j].value
            j += 1
            if j < len(toks) and toks[j].is_punct("("):
                j = _matching_paren(toks, j) + 1
            if not (j < len(toks) and toks[j].is_word("as")):
                break  # WITH ORDINALITY, WITH TIES... no es una CTE
            j += 1
            if j < len(toks) and toks[j].is_word("not"):
                j += 1
            if j < len(toks) and toks[j].is_word("materialized"):
                j += 1
            if not (j < len(toks) and toks[j].is_punct("(")):
                break
            names.add(name)
            j = _matching_paren(toks, j) + 1
            if j < len(toks) and toks[j].is_punct(","):
                j += 1
                continue
            break
    return names


def _paren_kinds(toks):
    """Para cada token, si está dentro de un paréntesis de subconsulta o de
    otro tipo (llamada a función, lista...).

    Hace falta para distinguir el FROM de una consulta del de
    EXTRACT(year FROM fecha) o substring(x FROM 1).
    """
    kinds, stack = [], []
    for i, tok in enumerate(toks):
        kinds.append(stack[-1] if stack else "query")
        if tok.is_punct("("):
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            stack.append("query" if nxt is not None and nxt.is_word("select", "with", "values") else "other")
        elif tok.is_punct(")"):
            if not stack:
                raise SqlValidationError("Paréntesis ')' sin abrir.")
            stack.pop()
    if stack:
        raise SqlValidationError("Paréntesis sin cerrar.")
    return kinds


def _read_relation(toks, j):
    """Lee `[esquema.]nombre` desde j. Devuelve (esquema, nombre, siguiente)."""
    parts = [toks[j].value]
    j += 1
    while j + 1 < len(toks) and toks[j].is_punct(".") and _is_name(toks[j + 1]):
        parts.append(toks[j + 1].value)
        j += 2
    if len(parts) > 2:
        raise SqlValidationError("Nombre de tabla con demasiados puntos: %s." % ".".join(parts))
    schema = parts[0] if len(parts) == 2 else None
    return schema, parts[-1], j


def _skip_alias(toks, j):
    """Salta `[AS] alias [(col, ...)]` tras una tabla."""
    if j < len(toks) and toks[j].is_word("as"):
        j += 1
    if j < len(toks) and _is_name(toks[j]) and not (toks[j].kind == "word" and toks[j].value in _NOT_ALIAS):
        j += 1
        if j < len(toks) and toks[j].is_punct("("):
            j = _matching_paren(toks, j) + 1
    return j


def _referenced_relations(toks, kinds):
    """Relaciones (tablas/vistas) que aparecen tras FROM / JOIN, con su posición.

    Las funciones en el FROM (generate_series(...), unnest(...)) no son
    relaciones; las prohibidas ya se rechazan en otra regla.
    """
    found = []
    for i, tok in enumerate(toks):
        if not tok.is_word("from", "join") or kinds[i] != "query":
            continue
        # IS [NOT] DISTINCT FROM no abre una lista de tablas
        if tok.value == "from" and i > 0 and toks[i - 1].is_word("distinct"):
            continue
        j = i + 1
        while j < len(toks):
            while j < len(toks) and toks[j].is_word("lateral", "only"):
                j += 1
            if j >= len(toks):
                break
            if toks[j].is_punct("("):
                # Subconsulta o join entre paréntesis: su contenido se recorre
                # igualmente en este mismo bucle externo.
                j = _matching_paren(toks, j) + 1
            elif _is_name(toks[j]):
                schema, name, nxt = _read_relation(toks, j)
                if nxt < len(toks) and toks[nxt].is_punct("("):
                    j = _matching_paren(toks, nxt) + 1  # función en el FROM
                else:
                    found.append((schema, name, toks[j]))
                    j = nxt
            else:
                break
            if tok.value == "join":
                break
            j = _skip_alias(toks, j)
            if j < len(toks) and toks[j].is_punct(","):
                j += 1
                continue
            break
    return found


def validate_select(sql, whitelist=DEFAULT_WHITELIST):
    """Valida `sql` y devuelve un dict con la consulta normalizada.

    Resultado: {'sql': consulta sin ';' final, 'tables': set de relaciones
    leídas, 'ctes': set de nombres de CTE}.
    Lanza SqlValidationError con un motivo legible si la rechaza.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise SqlValidationError("La consulta está vacía.")
    if len(sql) > MAX_QUERY_LENGTH:
        raise SqlValidationError("La consulta supera %d caracteres." % MAX_QUERY_LENGTH)
    if "\x00" in sql:
        raise SqlValidationError("La consulta contiene caracteres no válidos.")

    toks = tokenize(sql)

    # Una sola sentencia: solo se tolera ';' al final.
    while toks and toks[-1].is_punct(";"):
        toks.pop()
    if not toks:
        raise SqlValidationError("La consulta está vacía.")
    if any(t.is_punct(";") for t in toks):
        raise SqlValidationError("Solo se admite una sentencia; quita los ';' intermedios.")
    # Se recorta tras el último token útil: fuera el ';' final y lo que le siga.
    clean_sql = sql[:toks[-1].end].strip()

    if not toks[0].is_word("select", "with"):
        raise SqlValidationError("Solo se admiten consultas SELECT o WITH ... SELECT.")

    for t in toks:
        if t.kind == "word" and t.value in FORBIDDEN_KEYWORDS:
            raise SqlValidationError(
                "Palabra no permitida en una consulta de solo lectura: %s." % t.value.upper())
        if t.kind in ("word", "ident") and t.value.lower() in FORBIDDEN_SCHEMAS:
            raise SqlValidationError("No se puede consultar el esquema %s." % t.value)

    for i, t in enumerate(toks[:-1]):
        if _is_name(t) and toks[i + 1].is_punct("(") and _FORBIDDEN_FUNC_RE.search(t.value.lower()):
            raise SqlValidationError("Función no permitida: %s()." % t.value)

    kinds = _paren_kinds(toks)
    ctes = _collect_ctes(toks)
    tables = set()
    for schema, name, _tok in _referenced_relations(toks, kinds):
        if schema is not None and schema.lower() != "public":
            raise SqlValidationError("Solo se puede leer del esquema public (se usó %s)." % schema)
        if schema is None and name in ctes:
            continue
        if name not in whitelist:
            raise SqlValidationError(
                "Tabla no permitida: %s. Tablas disponibles: %s."
                % (name, ", ".join(sorted(whitelist))))
        tables.add(name)
    return {"sql": clean_sql, "tables": tables, "ctes": ctes}

