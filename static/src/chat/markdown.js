/** @odoo-module **/

import { markup } from "@odoo/owl";

/**
 * Markdown -> HTML para lo que devuelve el modelo: párrafos, encabezados,
 * viñetas, listas numeradas, tablas, negrita, cursiva y `código`.
 *
 * El texto lleva datos de la base (nombres de productos, droguerías...): se
 * ESCAPA primero y solo después se insertan las etiquetas que genera esta
 * función. Nunca se inyecta HTML venido del modelo ni de la base.
 */
function escape(text) {
    return String(text || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function inline(text) {
    return text
        .replace(/`([^`\n]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
        .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
        .replace(/(^|[^*\w])\*([^*\n]+)\*(?![*\w])/g, "$1<em>$2</em>");
}

function splitRow(line) {
    let row = line.trim();
    if (row.startsWith("|")) {
        row = row.slice(1);
    }
    if (row.endsWith("|")) {
        row = row.slice(0, -1);
    }
    return row.split("|").map((cell) => cell.trim());
}

const SEPARATOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const NUMERIC = /^[-+]?[\d.,\s]+%?$/;

export function renderMarkdown(text) {
    const lines = escape(text).split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
        const line = lines[i];
        // Tabla: fila de cabecera seguida de una fila separadora |---|---|
        if (line.includes("|") && i + 1 < lines.length && SEPARATOR.test(lines[i + 1])) {
            const head = splitRow(line);
            i += 2;
            const rows = [];
            while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
                rows.push(splitRow(lines[i]));
                i++;
            }
            const th = head.map((c) => `<th>${inline(c)}</th>`).join("");
            const body = rows
                .map((r) => "<tr>" + r.map((c) =>
                    `<td class="${NUMERIC.test(c) ? "o_ai_num" : ""}">${inline(c)}</td>`).join("") + "</tr>")
                .join("");
            out.push(`<div class="o_ai_table_wrap"><table class="table table-sm o_ai_table"><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table></div>`);
            continue;
        }
        const heading = line.match(/^#{1,6}\s+(.*)$/);
        if (heading) {
            out.push(`<h6 class="o_ai_heading">${inline(heading[1])}</h6>`);
            i++;
            continue;
        }
        if (/^\s*[-*]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
            const ordered = /^\s*\d+[.)]\s+/.test(line);
            const items = [];
            const itemRe = ordered ? /^\s*\d+[.)]\s+(.*)$/ : /^\s*[-*]\s+(.*)$/;
            while (i < lines.length && itemRe.test(lines[i])) {
                items.push(`<li>${inline(lines[i].match(itemRe)[1])}</li>`);
                i++;
            }
            out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`);
            continue;
        }
        if (!line.trim()) {
            i++;
            continue;
        }
        const para = [];
        while (i < lines.length && lines[i].trim() && !/^#{1,6}\s/.test(lines[i])
               && !/^\s*([-*]|\d+[.)])\s+/.test(lines[i])
               && !(lines[i].includes("|") && i + 1 < lines.length && SEPARATOR.test(lines[i + 1]))) {
            para.push(inline(lines[i]));
            i++;
        }
        out.push(`<p>${para.join("<br/>")}</p>`);
    }
    return markup(out.join(""));
}
