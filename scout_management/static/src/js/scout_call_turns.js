/** @odoo-module **/
/* Turnos de palabra en cualquier videollamada de Conversaciones (las del consejo usan su propio recuadro).
   Semáforo verde / amarillo / rojo, alarma en los últimos segundos, aviso en pantalla y en el chat, y silenciado
   automático al terminar el tiempo. Todo se configura por canal (y por participante). Los cambios llegan por el bus
   de Odoo; el servidor revisa los tiempos con una acción planificada y cuando un participante ve el cambio. */
import { Component, onWillDestroy, useState, xml } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { councilBeep, fmtClock } from "./scout_council_core";

const LABELS = { green: "A tiempo", yellow: "Mitad del tiempo", red: "Se acaba el tiempo", over: "Tiempo agotado",
                 idle: "Nadie tiene la palabra" };

export class ScoutCallTurns extends Component {
    static props = ["*"];
    static template = xml`
        <t t-if="state.data and !state.data.council">
            <button t-if="!state.data.enabled and state.data.can_moderate" class="o_scout_turns_fab" t-on-click="() => this.control('enable')"
                    title="Activar turnos de palabra"><i class="fa fa-clock-o"/> Turnos de palabra</button>
            <div t-elif="state.data.enabled" t-attf-class="o_scout_ccall o_scout_turns o_scout_ccall_#{phase} #{state.collapsed ? 'o_collapsed' : ''}">
                <div class="o_scout_ccall_head" t-on-click="() => this.state.collapsed = !this.state.collapsed">
                    <span class="o_scout_ccall_dot"/>
                    <span class="o_scout_ccall_clock" t-esc="clockText"/>
                    <span class="o_scout_ccall_title" t-esc="turn ? 'Habla: ' + turn.speaker : 'Turnos de palabra'"/>
                    <i t-attf-class="fa #{state.collapsed ? 'fa-chevron-up' : 'fa-chevron-down'} ms-auto"/>
                </div>
                <div t-if="state.alert" t-attf-class="o_scout_ccall_alert o_scout_ccall_alert_#{state.alert.level}">
                    <b t-esc="state.alert.title"/><div t-if="state.alert.text" t-esc="state.alert.text"/>
                </div>
                <div t-if="!state.collapsed" class="o_scout_ccall_body">
                    <div class="o_scout_ccall_bar"><span t-attf-style="width: #{barPct}%"/></div>
                    <div class="o_scout_ccall_meta"><span t-esc="phaseLabel"/><span t-if="turn"> · <t t-esc="Math.round(turn.duration / 6) / 10"/> min</span></div>
                    <t t-if="state.data.can_moderate">
                        <div class="o_scout_ccall_label">Dar la palabra
                            <select class="o_scout_ccall_min" t-model="state.minutes">
                                <option value="">su tiempo</option>
                                <option value="1">1 min</option><option value="2">2 min</option><option value="3">3 min</option>
                                <option value="5">5 min</option><option value="7">7 min</option><option value="10">10 min</option>
                            </select>
                        </div>
                        <div class="o_scout_ccall_people">
                            <button t-foreach="participants" t-as="p" t-key="p.key"
                                    t-attf-class="btn btn-sm #{p.talking ? 'o_talking' : ''} #{turn and turn.member_id == p.memberId ? 'o_holder' : ''}"
                                    t-on-click="() => this.giveFloor(p)" t-att-disabled="!p.memberId">
                                <i t-if="p.talking" class="fa fa-volume-up"/> <t t-esc="p.name"/><small t-if="p.seconds"> · <t t-esc="Math.round(p.seconds / 6) / 10"/>′</small>
                            </button>
                        </div>
                        <div class="o_scout_ccall_ctrl" t-if="turn">
                            <button t-if="state.data.state == 'paused'" class="btn btn-sm btn-light" t-on-click="() => this.control('resume')"><i class="fa fa-play"/></button>
                            <button t-else="" class="btn btn-sm btn-light" t-on-click="() => this.control('pause')"><i class="fa fa-pause"/></button>
                            <button class="btn btn-sm btn-light" t-on-click="() => this.control('add', {minutes: 1})">+1 min</button>
                            <button class="btn btn-sm btn-warning" t-on-click="() => this.control('end')">Terminó</button>
                        </div>
                        <button class="btn btn-sm btn-link text-light p-0" t-on-click="() => this.state.settings = !this.state.settings">
                            <i class="fa fa-cog"/> Configurar</button>
                        <div t-if="state.settings" class="o_scout_turns_cfg">
                            <label>Minutos por turno <input type="number" min="0.5" step="0.5" t-att-value="cfg.minutes" t-on-change="(ev) => this.saveCfg({minutes: ev.target.value})"/></label>
                            <label>Amarillo al pasar (%) <input type="number" min="1" max="99" t-att-value="cfg.yellow_pct" t-on-change="(ev) => this.saveCfg({yellow_pct: ev.target.value})"/></label>
                            <label>Rojo cuando quedan (s) <input type="number" min="0" t-att-value="cfg.red_seconds" t-on-change="(ev) => this.saveCfg({red_seconds: ev.target.value})"/></label>
                            <label>Alarma últimos (s) <input type="number" min="0" t-att-value="cfg.alarm_seconds" t-on-change="(ev) => this.saveCfg({alarm_seconds: ev.target.value})"/></label>
                            <label><input type="checkbox" t-att-checked="cfg.auto_mute" t-on-change="(ev) => this.saveCfg({auto_mute: ev.target.checked})"/> Silenciar al terminar</label>
                            <label t-if="cfg.auto_mute">Gracia (s) <input type="number" min="0" max="120" t-att-value="cfg.grace" t-on-change="(ev) => this.saveCfg({grace: ev.target.value})"/></label>
                            <label><input type="checkbox" t-att-checked="cfg.chat" t-on-change="(ev) => this.saveCfg({chat: ev.target.checked})"/> Avisos en el chat</label>
                            <label><input type="checkbox" t-att-checked="cfg.popup" t-on-change="(ev) => this.saveCfg({popup: ev.target.checked})"/> Aviso en pantalla</label>
                            <b class="d-block mt-1">Tiempo de cada participante (min, vacío = el del canal)</b>
                            <t t-foreach="participants" t-as="p" t-key="'t' + p.key">
                                <label t-if="p.memberId"><t t-esc="p.name"/>
                                    <input type="number" min="0" step="0.5" t-att-value="p.seconds ? p.seconds / 60 : ''" t-on-change="(ev) => this.control('member_time', {member_id: p.memberId, minutes: ev.target.value || 0})"/></label>
                            </t>
                            <button class="btn btn-sm btn-outline-light mt-1" t-on-click="() => this.control('disable')">Desactivar turnos</button>
                        </div>
                    </t>
                    <div t-if="state.error" class="o_scout_ccall_err" t-esc="state.error"/>
                </div>
            </div>
        </t>`;

    setup() {
        this.store = this.env.services["mail.store"];
        this.bus = this.env.services.bus_service;
        this.state = useState({ data: null, collapsed: false, settings: false, minutes: "", alert: null,
                                now: Date.now(), tickN: 0, error: "" });
        this.channelId = null;
        this.receivedAt = Date.now();
        this.lastPoll = 0;
        this.lastPhase = null;
        this.lastAlarm = null;
        this.flags = {};
        this.onUpdate = (payload) => {
            if (payload && payload.channel_id === this.channelId) {
                this.poll();
            }
        };
        this.onAlert = (payload) => this.handleAlert(payload);
        try {
            this.bus?.subscribe("scout.turn/update", this.onUpdate);
            this.bus?.subscribe("scout.turn/alert", this.onAlert);
        } catch {
            // sin bus: se usa la consulta periódica
        }
        this.timer = setInterval(() => this.tick(), 1000);
        onWillDestroy(() => {
            clearInterval(this.timer);
            try {
                this.bus?.unsubscribe("scout.turn/update", this.onUpdate);
                this.bus?.unsubscribe("scout.turn/alert", this.onAlert);
            } catch {
                // nada
            }
        });
    }

    get rtc() {
        return this.store?.rtc;
    }

    get cfg() {
        return this.state.data?.config || {};
    }

    get turn() {
        return this.state.data?.turn || null;
    }

    currentChannelId() {
        try {
            return this.rtc?.state?.channel?.id || this.rtc?.channel?.id || null;
        } catch {
            return null;
        }
    }

    tick() {
        const channelId = this.currentChannelId();
        if (channelId !== this.channelId) {
            this.channelId = channelId;
            this.state.data = null;
            this.state.alert = null;
            this.lastPhase = null;
            if (channelId) {
                this.poll();
            }
            return;
        }
        if (!channelId) {
            return;
        }
        this.state.now = Date.now();
        this.state.tickN++;
        if (Date.now() - this.lastPoll >= (this.state.data?.enabled ? 10000 : 30000)) {
            this.poll();
        }
        this.watch();
    }

    async poll() {
        this.lastPoll = Date.now();
        const channelId = this.channelId;
        try {
            const data = await rpc("/scouts/turnos/api/estado", { channel_id: channelId });
            if (channelId === this.channelId) {
                this.receivedAt = Date.now();
                this.state.data = data && (data.channel_id || data.council) ? data : null;
            }
        } catch {
            // sin acceso
        }
    }

    get elapsed() {
        const t = this.turn;
        if (!t) {
            return 0;
        }
        return t.elapsed + (this.state.data.running ? (this.state.now - this.receivedAt) / 1000 : 0);
    }

    get phase() {
        const t = this.turn;
        if (!t) {
            return "idle";
        }
        const remaining = t.duration - this.elapsed;
        if (remaining <= 0) {
            return "over";
        }
        if (remaining <= (this.cfg.red_seconds || 0)) {
            return "red";
        }
        if (this.elapsed >= t.duration * (this.cfg.yellow_pct || 50) / 100) {
            return "yellow";
        }
        return "green";
    }

    get phaseLabel() {
        return (this.state.data?.state === "paused" ? "En pausa · " : "") + LABELS[this.phase];
    }

    get clockText() {
        const t = this.turn;
        return t ? fmtClock(t.duration - this.elapsed) : "--:--";
    }

    get barPct() {
        const t = this.turn;
        return t ? Math.min(100, (this.elapsed / Math.max(1, t.duration)) * 100).toFixed(1) : 0;
    }

    get participants() {
        void this.state.tickN;
        const channel = this.rtc?.state?.channel || this.rtc?.channel;
        const members = this.state.data?.members || [];
        const list = [];
        const seen = new Set();
        for (const session of channel?.rtc_session_ids || []) {
            const memberId = session.channel_member_id?.id;
            const persona = session.persona || session.partner_id || session.guest_id;
            const name = session.name || persona?.name || "Participante";
            const key = memberId ? `m${memberId}` : `s${session.id}`;
            if (seen.has(key)) {
                continue;
            }
            seen.add(key);
            const info = members.find((m) => m.id === memberId) || {};
            list.push({ key, memberId, name, seconds: info.seconds || 0,
                        talking: Boolean(session.isTalking && !session.isMute) });
        }
        return list.sort((a, b) => (b.talking - a.talking) || a.name.localeCompare(b.name));
    }

    isMyTurn() {
        const t = this.turn;
        const local = this.rtc?.localSession || this.rtc?.selfSession;
        return Boolean(t && local && local.channel_member_id?.id === t.member_id);
    }

    notify(message, type, sticky = false) {
        try {
            this.env.services.notification?.add(message, { type, sticky });
        } catch {
            // sin notificaciones
        }
    }

    watch() {
        const t = this.turn;
        const phase = this.phase;
        const running = this.state.data?.state === "running" || this.state.data?.state === "over";
        if (!t || !running) {
            this.lastPhase = phase;
            this.state.alert = null;
            return;
        }
        const mine = this.isMyTurn();
        const moderator = this.state.data.can_moderate;
        // aviso al servidor (chat + aviso emergente, una sola vez) cuando cambia el color
        if (this.lastPhase && phase !== this.lastPhase && ["yellow", "red", "over"].includes(phase)) {
            rpc("/scouts/turnos/api/revisar", { channel_id: this.channelId }).catch(() => {});
            if (mine || moderator) {
                councilBeep(phase === "yellow" ? 1 : phase === "red" ? 2 : 3, phase === "over" ? 990 : 880);
            }
        }
        this.lastPhase = phase;
        // alarma en los últimos segundos
        const remaining = Math.ceil(t.duration - this.elapsed);
        const alarm = this.cfg.alarm_seconds || 0;
        if ((mine || moderator) && remaining > 0 && remaining <= alarm && this.lastAlarm !== remaining) {
            this.lastAlarm = remaining;
            councilBeep(1, remaining <= 3 ? 1200 : 1000);
        }
        if (!mine) {
            this.state.alert = null;
            return;
        }
        const flags = (this.flags[t.key] = this.flags[t.key] || {});
        if (phase === "red") {
            this.state.alert = { level: "red", title: "Tu tiempo se está acabando", text: `Te quedan ${Math.max(0, remaining)} s.` };
        } else if (phase === "over") {
            flags.overAt = flags.overAt || Date.now();
            const grace = this.cfg.grace || 0;
            const left = Math.max(0, Math.ceil(grace - (Date.now() - flags.overAt) / 1000));
            if (this.cfg.auto_mute && !flags.muted) {
                this.state.alert = { level: "over", title: "Tu tiempo terminó", text: left ? `Tu micrófono se silenciará en ${left} s.` : "" };
                if (left <= 0) {
                    flags.muted = true;
                    this.muteMe(t.key);
                }
            } else {
                this.state.alert = { level: "over", title: "Tu tiempo terminó",
                                     text: flags.muted ? "Se silenció tu micrófono. Pide más tiempo a quien modera." : "Cierra tu participación." };
            }
        } else {
            this.state.alert = null;
        }
    }

    handleAlert(payload) {
        if (!payload || payload.channel_id !== this.channelId) {
            return;
        }
        if (payload.popup) {
            this.notify(payload.message, payload.kind === "over" ? "danger" : "warning", payload.kind === "over");
        }
        if (payload.kind === "over") {
            councilBeep(3, 990);
        }
    }

    async muteMe(key) {
        try {
            const local = this.rtc?.localSession || this.rtc?.selfSession;
            if (local && !local.isSelfMuted && !local.is_muted) {
                await this.rtc.mute();
            }
            this.notify("Se silenció tu micrófono porque terminó tu tiempo.", "info");
            rpc("/scouts/turnos/api/revisar", { channel_id: this.channelId, muted_key: key }).catch(() => {});
        } catch {
            // queda el aviso
        }
    }

    async control(op, extra = {}) {
        try {
            const res = await rpc("/scouts/turnos/api/control", { channel_id: this.channelId, op, ...extra });
            if (res.error) {
                this.state.error = res.error;
                return;
            }
            this.state.error = "";
            this.receivedAt = Date.now();
            this.state.data = res;
        } catch (e) {
            this.state.error = e.message || String(e);
        }
    }

    giveFloor(p) {
        return this.control("floor", { member_id: p.memberId, minutes: this.state.minutes || null });
    }

    saveCfg(cfg) {
        return this.control("settings", { cfg });
    }
}

registry.category("main_components").add("scout_call_turns", { Component: ScoutCallTurns });
