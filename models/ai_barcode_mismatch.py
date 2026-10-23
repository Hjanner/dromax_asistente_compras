# -*- coding: utf-8 -*-
"""Reporte de incoherencias de código de barras entre los productos propios y
la oferta de las droguerías.

Se regenera completo con un botón (action_generate) y queda como tabla para
revisar y corregir. Todo el cálculo es SQL sobre tablas temporales:

1. ai_bm_productos: productos propios (activos, almacenables o consumibles,
   comprables) con su código normalizado (ai_barcode_norm) y su nombre.
2. ai_bm_oferta: líneas de la oferta de las droguerías con código normalizado.
3. A ambos lados se normaliza el nombre (norm_sql) y se extraen rasgos para
   comparar: números, dosis (número + unidad), cantidad del empaque ("X 30"),
   talla, primera palabra significativa y última (suele ser el laboratorio).

Regla de "nombre parecido, código distinto" (calibrada con la oferta real:
pares del mismo código en dos droguerías y genéricos de distinto laboratorio):
similitud de trigramas >= umbral, números y dosis de un nombre contenidos en
los del otro, misma cantidad de empaque y talla si ambos la dicen, la primera
palabra de cada nombre presente en el otro y la última de alguno presente en
el otro. Así no casan "ABRETIA 10 MG" con "ABRETIA 18 MG", "X 10" con "X 30",
LEVOFLOXACINA con CIPROFLOXACINA ni el mismo genérico de otro laboratorio.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from ..services.config import get_setting
from .ai_v_oferta import grant_readonly

GROUP_XMLID = "dromax_asistente_compras.group_ai_purchase_user"

DEFAULT_SIMILARITY = 0.6
DEFAULT_PRESENTATION_RATIO = 3.0
# Sugerencias de código por producto propio.
MAX_SUGGESTIONS = 3

# Palabras que no identifican al laboratorio aunque cierren el nombre.
GENERIC_WORDS = (
    "TAB,CAP,COMP,UND,CAJA,BLISTER,BLISTERS,SOBRE,SOBRES,AMP,AMPOLLA,AMPOLLAS,VIAL,FCO,"
    "FRASCO,JBE,JARABE,SUSP,SOL,SOLUCION,GOTAS,CREMA,GEL,ML,MG,MCG,LAB,PAQ,TALLA,ORAL,INY,"
    "TOPICO,USO,ADULTO,ADULTOS,PEDIATRICO,NINOS,UI,CC,MEQ,DISP"
)

TYPES = [
    ("codigo_distinto", "Nombre parecido, código distinto"),
    ("sin_oferta", "Sin oferta en droguerías"),
    ("sin_codigo", "Producto sin código de barras"),
    ("sin_producto", "Código sin producto propio"),
    ("presentacion", "Posible otra presentación"),
    ("duplicado", "Código duplicado en productos propios"),
]


def norm_sql(expr):
    """Expresión SQL que normaliza un nombre de producto para compararlo.

    Mayúsculas sin tildes, coma decimal a punto, sin puntuación (salvo el
    punto decimal y %), abreviaturas unificadas (TABLETAS -> TAB, GR -> G...)
    y unidades pegadas al número ("500 MG" -> "500MG").

    Devuelve el SQL con los % escapados (%%): va dentro de consultas que se
    ejecutan con parámetros.
    """
    sql = "translate(upper(%s), 'ÁÉÍÓÚÜÀÈÌÒÙ„', 'AEIOUUAEIOUÑ')" % expr
    for pattern, repl in (
        (r"(\d),(\d)", r"\1.\2"),
        (r"[^[:alnum:].%]+", " "),
        (r"\.(?!\d)|(?<!\d)\.", " "),
        (r"(^| )X(\d)", r"\1X \2"),
        (r"(\d)(MG|ML|G|GR|MCG)X(\d)", r"\1\2 X \3"),
        (r"(\d)(TABLETAS|TABLETA|TABS|TAB|CAPSULAS|CAPSULA|CAPS|CAP|COMP|UND|AMP|SOBRES)( |$)", r"\1 \2\3"),
    ):
        sql = "regexp_replace(%s, '%s', '%s', 'g')" % (sql, pattern, repl)
    sql = "(' ' || %s || ' ')" % sql
    for pattern, repl in (
        (r" (TABLETAS|TABLETA|TABS|TABL)(?= )", " TAB"),
        (r" (CAPSULAS|CAPSULA|CAPS)(?= )", " CAP"),
        (r" (COMPRIMIDOS|COMPRIMIDO|COMPR)(?= )", " COMP"),
        (r" (GRAMOS|GRS|GR)(?= )", " G"),
        (r" (UNIDADES|UNIDAD|UNID|UDS|UDN)(?= )", " UND"),
        (r"(\d) (MG|G|ML|MCG|UI|L|CC|MEQ|%)(?= )", r"\1\2"),
        (r" +", " "),
    ):
        sql = "regexp_replace(%s, '%s', '%s', 'g')" % (sql, pattern, repl)
    return ("trim(%s)" % sql).replace("%", "%%")


# Rasgos de comparación a partir de `txt` (nombre normalizado), como columnas
# de un SELECT: números, dosis, cantidad del empaque, talla, palabras, primera
# palabra significativa y última que no sea genérica (suele ser el laboratorio).
FEATURES_SQL = """
    ARRAY(SELECT DISTINCT m[1] FROM regexp_matches(txt, '(\\d+(?:\\.\\d+)?)', 'g') m ORDER BY 1) AS nums,
    ARRAY(SELECT DISTINCT m[1] FROM regexp_matches(' ' || txt || ' ',
          '(\\d+(?:\\.\\d+)?(?:MG|G|ML|MCG|UI|L|CC|MEQ|%%))(?= )', 'g') m ORDER BY 1) AS dosis,
    ARRAY(SELECT DISTINCT m[1] FROM regexp_matches(' ' || txt || ' ', ' X (\\d+)(?= )', 'g') m ORDER BY 1) AS packs,
    ARRAY(SELECT DISTINCT m[1] FROM regexp_matches(' ' || txt || ' ', ' TALLA ([A-Z0-9]+)(?= )', 'g') m ORDER BY 1) AS tallas,
    string_to_array(txt, ' ') AS words,
    (SELECT w FROM unnest(string_to_array(txt, ' ')) WITH ORDINALITY u(w, i)
      WHERE w ~ '^[A-ZÑ]{3,}$' ORDER BY i LIMIT 1) AS w1,
    (SELECT w FROM unnest(string_to_array(txt, ' ')) WITH ORDINALITY u(w, i)
      WHERE w ~ '^[A-Z0-9Ñ]*[A-ZÑ][A-Z0-9Ñ]*$' AND length(w) >= 2
        AND w <> ALL(string_to_array(%(generic)s, ','))
      ORDER BY i DESC LIMIT 1) AS wl
"""

# Regla para decidir que dos nombres son el mismo producto (a = propio, b = oferta).
SAME_PRODUCT_SQL = """
    similarity(a.txt, b.txt) >= %(threshold)s
    AND (a.nums <@ b.nums OR b.nums <@ a.nums)
    AND (a.dosis <@ b.dosis OR b.dosis <@ a.dosis)
    AND (a.packs = b.packs OR a.packs = '{}' OR b.packs = '{}')
    AND (a.tallas = b.tallas OR a.tallas = '{}' OR b.tallas = '{}')
    AND (a.w1 IS NULL OR a.w1 = ANY(b.words)) AND (b.w1 IS NULL OR b.w1 = ANY(a.words))
    AND (a.wl IS NULL OR b.wl IS NULL OR a.wl = ANY(b.words) OR b.wl = ANY(a.words))
"""


class AiBarcodeMismatch(models.Model):
    _name = "ai.barcode.mismatch"
    _description = "Incoherencia de código de barras"
    _order = "tipo, similitud desc, id"
    _rec_name = "detalle"

    tipo = fields.Selection(TYPES, string="Tipo", required=True, readonly=True, index=True)
    product_id = fields.Many2one("product.product", "Producto propio", readonly=True, ondelete="cascade")
    codigo_propio = fields.Char("Código del producto", readonly=True,
                                help="Código de barras tal como está en la ficha del producto.")
    codigo_barras = fields.Char("Código normalizado", readonly=True, index=True,
                                help="EAN-13 normalizado: el de la droguería o, si no hay, el del producto.")
    codigo_drogueria = fields.Char("Código en el archivo", readonly=True,
                                   help="Código de barras tal como lo trae la droguería.")
    descripcion = fields.Char("Descripción en la droguería", readonly=True)
    partner_id = fields.Many2one("res.partner", "Droguería", readonly=True, ondelete="cascade")
    droguerias = fields.Char("Droguerías", readonly=True)
    similitud = fields.Float("Similitud", digits=(3, 2), readonly=True,
                             help="Parecido entre el nombre propio y la descripción de la droguería (0 a 1).")
    precio_min = fields.Float("Precio mínimo", readonly=True)
    precio_max = fields.Float("Precio máximo", readonly=True)
    detalle = fields.Char("Detalle", readonly=True)

    def init(self):
        grant_readonly(self.env, self._table)

    # --- Generación -----------------------------------------------------------
    @api.model
    def _params(self):
        def number(name, default, low, high):
            try:
                value = float(get_setting(self.env, name, default))
            except (TypeError, ValueError):
                value = default
            return min(max(value, low), high)
        return {
            "threshold": number("barcode_similarity_threshold", DEFAULT_SIMILARITY, 0.3, 1.0),
            "ratio": number("barcode_presentation_ratio", DEFAULT_PRESENTATION_RATIO, 1.5, 100.0),
            "generic": GENERIC_WORDS,
            "max_suggestions": MAX_SUGGESTIONS,
        }

    def action_generate(self):
        """Rehace el reporte completo. Devuelve una notificación con el total.

        Sin @api.model: el botón de la cabecera de la lista lo llama con los
        registros seleccionados (o ninguno), que aquí no se usan.
        """
        if not self.env.user.has_group(GROUP_XMLID):
            raise AccessError(_("Solo los usuarios del Asistente de Compras IA pueden generar el reporte."))
        count = self._generate()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Reporte de códigos de barras"),
                "message": _("Se encontraron %s incoherencias.", count),
                "type": "success",
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    @api.model
    def _generate(self):
        params = self._params()
        cr = self.env.cr
        self.env.flush_all()
        cr.execute("DELETE FROM ai_barcode_mismatch")
        self._create_temp_tables(params)
        audit = "%(uid)s, %(uid)s, now() AT TIME ZONE 'UTC', now() AT TIME ZONE 'UTC'"
        params["uid"] = self.env.uid
        for statement in (
            INSERT_CODIGO_DISTINTO, INSERT_SIN_OFERTA, INSERT_SIN_CODIGO,
            INSERT_SIN_PRODUCTO, INSERT_PRESENTACION, INSERT_DUPLICADO,
        ):
            cr.execute(statement.replace("{audit}", audit), params)
        cr.execute("SELECT count(*) FROM ai_barcode_mismatch")
        self.invalidate_model()
        return cr.fetchone()[0]

    @api.model
    def _create_temp_tables(self, params):
        """Crea las tablas temporales con todas sus columnas en un solo paso.

        Nada de UPDATE antes de indexar: Odoo trabaja en REPEATABLE READ y un
        índice creado sobre filas actualizadas en la misma transacción queda
        inservible para ella (indcheckxmin), así que la búsqueda por trigramas
        recorría toda la oferta por cada producto (minutos en vez de segundos).
        """
        cr = self.env.cr
        cr.execute("DROP TABLE IF EXISTS ai_bm_productos, ai_bm_oferta")
        cr.execute("""
            CREATE TEMP TABLE ai_bm_productos ON COMMIT DROP AS
            SELECT x.*, """ + FEATURES_SQL + """
              FROM (SELECT pp.id AS product_id, NULLIF(trim(pp.barcode), '') AS barcode,
                           pp.ai_barcode_norm AS cod, n.nombre, """ + norm_sql("n.nombre") + """ AS txt
                      FROM product_product pp
                      JOIN product_template pt ON pt.id = pp.product_tmpl_id
                      CROSS JOIN LATERAL (SELECT COALESCE(pt.name->>'es_VE', pt.name->>'en_US') AS nombre) n
                     WHERE pp.active AND pt.active AND pt.purchase_ok
                       AND pt.detailed_type IN ('product', 'consu')) x
        """, params)
        cr.execute("""
            CREATE TEMP TABLE ai_bm_oferta ON COMMIT DROP AS
            SELECT x.*, """ + FEATURES_SQL + """
              FROM (SELECT s.id, s.partner_id, rp.name AS drogueria, s.ai_barcode_norm AS cod,
                           s.codigo_barras AS codigo_drogueria, s.descripcion_producto AS descripcion,
                           s.precio_unitario_final AS precio,
                           (s.stock_disponible > 0 AND s.precio_unitario_final > 0) AS disponible,
                           """ + norm_sql("s.descripcion_producto") + """ AS txt
                      FROM supplier_product_staging s
                      JOIN res_partner rp ON rp.id = s.partner_id
                     WHERE s.ai_barcode_norm IS NOT NULL) x
        """, params)
        cr.execute("CREATE INDEX ON ai_bm_oferta USING gin (txt gin_trgm_ops)")
        cr.execute("CREATE INDEX ON ai_bm_oferta (cod)")
        cr.execute("CREATE INDEX ON ai_bm_productos (cod)")
        cr.execute("ANALYZE ai_bm_productos")
        cr.execute("ANALYZE ai_bm_oferta")
        # El operador % usa este umbral y aprovecha el índice de trigramas.
        cr.execute("SELECT set_config('pg_trgm.similarity_threshold', %s, true)", (str(params["threshold"]),))

    def action_open_product(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "product.product",
            "res_id": self.product_id.id,
            "view_mode": "form",
            "target": "current",
        }


INSERT_COLUMNS = """
    INSERT INTO ai_barcode_mismatch (tipo, product_id, codigo_propio, codigo_barras, codigo_drogueria,
        descripcion, partner_id, droguerias, similitud, precio_min, precio_max, detalle,
        create_uid, write_uid, create_date, write_date)
"""

# Producto propio parecido a una oferta cuyo código no casa con ningún producto
# propio: lo más probable es que uno de los dos códigos esté mal o falte.
INSERT_CODIGO_DISTINTO = INSERT_COLUMNS + """
    WITH candidatos AS (
        SELECT DISTINCT ON (a.product_id, b.cod)
               a.product_id, a.barcode, a.nombre, b.cod, b.codigo_drogueria, b.descripcion,
               b.partner_id, b.sim
          FROM ai_bm_productos a
          -- LATERAL: una búsqueda por producto con el índice de trigramas (con un
          -- JOIN normal Postgres recorría toda la oferta por cada producto).
          CROSS JOIN LATERAL (
              SELECT b.*, similarity(a.txt, b.txt) AS sim
                FROM ai_bm_oferta b
               WHERE b.txt %% a.txt
          ) b
         WHERE b.cod NOT IN (SELECT cod FROM ai_bm_productos WHERE cod IS NOT NULL)
           AND """ + SAME_PRODUCT_SQL + """
         ORDER BY a.product_id, b.cod, b.sim DESC
    ), mejores AS (
        SELECT c.*, row_number() OVER (PARTITION BY c.product_id ORDER BY c.sim DESC, c.cod) AS n
          FROM candidatos c
    ), droguerias AS (
        SELECT cod, string_agg(DISTINCT drogueria, ', ') AS nombres
          FROM ai_bm_oferta WHERE cod IN (SELECT cod FROM candidatos) GROUP BY cod
    )
    SELECT 'codigo_distinto', m.product_id, m.barcode, m.cod, m.codigo_drogueria, m.descripcion,
           m.partner_id, d.nombres, round(m.sim::numeric, 2), NULL, NULL,
           CASE WHEN m.barcode IS NULL
                THEN 'Sin código propio: las droguerías lo ofrecen con ' || m.cod
                ELSE 'Código propio ' || m.barcode || '; las droguerías usan ' || m.cod END,
           {audit}
      FROM mejores m
      JOIN droguerias d ON d.cod = m.cod
     WHERE m.n <= %(max_suggestions)s
"""

INSERT_SIN_OFERTA = INSERT_COLUMNS + """
    SELECT 'sin_oferta', a.product_id, a.barcode, a.cod, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
           'Ninguna droguería ofrece ' || a.cod, {audit}
      FROM ai_bm_productos a
      LEFT JOIN (SELECT DISTINCT cod FROM ai_bm_oferta) o ON o.cod = a.cod
     WHERE a.cod IS NOT NULL AND o.cod IS NULL
"""

INSERT_SIN_CODIGO = INSERT_COLUMNS + """
    SELECT 'sin_codigo', a.product_id, a.barcode, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
           'El producto no tiene código de barras', {audit}
      FROM ai_bm_productos a
     WHERE a.cod IS NULL
"""

# Una fila por código: lo que ofrecen las droguerías y no tenemos. Agregado
# con GROUP BY: las subconsultas por fila tardaban minutos con 15.000 líneas.
INSERT_SIN_PRODUCTO = INSERT_COLUMNS + """
    WITH codigos AS (
        SELECT o.cod,
               string_agg(DISTINCT o.drogueria, ', ') AS droguerias,
               min(o.precio) FILTER (WHERE o.disponible) AS precio_min,
               -- línea que representa al código: la más barata disponible
               (array_agg(o.id ORDER BY o.disponible DESC, o.precio))[1] AS linea_id
          FROM ai_bm_oferta o
          LEFT JOIN ai_bm_productos p ON p.cod = o.cod
         WHERE p.product_id IS NULL
         GROUP BY o.cod
    )
    SELECT 'sin_producto', NULL, NULL, c.cod, r.codigo_drogueria, r.descripcion, r.partner_id,
           c.droguerias, NULL, c.precio_min, NULL,
           'Ningún producto propio tiene el código ' || c.cod, {audit}
      FROM codigos c
      JOIN ai_bm_oferta r ON r.id = c.linea_id
"""

# Mismo código con precios muy distintos entre droguerías: suele ser otra
# presentación (p. ej. "CAJA X 30 BLISTER" frente a la caja de un blíster).
INSERT_PRESENTACION = INSERT_COLUMNS + """
    WITH por_drogueria AS (
        SELECT DISTINCT ON (cod, partner_id) cod, partner_id, drogueria, descripcion,
               codigo_drogueria, precio
          FROM ai_bm_oferta
         WHERE disponible
         ORDER BY cod, partner_id, precio
    ), extremos AS (
        SELECT cod,
               (array_agg(drogueria ORDER BY precio))[1] AS drog_min,
               (array_agg(descripcion ORDER BY precio))[1] AS desc_min,
               min(precio) AS precio_min,
               (array_agg(partner_id ORDER BY precio DESC))[1] AS partner_max,
               (array_agg(drogueria ORDER BY precio DESC))[1] AS drog_max,
               (array_agg(descripcion ORDER BY precio DESC))[1] AS desc_max,
               (array_agg(codigo_drogueria ORDER BY precio DESC))[1] AS codigo_max,
               max(precio) AS precio_max,
               string_agg(drogueria, ', ' ORDER BY precio) AS droguerias
          FROM por_drogueria
         GROUP BY cod
        HAVING count(*) > 1 AND max(precio) > min(precio) * %(ratio)s
    )
    SELECT 'presentacion', p.product_id, p.barcode, e.cod, e.codigo_max, e.desc_max, e.partner_max,
           e.droguerias, NULL, e.precio_min, e.precio_max,
           left(e.drog_min || ': ' || e.desc_min || ' (' || round(e.precio_min::numeric, 2)
                || ') | ' || e.drog_max || ': ' || e.desc_max || ' (' || round(e.precio_max::numeric, 2) || ')',
                500),
           {audit}
      FROM extremos e
      LEFT JOIN (SELECT DISTINCT ON (cod) cod, product_id, barcode FROM ai_bm_productos
                  WHERE cod IS NOT NULL ORDER BY cod, product_id) p ON p.cod = e.cod
"""

INSERT_DUPLICADO = INSERT_COLUMNS + """
    WITH grupos AS (
        SELECT cod, array_agg(product_id ORDER BY product_id) AS ids,
               array_agg(nombre ORDER BY product_id) AS nombres
          FROM ai_bm_productos
         WHERE cod IS NOT NULL
         GROUP BY cod
        HAVING count(*) > 1
    )
    SELECT 'duplicado', a.product_id, a.barcode, a.cod, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
           left('Mismo código que: ' || array_to_string(ARRAY(
                SELECT g.nombres[i] FROM generate_subscripts(g.ids, 1) i
                 WHERE g.ids[i] <> a.product_id), ', '), 500),
           {audit}
      FROM ai_bm_productos a
      JOIN grupos g ON g.cod = a.cod
"""
