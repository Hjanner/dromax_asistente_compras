/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Component, useState, useRef, onWillStart, onMounted, onPatched } from "@odoo/owl";
import { renderMarkdown } from "./markdown";

const SESSION = "ai.purchase.chat.session";

export class AiPurchaseChat extends Component {
    static template = "dromax_asistente_compras.AiPurchaseChat";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.threadRef = useRef("thread");
        this.inputRef = useRef("input");
        this.state = useState({
            sessions: [],
            presets: [],
            levels: [],
            activeId: null,
            messages: [],
            input: "",
            level: "medium",
            loading: false,
            pendingText: "",
            openTraces: {},
            canCreateRfq: false,
            // Pregunta predefinida que espera un dato (p. ej. el laboratorio) en el cuadro de texto.
            inputPreset: null,
        });
        // Bajar al final solo cuando cambia el hilo: si no, cualquier
        // repintado devolvería al fondo mientras se leen mensajes antiguos.
        this.pendingScroll = true;
        this.renderMarkdown = renderMarkdown;

        onWillStart(async () => {
            const data = await this.orm.call(SESSION, "chat_bootstrap", []);
            Object.assign(this.state, {
                sessions: data.sessions, presets: data.presets, levels: data.levels,
                canCreateRfq: data.can_create_rfq,
            });
            if (data.sessions.length) {
                await this.selectSession(data.sessions[0].id);
            }
        });
        // onPatched no se dispara en el primer renderizado.
        onMounted(() => this.scrollToBottom());
        onPatched(() => this.pendingScroll && this.scrollToBottom());
    }

    scrollToBottom() {
        this.pendingScroll = false;
        const go = () => {
            const el = this.threadRef.el;
            if (el) {
                el.scrollTop = el.scrollHeight;
            }
        };
        go();
        // Las tablas cambian la altura tras el primer cálculo.
        requestAnimationFrame(go);
    }

    get activeSession() {
        return this.state.sessions.find((s) => s.id === this.state.activeId);
    }

    async selectSession(id) {
        if (this.state.loading) {
            return;
        }
        this.state.activeId = id;
        this.state.messages = await this.orm.call(SESSION, "chat_messages", [[id]]);
        this.state.openTraces = {};
        this.pendingScroll = true;
    }

    newSession() {
        if (this.state.loading) {
            return;
        }
        // La conversación se crea al enviar el primer mensaje.
        Object.assign(this.state, { activeId: null, messages: [], openTraces: {} });
        this.inputRef.el?.focus();
    }

    async deleteSession(id, ev) {
        ev.stopPropagation();
        if (this.state.loading) {
            return;
        }
        await this.orm.call(SESSION, "chat_delete", [[id]]);
        this.state.sessions = this.state.sessions.filter((s) => s.id !== id);
        if (this.state.activeId === id) {
            this.newSession();
        }
    }

    async ensureSession() {
        if (!this.state.activeId) {
            const session = await this.orm.call(SESSION, "chat_new_session", []);
            this.state.sessions.unshift(session);
            this.state.activeId = session.id;
        }
        return this.state.activeId;
    }

    /** Botón de pregunta predefinida: se envía ya, o primero pide su dato. */
    onPreset(preset) {
        if (this.state.loading) {
            return;
        }
        if (preset.input) {
            this.state.inputPreset = preset;
            this.inputRef.el?.focus();
        } else {
            this.send({ preset: preset.key });
        }
    }

    cancelInputPreset() {
        this.state.inputPreset = null;
        this.inputRef.el?.focus();
    }

    submitInput() {
        const preset = this.state.inputPreset;
        this.send(preset ? { text: this.state.input, preset: preset.key } : { text: this.state.input });
    }

    async send({ text = null, preset = null } = {}) {
        if (this.state.loading) {
            return;
        }
        const presetDef = preset && this.state.presets.find((p) => p.key === preset);
        if ((!preset || presetDef?.input) && !(text || "").trim()) {
            return;
        }
        const label = !presetDef ? text.trim()
            : presetDef.input ? `${presetDef.label}: ${text.trim()}` : presetDef.label;
        this.state.loading = true;
        this.state.pendingText = label;
        this.state.input = "";
        this.pendingScroll = true;
        try {
            const id = await this.ensureSession();
            const res = await this.orm.call(SESSION, "chat_send", [[id]], {
                text, preset, level: this.state.level,
            });
            this.state.messages.push(...res.messages);
            const idx = this.state.sessions.findIndex((s) => s.id === id);
            if (idx >= 0) {
                this.state.sessions.splice(idx, 1);
            }
            this.state.sessions.unshift(res.session);
            if (presetDef?.input) {
                this.state.inputPreset = null;
            }
        } catch (error) {
            this.state.input = text || "";
            throw error;
        } finally {
            this.state.loading = false;
            this.state.pendingText = "";
            this.pendingScroll = true;
        }
    }

    onSubmit(ev) {
        ev.preventDefault();
        this.submitInput();
    }

    onKeydown(ev) {
        // Enter envía; Mayús+Enter hace salto de línea; Escape quita la pregunta que esperaba un dato.
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.submitInput();
        } else if (ev.key === "Escape" && this.state.inputPreset) {
            this.cancelInputPreset();
        }
    }

    get inputPlaceholder() {
        return this.state.inputPreset
            ? `${this.state.inputPreset.input} (Enter envía, Esc cancela)`
            : "Escribe tu pregunta… (Enter envía, Mayús+Enter salto de línea)";
    }

    /** Solo bajo la última respuesta que trató cada propuesta, para no repetir los botones. */
    showProposalButtons(msg) {
        if (!msg.proposal) {
            return false;
        }
        const last = this.state.messages.findLast((m) => m.proposal?.id === msg.proposal.id);
        return last.id === msg.id;
    }

    /** Tras revisar los traslados no se ofrece comprar: primero se ponen en 0 los que se trasladan. */
    canGenerate(proposal) {
        return this.state.canCreateRfq && !["done", "discarded"].includes(proposal.state) &&
            proposal.vista !== "traslados";
    }

    openLabel(proposal) {
        return proposal.vista === "traslados" ? "Ver traslados en la propuesta" : "Abrir propuesta";
    }

    async openProposal(proposal) {
        if (proposal.vista === "traslados") {
            const action = await this.orm.call("ai.purchase.proposal", "action_open_lines",
                [[proposal.id]], { transfers: true });
            return this.action.doAction(action);
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "ai.purchase.proposal",
            res_id: proposal.id,
            views: [[false, "form"]],
        });
    }

    /** Con un solo laboratorio en la respuesta, el botón pide solo ese laboratorio. */
    draftsLabel(proposal) {
        return proposal.laboratorio
            ? `Generar borradores de ${proposal.laboratorio}`
            : "Generar todos los borradores";
    }

    generateDrafts(proposal) {
        const scope = proposal.laboratorio
            ? `del laboratorio «${proposal.laboratorio}» en «${proposal.name}», de todas tus farmacias. ` +
              `Los demás laboratorios no se piden.`
            : `de todos los laboratorios de «${proposal.name}», de todas tus farmacias.`;
        this.dialog.add(ConfirmationDialog, {
            title: this.draftsLabel(proposal),
            body: `Se creará una orden de compra en borrador por farmacia y droguería con las ` +
                `líneas por pedir ${scope} ¿Continuar?`,
            confirmLabel: "Generar borradores",
            confirm: async () => {
                const action = await this.orm.call(SESSION, "chat_create_rfq",
                    [[this.state.activeId], proposal.id, proposal.laboratorio]);
                await this.action.doAction(action);
            },
            cancel: () => {},
        });
    }

    toggleTrace(messageId) {
        this.state.openTraces[messageId] = !this.state.openTraces[messageId];
    }

    levelLabel(key) {
        return this.state.levels.find((l) => l.key === key)?.label || key;
    }

    formatCost(cost) {
        return `${(cost || 0).toFixed(4)} USD`;
    }

    formatDuration(ms) {
        return `${((ms || 0) / 1000).toFixed(1)} s`;
    }
}

registry.category("actions").add("dromax_ai_purchase_chat", AiPurchaseChat);
