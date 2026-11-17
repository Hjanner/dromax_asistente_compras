/** @odoo-module **/
/**
 * Aviso «Cargando…» grande para las pantallas del Agente IA.
 *
 * El indicador de Odoo es un recuadro pequeño en una esquina y no bloquea la
 * pantalla: con cálculos de varios segundos (reabastecimiento, propuesta,
 * borradores) el usuario no lo ve y cree que no pasa nada. Este componente
 * escucha las llamadas al servidor (`RPC:REQUEST` / `RPC:RESPONSE` del bus) y,
 * si una del Agente IA tarda más de SHOW_DELAY, cubre la pantalla con un aviso
 * centrado (que además evita pulsar dos veces); a partir de SLOW_DELAY avisa
 * que sigue trabajando.
 *
 * Cuentan las llamadas a modelos `ai.*` y los botones «Ejecutar ahora» de
 * Ajustes (`action_ai_*`). Queda fuera la pregunta del chat (`chat_send`), que
 * tiene su propio indicador y puede tardar un minuto.
 */
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { useBus } from "@web/core/utils/hooks";
import { Component, useState, onWillDestroy } from "@odoo/owl";

export const SHOW_DELAY = 300;
export const SLOW_DELAY = 5000;

const CHAT_MODEL = "ai.purchase.chat.session";

export function isAgentCall(params) {
    const model = params && params.model;
    const method = (params && params.method) || "";
    if (!model) {
        return false;
    }
    if (model === CHAT_MODEL && method === "chat_send") {
        return false;
    }
    if (model.startsWith("ai.")) {
        return true;
    }
    return model === "res.config.settings" && method.startsWith("action_ai_");
}

export class AiLoadingOverlay extends Component {
    static template = "dromax_asistente_compras.AiLoadingOverlay";
    static props = {};

    setup() {
        this.state = useState({ show: false, slow: false });
        this.pending = new Set();
        this.showTimer = null;
        this.slowTimer = null;
        useBus(this.env.bus, "RPC:REQUEST", ({ detail }) => this.onRequest(detail));
        useBus(this.env.bus, "RPC:RESPONSE", ({ detail }) => this.onResponse(detail));
        onWillDestroy(() => this.clearTimers());
    }

    onRequest({ data, settings }) {
        if ((settings && settings.silent) || !data || !isAgentCall(data.params)) {
            return;
        }
        this.pending.add(data.id);
        if (this.pending.size === 1) {
            this.clearTimers();
            this.showTimer = browser.setTimeout(() => (this.state.show = true), SHOW_DELAY);
            this.slowTimer = browser.setTimeout(() => (this.state.slow = true), SLOW_DELAY);
        }
    }

    onResponse({ data }) {
        if (!data || !this.pending.delete(data.id) || this.pending.size) {
            return;
        }
        this.clearTimers();
        this.state.show = false;
        this.state.slow = false;
    }

    clearTimers() {
        browser.clearTimeout(this.showTimer);
        browser.clearTimeout(this.slowTimer);
    }
}

registry.category("main_components").add("dromax_asistente_compras.AiLoadingOverlay", {
    Component: AiLoadingOverlay,
});
