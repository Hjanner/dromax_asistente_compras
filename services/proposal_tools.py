# -*- coding: utf-8 -*-
"""Herramientas de la propuesta de compra: leerla y ajustar sus líneas.

El agente trabaja sobre la propuesta guardada (la del cron del lunes o la del
botón Calcular propuesta), no sobre una lista que arma él: lo que el comprador
ve en el chat y en Compras IA → Propuestas es lo mismo, y lo que ajusta en el
chat queda en la propuesta.

update_proposal_lines es la única escritura del agente: cambia la cantidad
final, la droguería o la nota de líneas concretas, con las mismas reglas que
la pantalla (write de la línea). Las órdenes de compra no: las genera el
comprador con el botón.

Las dos herramientas ven las líneas de todas las farmacias del usuario, no
solo las marcadas en el selector de empresas: la compra no es estricta por
farmacia.
"""
import re

from markupsafe import Markup, escape

from odoo import _, fields
from odoo.exceptions import UserError, ValidationError

GET_PROPOSAL = {
    "type": "function",
    "function": {
        "name": "get_proposal",
        "description": (
            "Lee la propuesta de compra semanal guardada (por defecto, la vigente). Sin filtros "
            "devuelve el resumen: totales, farmacias, laboratorios de mayor monto, cuántas líneas "
            "necesitan atención y las de mayor monto a revisar por presentación o que se pueden "
            "cubrir con un traslado. Con algún filtro (laboratorio, buscar, farmacia o filtro) "
            "devuelve las líneas que coinciden, cada una con su linea_id, cantidades, "
            "droguería, precio, motivo y las ofertas de las demás droguerías."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "propuesta_id": {"type": "integer",
                                 "description": "Otra propuesta; por defecto, la vigente."},
                "laboratorio": {"type": "string", "description": "Laboratorio (nombre o parte)."},
                "buscar": {"type": "string",
                           "description": "Producto: parte del nombre, referencia o código de barras."},
                "farmacia": {"type": "string", "description": "Farmacia (nombre o parte)."},
                "filtro": {
                    "type": "string",
                    "enum": ["por_pedir", "ajustadas", "revisar_presentacion", "sin_drogueria",
                             "con_excedente", "se_cubre_con_traslado", "en_solicitud", "pedidas"],
                    "description": (
                        "por_pedir: con cantidad y droguería, aún sin orden; ajustadas: cambiadas "
                        "por el comprador; revisar_presentacion: precio de más del triple del "
                        "costo; sin_drogueria: ninguna droguería lo ofrece; con_excedente: otra "
                        "farmacia tiene de sobra; se_cubre_con_traslado: el excedente de otras "
                        "farmacias alcanza para toda la cantidad final (de mayor a menor monto); "
                        "en_solicitud: ya hay solicitudes sin confirmar; pedidas: ya están en una "
                        "orden de compra."),
                },
                "limite": {"type": "integer", "description": "Máximo de líneas (por defecto 40, tope 80)."},
            },
            "additionalProperties": False,
        },
    },
}

UPDATE_PROPOSAL_LINES = {
    "type": "function",
    "function": {
        "name": "update_proposal_lines",
        "description": (
            "Ajusta líneas de la propuesta, solo cuando el comprador lo pide: cantidad final "
            "(0 = no pedir), droguería (una de las que ofertan el producto; el precio se toma "
            "de su oferta) o una nota que se agrega al motivo. Usa los linea_id que devolvió "
            "get_proposal. No crea órdenes de compra. Devuelve el antes y el después de cada "
            "línea, o el error de las que no se pudieron cambiar."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "cambios": {
                    "type": "array",
                    "description": "Hasta 50 cambios.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "linea_id": {"type": "integer"},
                            "cantidad_final": {"type": "integer"},
                            "drogueria": {"type": "string", "description": "Nombre o parte del nombre."},
                            "nota": {"type": "string", "description": "Por qué se cambia, en pocas palabras."},
                        },
                        "required": ["linea_id"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["cambios"],
            "additionalProperties": False,
        },
    },
}

DEFAULT_LINES = 40
MAX_LINES = 80
MAX_CHANGES = 50
TOP_LABS = 30
TOP_ATTENTION = 10
MAX_NOTE = 300

FILTERS = {
    "por_pedir": [("cantidad_final", ">", 0), ("drogueria_id", "!=", False), ("purchase_line_id", "=", False)],
    "ajustadas": [("ajustada", "=", True)],
    "revisar_presentacion": [("revisar_presentacion", "=", True)],
    "sin_drogueria": [("drogueria_id", "=", False)],
    "con_excedente": [("excedente_otras", ">", 0)],
    "en_solicitud": [("en_solicitud", ">", 0)],
    "pedidas": [("purchase_line_id", "!=", False)],
    "se_cubre_con_traslado": [("se_cubre_con_traslado", "=", True), ("purchase_line_id", "=", False)],
}
TRANSFER_FILTER = "se_cubre_con_traslado"


# "FARMACIA A (12), FARMACIA B (3)", de mayor a menor excedente (ver ai_v_faltantes).
EXCESS_ITEM = re.compile(r"(.+?) \((\d+)\)(?:, |$)")


def _excess_origins(line):
    """Farmacias con excedente del producto, de mayor a menor: [{'farmacia', 'excedente'}]."""
    return [{"farmacia": name, "excedente": int(qty)}
            for name, qty in EXCESS_ITEM.findall(line.farmacias_con_excedente or "")]


def _transfer_origin(line):
    """La farmacia con más excedente y si alcanza sola para la cantidad final."""
    best = max(_excess_origins(line), key=lambda o: o["excedente"], default=None)
    return best and best["farmacia"], bool(best and best["excedente"] >= line.cantidad_final)


def _by_origin(lines):
    """Lo que se deja de comprar por farmacia de origen, con las líneas que una sola farmacia cubre."""
    totals, several = {}, 0
    for line in lines:
        origin, alone = _transfer_origin(line)
        if not alone:
            several += 1
            continue
        item = totals.setdefault(origin, {"farmacia": origin, "lineas": 0, "monto": 0.0})
        item["lineas"] += 1
        item["monto"] += line.monto
    result = sorted(totals.values(), key=lambda i: -i["monto"])
    for item in result:
        item["monto"] = _money(item["monto"])
    return result, several


class ProposalToolError(Exception):
    """Error que se le devuelve al modelo para que corrija o lo explique."""


def all_companies_env(env):
    """env con todas las farmacias del usuario permitidas, la actual primero."""
    ids = [env.company.id] + [cid for cid in env.user.company_ids.ids if cid != env.company.id]
    return env(context=dict(env.context, allowed_company_ids=ids))


def _money(value):
    return round(value or 0.0, 2)


def _local(record, value):
    return fields.Datetime.context_timestamp(record, value).strftime("%d/%m/%Y %H:%M") if value else None


def _proposal(env, proposal_id):
    Proposal = env["ai.purchase.proposal"]
    if proposal_id:
        proposal = Proposal.browse(int(proposal_id)).exists()
        if not proposal:
            raise ProposalToolError(_("No existe la propuesta %s.", proposal_id))
        return proposal
    proposal = Proposal._current()
    if not proposal:
        raise ProposalToolError(_(
            "Todavía no hay ninguna propuesta de compra. Se calcula sola los lunes en la madrugada; "
            "también se puede calcular con «Arma la compra de esta semana» o con el botón Calcular "
            "propuesta en Compras IA → Propuestas."))
    return proposal


def _header(proposal):
    return {
        "id": proposal.id,
        "nombre": proposal.name,
        "estado": dict(proposal._fields["state"].selection)[proposal.state],
        "calculada": _local(proposal, proposal.date_computed),
        "oferta_cargada": _local(proposal, proposal.offer_date),
        "semanas_cobertura": proposal.coverage_weeks,
        "semanas_historial": proposal.window_weeks,
        "ordenes_generadas": proposal.purchase_order_ids.mapped("name"),
    }


def _short_line(line, **extra):
    item = {"linea_id": line.id, "producto": line.product_id.display_name, "farmacia": line.company_id.name,
            "cantidad_final": line.cantidad_final, "drogueria": line.drogueria_id.name or None,
            "precio": _money(line.precio), "monto": _money(line.monto)}
    item.update(extra)
    return item


def _summary(Line, base):
    count = lambda extra: Line.search_count(base + extra)
    [(lines, amount, suggested)] = Line._read_group(base, [], ["__count", "monto:sum", "monto_sugerido:sum"])
    pharmacies = [{"farmacia": company.name, "lineas": n, "monto": _money(total)}
                  for company, n, total in Line._read_group(
                      base, ["company_id"], ["__count", "monto:sum"], order="monto:sum desc")]
    to_order = dict(Line._read_group(base + FILTERS["por_pedir"], ["laboratorio"], ["__count"]))
    review = dict(Line._read_group(base + FILTERS["revisar_presentacion"], ["laboratorio"], ["__count"]))
    labs = Line._read_group(base, ["laboratorio"], ["__count", "monto:sum"], order="monto:sum desc")
    review_lines = Line.search(base + FILTERS["revisar_presentacion"], order="monto desc", limit=TOP_ATTENTION)
    # Traslados que cubren toda la cantidad: otra farmacia tiene al menos lo que se iba a pedir.
    transfers = Line.search(base + FILTERS[TRANSFER_FILTER], order="monto desc, id")
    return {
        "lineas": lines,
        "monto_final": _money(amount),
        "monto_sugerido": _money(suggested),
        "lineas_ajustadas": count(FILTERS["ajustadas"]),
        "en_cero": count([("cantidad_final", "=", 0)]),
        "atencion": {key: count(FILTERS[key]) for key in (
            "por_pedir", "pedidas", "revisar_presentacion", "sin_drogueria", "con_excedente", "en_solicitud")},
        "farmacias": pharmacies,
        "laboratorios_total": len(labs),
        "laboratorios_mayor_monto": [{
            "laboratorio": lab, "lineas": n, "monto": _money(total),
            "por_pedir": to_order.get(lab, 0), "revisar_presentacion": review.get(lab, 0),
        } for lab, n, total in labs[:TOP_LABS]],
        "revisar_presentacion_mayor_monto": [_short_line(l, costo=_money(l.costo)) for l in review_lines],
        "traslados_que_cubren_todo": len(transfers),
        "monto_que_cubren_los_traslados": _money(sum(transfers.mapped("monto"))),
        "traslados_mayor_monto": [_short_line(l, excedente_otras=l.excedente_otras,
                                              origenes_excedente=_excess_origins(l))
                                  for l in transfers[:TOP_ATTENTION]],
    }


def _offers(env, product_ids):
    """Mejor oferta disponible de cada droguería por producto, de la más barata a la más cara."""
    if not product_ids:
        return {}
    env.cr.execute("""
        SELECT DISTINCT ON (product_id, partner_id) product_id, partner_id, precio_final, stock_drogueria
          FROM ai_v_oferta
         WHERE disponible AND product_id = ANY(%s)
         ORDER BY product_id, partner_id, precio_final, id
    """, (list(product_ids),))
    rows = env.cr.fetchall()
    names = {p.id: p.name for p in env["res.partner"].sudo().browse({r[1] for r in rows})}
    offers = {}
    for product_id, partner_id, price, stock in sorted(rows, key=lambda r: (r[0], r[2])):
        offers.setdefault(product_id, []).append(
            {"drogueria": names[partner_id], "precio": _money(price), "stock": stock})
    return offers


def _line_dict(line, offers):
    item = {
        "linea_id": line.id,
        "farmacia": line.company_id.name,
        "producto": line.product_id.display_name,
        "laboratorio": line.laboratorio,
        "demanda_semanal": line.demanda,
        "stock": line.stock,
        "pendiente": line.pendiente,
        "cantidad_sugerida": line.cantidad_sugerida,
        "cantidad_final": line.cantidad_final,
        "drogueria": line.drogueria_id.name or None,
        "precio": _money(line.precio),
        "descuento_pct": line.descuento,
        "monto": _money(line.monto),
        "stock_drogueria": line.stock_drogueria,
        "vencimiento": line.fecha_vencimiento and line.fecha_vencimiento.isoformat() or None,
        "motivo": line.motivo,
    }
    if line.drogueria_id != line.drogueria_sugerida_id:
        item["drogueria_sugerida"] = line.drogueria_sugerida_id.name or None
    if line.en_solicitud:
        item["en_solicitud"] = line.en_solicitud
    if line.excedente_otras:
        origin, alone = _transfer_origin(line)
        item.update(excedente_otras=line.excedente_otras, origenes_excedente=_excess_origins(line),
                    origen_sugerido=origin, una_farmacia_alcanza=alone)
    if line.revisar_presentacion:
        item.update(revisar_presentacion=True, costo=_money(line.costo))
    if line.purchase_line_id:
        item["orden_de_compra"] = line.purchase_order_id.name
    product_offers = offers.get(line.product_id.id, [])
    if len(product_offers) > 1 or (product_offers and not line.drogueria_id):
        item["ofertas"] = product_offers
    return item


def get_proposal(env, args):
    env = all_companies_env(env)
    proposal = _proposal(env, args.get("propuesta_id"))
    Line = env["ai.purchase.proposal.line"]
    base = [("proposal_id", "=", proposal.id)]
    domain = list(base)
    lab = (args.get("laboratorio") or "").strip().upper()
    lab_found = []
    if lab:
        exact = Line.search_count(base + [("laboratorio", "=", lab)])
        domain.append(("laboratorio", "=" if exact else "ilike", lab))
        lab_found = [found for (found,) in Line._read_group(base + domain[-1:], ["laboratorio"])]
    if (args.get("buscar") or "").strip():
        domain.append(("product_id", "ilike", args["buscar"].strip()))
    if (args.get("farmacia") or "").strip():
        domain.append(("company_id", "ilike", args["farmacia"].strip()))
    filtro = args.get("filtro")
    if filtro:
        if filtro not in FILTERS:
            raise ProposalToolError(_("Filtro desconocido: %s.", filtro))
        domain += FILTERS[filtro]

    result = {"propuesta": _header(proposal)}
    if len(lab_found) == 1:
        result["laboratorio"] = lab_found[0]
    if domain == base:
        result.update(_summary(Line, base))
        result["row_count"] = result["lineas"]
        return result
    try:
        limit = min(max(int(args.get("limite") or DEFAULT_LINES), 1), MAX_LINES)
    except (TypeError, ValueError):
        limit = DEFAULT_LINES
    if filtro == TRANSFER_FILTER:
        matching = Line.search(domain, order="monto desc, id")
        found, amount, lines = len(matching), sum(matching.mapped("monto")), matching[:limit]
        result["por_farmacia_origen"], result["lineas_que_necesitan_varias_farmacias"] = _by_origin(matching)
    else:
        found = Line.search_count(domain)
        [(amount,)] = Line._read_group(domain, [], ["monto:sum"])
        lines = Line.search(domain, limit=limit)
    offers = _offers(env, lines.product_id.ids)
    result.update(
        lineas_encontradas=found,
        monto_encontrado=_money(amount),
        row_count=len(lines),
        truncated=found > len(lines),
        lineas=[_line_dict(line, offers) for line in lines],
    )
    if not found:
        result["nota"] = _("Ninguna línea de la propuesta coincide. Si el producto no aparece, es "
                           "porque su stock y lo pendiente cubren la demanda.")
        if lab:
            result["laboratorios_parecidos"] = _similar_labs(env, proposal, lab)
    return result


def _similar_labs(env, proposal, lab):
    """Laboratorios de la propuesta con nombre parecido (trigramas), para un nombre mal escrito."""
    env.cr.execute("""
        SELECT laboratorio FROM ai_purchase_proposal_line
         WHERE proposal_id = %s AND laboratorio IS NOT NULL
         GROUP BY laboratorio
        HAVING max(similarity(laboratorio, %s)) > 0.2
         ORDER BY max(similarity(laboratorio, %s)) DESC, laboratorio
         LIMIT 8
    """, (proposal.id, lab, lab))
    return [row[0] for row in env.cr.fetchall()]


def _snapshot(line):
    return {"cantidad_final": line.cantidad_final, "drogueria": line.drogueria_id.name or None,
            "precio": _money(line.precio), "monto": _money(line.monto)}


def _resolve_drogueria(line, text):
    """La droguería de la oferta del producto que corresponde a `text`."""
    options = line.drogueria_opciones_ids.sudo()
    wanted = text.strip().casefold()
    matches = options.filtered(lambda p: p.name.casefold() == wanted) \
        or options.filtered(lambda p: wanted in p.name.casefold())
    if len(matches) == 1:
        return matches
    listed = ", ".join(sorted(options.mapped("name"))) or _("ninguna")
    if matches:
        raise ProposalToolError(_("«%(text)s» coincide con varias droguerías: %(names)s.",
                                  text=text, names=", ".join(matches.mapped("name"))))
    raise ProposalToolError(_("«%(text)s» no tiene oferta disponible de este producto. Droguerías con "
                              "oferta: %(names)s.", text=text, names=listed))


def _apply_change(Line, change):
    line_id = change.get("linea_id")
    if not isinstance(line_id, int) or isinstance(line_id, bool):
        raise ProposalToolError(_("Falta linea_id (entero)."))
    line = Line.search([("id", "=", line_id)])
    if not line:
        raise ProposalToolError(_("La línea %s no existe o es de una farmacia a la que no tienes acceso.", line_id))
    vals = {}
    if change.get("cantidad_final") is not None:
        qty = change["cantidad_final"]
        if not isinstance(qty, (int, float)) or isinstance(qty, bool) or qty != int(qty) or qty < 0:
            raise ProposalToolError(_("La cantidad final debe ser un entero mayor o igual a 0."))
        vals["cantidad_final"] = int(qty)
    if (change.get("drogueria") or "").strip():
        vals["drogueria_id"] = _resolve_drogueria(line, change["drogueria"]).id
    note = (change.get("nota") or "").strip()[:MAX_NOTE]
    if note:
        vals["motivo"] = "%s\n%s" % (line.motivo or "", _("Ajuste en el chat: %s", note))
    if not vals:
        raise ProposalToolError(_("Nada que cambiar: indica cantidad_final, drogueria o nota."))
    before = _snapshot(line)
    line.write(vals)
    return line, {"linea_id": line.id, "producto": line.product_id.display_name,
                  "farmacia": line.company_id.name, "laboratorio": line.laboratorio,
                  "antes": before, "ahora": _snapshot(line)}


def answer_laboratories(result):
    """Laboratorios de las líneas que leyó o ajustó una llamada, o None si trató la
    propuesta completa (resumen o lista recortada de varios laboratorios)."""
    if "resultados" in result:
        return {r["laboratorio"] for r in result["resultados"] if "error" not in r}
    if "lineas" not in result or not isinstance(result["lineas"], list):
        return None
    if result.get("truncated"):
        return {result["laboratorio"]} if result.get("laboratorio") else None
    return {line["laboratorio"] for line in result["lineas"]}


def _log_body(user, changes):
    items = Markup()
    for line, result in changes:
        before, after = result["antes"], result["ahora"]
        parts = []
        if before["cantidad_final"] != after["cantidad_final"]:
            parts.append(_("cantidad %(a)s → %(b)s", a=before["cantidad_final"], b=after["cantidad_final"]))
        if before["drogueria"] != after["drogueria"]:
            parts.append(_("droguería %(a)s → %(b)s", a=before["drogueria"] or "-", b=after["drogueria"] or "-"))
        if not parts:
            parts.append(_("nota"))
        items += Markup("<li>%s · %s: %s</li>") % (line.product_id.display_name, line.company_id.name,
                                                   "; ".join(parts))
    return Markup("<p>%s</p><ul>%s</ul>") % (escape(_("Ajustes desde el chat de %s:", user.name)), items)


def update_proposal_lines(env, args):
    env = all_companies_env(env)
    changes = args.get("cambios")
    if not isinstance(changes, list) or not changes:
        raise ProposalToolError(_("Indica al menos un cambio en «cambios»."))
    if len(changes) > MAX_CHANGES:
        raise ProposalToolError(_("Como máximo %s cambios por llamada.", MAX_CHANGES))
    Line = env["ai.purchase.proposal.line"]
    results, applied = [], []
    for change in changes:
        if not isinstance(change, dict):
            results.append({"error": _("Cada cambio debe ser un objeto.")})
            continue
        try:
            line, result = _apply_change(Line, change)
        except (ProposalToolError, UserError, ValidationError) as exc:
            results.append({"linea_id": change.get("linea_id"), "error": str(exc.args[0] if exc.args else exc)})
            continue
        results.append(result)
        applied.append((line, result))

    proposals = env["ai.purchase.proposal"]
    for line, _result in applied:
        proposals |= line.proposal_id
    for proposal in proposals:
        proposal.message_post(body=_log_body(env.user, [c for c in applied if c[0].proposal_id == proposal]))
    out = {"row_count": len(applied), "resultados": results}
    if proposals:
        proposal = proposals[:1]
        out["propuesta"] = dict(_header(proposal), lineas_ajustadas=proposal.lines_changed,
                                monto_final=_money(proposal.amount_total))
    return out
