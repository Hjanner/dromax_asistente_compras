/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, useRef, onWillStart, onMounted, onPatched } from "@odoo/owl";
import { renderMarkdown } from "./markdown";

const SESSION = "ai.purchase.chat.session";

export class AiPurchaseChat extends Component {
    static template = "dromax_asistente_compras.AiPurchaseChat";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
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
        });
        // Bajar al final solo cuando cambia el hilo: si no, cualquier
        // repintado devolvería al fondo mientras se leen mensajes antiguos.
        this.pendingScroll = true;
        this.renderMarkdown = renderMarkdown;

        onWillStart(async () => {
            const data = await this.orm.call(SESSION, "chat_bootstrap", []);
            Object.assign(this.state, {
                sessions: data.sessions, presets: data.presets, levels: data.levels,
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

    async send({ text = null, preset = null } = {}) {
        if (this.state.loading) {
            return;
        }
        if (!preset && !(text || "").trim()) {
            return;
        }
        const label = preset ? this.state.presets.find((p) => p.key === preset)?.label : text.trim();
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
        this.send({ text: this.state.input });
    }

    onKeydown(ev) {
        // Enter envía; Mayús+Enter hace salto de línea.
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.send({ text: this.state.input });
        }
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
