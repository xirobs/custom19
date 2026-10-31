/** @odoo-module **/
/* Reloj del consejo y transcripción DENTRO de la videollamada de Odoo.
   Si la llamada en curso es la del evento de un consejo, aparece un recuadro con el punto en curso y el reloj
   (verde / amarillo / rojo). Quien modera puede avanzar los puntos y "Transcribir la llamada": se graba el audio de
   todos los participantes (no solo el propio) y cada minuto se manda a transcribir con la IA del grupo.
   La asistencia se firma sola en el servidor al entrar a la llamada; aquí solo se muestra. */
import { Component, onWillDestroy, useState, xml } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { AudioChunker, councilBeep, councilPhase, fmtClock, PHASE_LABELS } from "./scout_council_core";

export class ScoutCouncilCall extends Component {
    static props = ["*"];
    static template = xml`
        <div t-if="state.data and state.data.id" t-attf-class="o_scout_ccall o_scout_ccall_#{phase} #{state.collapsed ? 'o_collapsed' : ''}">
            <div class="o_scout_ccall_head" t-on-click="toggle">
                <span class="o_scout_ccall_dot"/>
                <span class="o_scout_ccall_clock" t-esc="clockText"/>
                <span class="o_scout_ccall_title" t-esc="headTitle"/>
                <i t-attf-class="fa #{state.collapsed ? 'fa-chevron-up' : 'fa-chevron-down'} ms-auto"/>
            </div>
            <div t-if="state.alert" t-attf-class="o_scout_ccall_alert o_scout_ccall_alert_#{state.alert.level}">
                <b t-esc="state.alert.title"/>
                <div t-if="state.alert.text" t-esc="state.alert.text"/>
            </div>
            <div t-if="!state.collapsed" class="o_scout_ccall_body">
                <div class="o_scout_ccall_bar"><span t-attf-style="width: #{barPct}%"/></div>
                <div class="o_scout_ccall_meta">
                    <span t-esc="phaseLabel"/>
                    <span t-if="state.data.turn"> · <t t-esc="turnTotal"/> min</span>
                </div>
                <div t-if="state.data.point" class="o_scout_ccall_next">Punto <t t-esc="state.data.index + 1"/>/<t t-esc="state.data.count"/>: <t t-esc="state.data.point.name"/></div>
                <div class="o_scout_ccall_sign">
                    <t t-if="state.data.my_checkin"><i class="fa fa-check-circle"/> Asistencia firmada <t t-esc="state.data.my_checkin.time"/></t>
                    <t t-else=""><i class="fa fa-clock-o"/> Firmando asistencia…</t>
                    <span t-if="state.data.recording" class="o_scout_ccall_rec"><i class="fa fa-circle"/> Transcribiendo</span>
                </div>
                <t t-if="state.data.can_moderate">
                    <div class="o_scout_ccall_label">Dar la palabra
                        <select class="o_scout_ccall_min" t-model="state.minutes">
                            <option value="1">1 min</option><option value="2">2 min</option><option value="3">3 min</option>
                            <option value="5">5 min</option><option value="7">7 min</option><option value="10">10 min</option>
                            <option value="point">lo del punto</option>
                        </select>
                    </div>
                    <div class="o_scout_ccall_people">
                        <button t-foreach="participants" t-as="p" t-key="p.key"
                                t-attf-class="btn btn-sm #{p.talking ? 'o_talking' : ''} #{isHolder(p) ? 'o_holder' : ''}"
                                t-on-click="() => this.giveFloor(p)" t-att-title="p.talking ? 'Está hablando' : 'Darle la palabra'">
                            <i t-if="p.talking" class="fa fa-volume-up"/> <t t-esc="p.name"/>
                        </button>
                    </div>
                    <div class="o_scout_ccall_ctrl">
                        <button t-if="state.data.state == 'idle' or state.data.state == 'done'" class="btn btn-sm btn-success" t-on-click="() => this.control('start')"><i class="fa fa-play"/> Iniciar</button>
                        <t t-else="">
                            <t t-if="state.data.turn">
                                <button t-if="state.data.running" class="btn btn-sm btn-light" title="Pausa" t-on-click="() => this.control('pause')"><i class="fa fa-pause"/></button>
                                <button t-else="" class="btn btn-sm btn-light" title="Reanudar" t-on-click="() => this.control('start')"><i class="fa fa-play"/></button>
                                <button class="btn btn-sm btn-light" title="+1 minuto" t-on-click="() => this.control('add')">+1</button>
                                <button class="btn btn-sm btn-warning" t-on-click="() => this.control('end_turn')">Terminó</button>
                            </t>
                            <button class="btn btn-sm btn-primary" t-on-click="() => this.control('next')">Sig. punto <i class="fa fa-step-forward"/></button>
                        </t>
                    </div>
                    <div class="o_scout_ccall_opts">
                        <label><input type="checkbox" t-att-checked="state.data.auto_mute" t-on-change="(ev) => this.saveSettings({auto_mute: ev.target.checked})"/> Silenciar al terminar</label>
                        <label t-if="state.data.auto_mute">gracia
                            <select t-on-change="(ev) => this.saveSettings({grace: Number(ev.target.value)})">
                                <option t-foreach="[0, 5, 10, 15, 30]" t-as="g" t-key="g" t-att-value="g" t-att-selected="g == state.data.grace" t-esc="g + ' s'"/>
                            </select></label>
                        <label><input type="checkbox" t-att-checked="state.data.chat_notices" t-on-change="(ev) => this.saveSettings({chat_notices: ev.target.checked})"/> Avisos en el chat</label>
                    </div>
                    <div class="o_scout_ccall_rec_row">
                        <t t-if="state.recording">
                            <button class="btn btn-sm btn-danger" t-on-click="stopRecording"><i class="fa fa-stop"/> Detener transcripción</button>
                            <span class="o_scout_ccall_level"><span t-attf-style="width: #{Math.min(100, state.level * 400)}%"/></span>
                        </t>
                        <button t-elif="state.data.ai_audio" class="btn btn-sm btn-outline-light" t-on-click="startRecording"><i class="fa fa-microphone"/> Transcribir la llamada</button>
                        <span t-else="" class="small">Para transcribir configura la IA (Gemini u OpenAI).</span>
                    </div>
                </t>
                <div t-if="state.lastText" class="o_scout_ccall_text" t-esc="state.lastText"/>
                <div t-if="state.error" class="o_scout_ccall_err" t-esc="state.error"/>
                <a class="o_scout_ccall_link" t-att-href="state.data.live_url" target="_blank">Abrir el consejo en pantalla completa <i class="fa fa-external-link"/></a>
            </div>
        </div>`;

    setup() {
        this.store = this.env.services["mail.store"];
        this.state = useState({ data: null, collapsed: false, recording: false, level: 0, error: "", lastText: "",
                                now: Date.now(), minutes: "3", tickN: 0, alert: null });
        this.turnFlags = {};
        this.channelId = null;
        this.receivedAt = Date.now();
        this.lastPoll = 0;
        this.lastPhase = null;
        this.trackStreams = new Map();
        this.timer = setInterval(() => this.tick(), 1000);
        onWillDestroy(() => {
            clearInterval(this.timer);
            this.stopRecording();
        });
    }

    get rtc() {
        return this.store?.rtc;
    }

    currentChannelId() {
        try {
            return this.rtc?.state?.channel?.id || this.rtc?.channel?.id || null;
        } catch {
            return null;
        }
    }

    async tick() {
        const channelId = this.currentChannelId();
        if (channelId !== this.channelId) {
            this.channelId = channelId;
            this.state.data = null;
            this.lastPhase = null;
            if (!channelId && this.state.recording) {
                this.stopRecording();
            }
            if (channelId) {
                this.poll(true);
            }
            return;
        }
        if (!channelId) {
            return;
        }
        this.state.now = Date.now();
        this.state.tickN++;
        const every = this.state.data && this.state.data.id ? 3000 : 20000;
        if (Date.now() - this.lastPoll >= every) {
            this.poll();
        }
        if (this.state.recording) {
            this.syncAudio();
        }
        this.watchPhase();
    }

    async poll(first = false) {
        this.lastPoll = Date.now();
        const channelId = this.channelId;
        try {
            const data = await rpc("/scouts/consejo/api/canal", { channel_id: channelId });
            if (channelId !== this.channelId) {
                return;
            }
            this.receivedAt = Date.now();
            this.state.data = data && data.id ? data : null;
            if (first && this.state.data && !this.state.data.my_checkin) {
                const res = await rpc("/scouts/consejo/api/firmar", { meeting_id: data.id });
                if (res && res.my_checkin) {
                    this.state.data.my_checkin = res.my_checkin;
                }
            }
        } catch {
            // sin acceso o sin red: el recuadro no aparece
        }
    }

    get elapsed() {
        const t = this.state.data?.turn;
        if (!t) {
            return 0;
        }
        return t.elapsed + (this.state.data.running ? (this.state.now - this.receivedAt) / 1000 : 0);
    }

    get phase() {
        const t = this.state.data?.turn;
        if (!t) {
            return "idle";
        }
        return councilPhase(t.duration, this.elapsed);
    }

    get phaseLabel() {
        const d = this.state.data;
        if (!d.turn) {
            return d.state === "done" ? "Reunión terminada" : d.state === "waiting" ? "Nadie tiene la palabra"
                : "Esperando inicio";
        }
        return (d.running ? "" : "En pausa · ") + PHASE_LABELS[this.phase];
    }

    get clockText() {
        const t = this.state.data?.turn;
        return t ? fmtClock(t.duration - this.elapsed) : "--:--";
    }

    get turnTotal() {
        return Math.round((this.state.data.turn.duration / 60) * 10) / 10;
    }

    get headTitle() {
        const d = this.state.data;
        if (d.turn) {
            return d.turn.speaker ? `Habla: ${d.turn.speaker}` : d.point?.name || d.title;
        }
        return d.state === "done" ? "Reunión terminada" : d.point?.name || d.title;
    }

    get barPct() {
        const t = this.state.data?.turn;
        return t ? Math.min(100, (this.elapsed / Math.max(1, t.duration)) * 100).toFixed(1) : 0;
    }

    /** Participantes de la llamada (para darles la palabra), el que habla primero. */
    get participants() {
        void this.state.tickN; // se recalcula cada segundo
        const rtc = this.rtc;
        const channel = rtc?.state?.channel || rtc?.channel;
        const list = [];
        const seen = new Set();
        for (const session of channel?.rtc_session_ids || []) {
            const persona = session.persona || session.partner_id || session.guest_id;
            const name = session.name || persona?.name || "Participante";
            const partnerId = session.partner_id?.id || (persona?.type === "partner" ? persona.id : false);
            const key = partnerId ? `p${partnerId}` : `s${session.id}`;
            if (seen.has(key)) {
                continue;
            }
            seen.add(key);
            list.push({ key, name, partnerId, talking: Boolean(session.isTalking && !session.isMute) });
        }
        return list.sort((a, b) => (b.talking - a.talking) || a.name.localeCompare(b.name));
    }

    isHolder(p) {
        const t = this.state.data?.turn;
        return Boolean(t && ((p.partnerId && t.partner_id === p.partnerId) || (!p.partnerId && t.speaker === p.name)));
    }

    async giveFloor(p) {
        const minutes = this.state.minutes === "point"
            ? (this.state.data.point ? this.state.data.point.planned / 60 : 3) : Number(this.state.minutes);
        await this.control("floor", { partner_id: p.partnerId || false, name: p.name, minutes });
    }

    /** ¿Soy yo quien tiene la palabra? (por mi contacto, o por nombre si entré como invitado) */
    isMyTurn() {
        const t = this.state.data?.turn;
        if (!t) {
            return false;
        }
        const rtc = this.rtc;
        const local = rtc?.localSession || rtc?.selfSession;
        const myPartner = local?.partner_id?.id;
        if (t.partner_id) {
            return Boolean(myPartner) && t.partner_id === myPartner;
        }
        const myName = (local?.name || local?.persona?.name || "").trim().toLowerCase();
        return Boolean(myName && t.speaker && myName === t.speaker.trim().toLowerCase());
    }

    notify(message, type) {
        try {
            this.env.services.notification?.add(message, { type, sticky: false });
        } catch {
            // sin servicio de notificaciones
        }
    }

    watchPhase() {
        const phase = this.phase;
        const d = this.state.data;
        const t = d?.turn;
        if (this.lastPhase && phase !== this.lastPhase && d?.running) {
            if (phase === "yellow") {
                councilBeep(1, 660);
            } else if (phase === "red") {
                councilBeep(2, 880);
            } else if (phase === "over") {
                councilBeep(3, 990);
            }
        }
        this.lastPhase = phase;
        if (!t || !d.running) {
            this.state.alert = null;
            return;
        }
        const flags = (this.turnFlags[t.key] = this.turnFlags[t.key] || {});
        const mine = this.isMyTurn();
        if (phase !== "over") {
            flags.overAt = null;
            if (mine && phase === "yellow" && !flags.half) {
                flags.half = true;
                this.notify("Llevas la mitad de tu tiempo.", "warning");
            }
            if (mine && phase === "red" && !flags.last) {
                flags.last = true;
                this.state.alert = { level: "red", title: "Te queda 1 minuto", text: "Ve cerrando tu participación." };
                this.notify("Te queda 1 minuto para cerrar.", "warning");
            }
            if (phase === "green" || phase === "yellow") {
                this.state.alert = null;
            }
            return;
        }
        // Tiempo agotado
        if (!flags.overAt) {
            flags.overAt = Date.now();
            rpc("/scouts/consejo/api/agotado", { meeting_id: d.id, key: t.key }).catch(() => {});
        }
        if (!mine) {
            this.state.alert = null;
            return;
        }
        const grace = Number(d.grace || 0);
        const left = Math.max(0, Math.ceil(grace - (Date.now() - flags.overAt) / 1000));
        if (!flags.overNotified) {
            flags.overNotified = true;
            this.notify("Tu tiempo terminó. Cierra tu participación.", "danger");
        }
        if (d.auto_mute && !flags.muted) {
            this.state.alert = { level: "over", title: "Tu tiempo terminó",
                                 text: left ? `Tu micrófono se silenciará en ${left} s.` : "" };
            if (left <= 0) {
                flags.muted = true;
                this.muteMe();
            }
        } else {
            this.state.alert = { level: "over", title: "Tu tiempo terminó",
                                 text: flags.muted ? "Se silenció tu micrófono. Pide más tiempo a quien modera." : "Cierra tu participación." };
        }
    }

    async muteMe() {
        try {
            const rtc = this.rtc;
            const local = rtc?.localSession || rtc?.selfSession;
            if (local && !local.isSelfMuted && !local.is_muted) {
                await rtc.mute();
            }
            this.notify("Se silenció tu micrófono porque terminó tu tiempo.", "info");
        } catch {
            // si no se puede silenciar, queda el aviso
        }
    }

    async saveSettings(cfg) {
        try {
            const res = await rpc("/scouts/consejo/api/ajustes", { meeting_id: this.state.data.id, ...cfg });
            if (res.error) {
                this.state.error = res.error;
            } else {
                this.poll();
            }
        } catch (e) {
            this.state.error = e.message || String(e);
        }
    }

    toggle() {
        this.state.collapsed = !this.state.collapsed;
    }

    async control(op, extra = {}) {
        try {
            const data = await rpc("/scouts/consejo/api/control", { meeting_id: this.state.data.id, op, ...extra });
            if (data.error) {
                this.state.error = data.error;
                return;
            }
            this.state.error = "";
            this.poll();
        } catch (e) {
            this.state.error = e.message || String(e);
        }
    }

    // ------------------------------------------------------------ transcripción de la llamada
    async startRecording() {
        const meetingId = this.state.data.id;
        const res = await rpc("/scouts/consejo/api/grabar", { meeting_id: meetingId, on: true });
        if (res.error) {
            this.state.error = res.error;
            return;
        }
        this.state.error = "";
        this.chunker = new AudioChunker({
            seconds: 60,
            onLevel: (rms) => (this.state.level = rms),
            onChunk: (dataUrl, seconds, speakers) => this.sendChunk(meetingId, dataUrl, speakers),
        });
        await this.chunker.start();
        this.state.recording = true;
        this.syncAudio();
    }

    syncAudio() {
        if (!this.chunker) {
            return;
        }
        const rtc = this.rtc;
        const channel = rtc?.state?.channel || rtc?.channel;
        const map = new Map();
        const talking = [];
        const track = rtc?.state?.audioTrack;
        if (track) {
            if (!this.trackStreams.has(track.id)) {
                this.trackStreams.set(track.id, new MediaStream([track]));
            }
            map.set("self", this.trackStreams.get(track.id));
        }
        const local = rtc?.localSession || rtc?.selfSession;
        for (const session of channel?.rtc_session_ids || []) {
            if (local && session.id === local.id) {
                if (session.isTalking && !session.isMute) {
                    talking.push(session.name || session.persona?.name || "Yo");
                }
                continue;
            }
            if (session.audioStream) {
                map.set(`s${session.id}`, session.audioStream);
            }
            if (session.isTalking) {
                talking.push(session.name || session.persona?.name || "Participante");
            }
        }
        this.chunker.syncStreams(map);
        if (talking.length) {
            this.chunker.markSpeakers(talking);
        }
    }

    async sendChunk(meetingId, dataUrl, speakers) {
        try {
            const res = await rpc("/scouts/consejo/api/audio", {
                meeting_id: meetingId, data: dataUrl, mimetype: "audio/wav", speakers, source: "call",
            });
            if (res.error) {
                this.state.error = res.error;
            } else {
                this.state.error = "";
                if (res.text) {
                    this.state.lastText = res.text.slice(-220);
                }
            }
        } catch (e) {
            this.state.error = "No se pudo enviar el audio: " + (e.message || e);
        }
    }

    async stopRecording() {
        if (!this.chunker) {
            return;
        }
        const chunker = this.chunker;
        this.chunker = null;
        this.state.recording = false;
        await chunker.stop();
        const id = this.state.data?.id;
        if (id) {
            rpc("/scouts/consejo/api/grabar", { meeting_id: id, on: false }).catch(() => {});
        }
    }
}

registry.category("main_components").add("scout_council_call", { Component: ScoutCouncilCall });
