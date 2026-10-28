/** @odoo-module **/
/* Pantalla "Consejo en vivo" (/scouts/consejo/<id>): reloj del turno de palabra compartido con todos, orden del
   día, pase de lista / dar la palabra, transcripción (micrófono, dictado, archivo o texto) y acta. */
import { rpc } from "@web/core/network/rpc";
import { AudioChunker, BrowserDictation, councilBeep, councilPhase, fmtClock, PHASE_LABELS } from "./scout_council_core";

const CIRC = 2 * Math.PI * 52;

function esc(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
}

function readFile(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}

class CouncilScreen {
    constructor(root) {
        this.root = root;
        this.id = Number(root.dataset.meetingId);
        this.moderate = root.dataset.moderate === "1";
        this.data = null;
        this.receivedAt = Date.now();
        this.lastPhase = null;
        this.q = (sel) => root.querySelector(sel);
        root.addEventListener("click", (ev) => this.onClick(ev));
        const search = this.q("[data-scc-floor-search]");
        if (search) {
            search.addEventListener("input", () => this.filterPeople(search.value));
            search.addEventListener("keydown", (ev) => {
                if (ev.key === "Enter" && search.value.trim()) {
                    ev.preventDefault();
                    const visible = [...root.querySelectorAll(".o_scc_person")].filter((b) => !b.hidden);
                    if (visible.length === 1) {
                        this.giveFloor(visible[0].dataset.partner, visible[0].dataset.name);
                    } else {
                        this.giveFloor(false, search.value.trim());
                    }
                    search.value = "";
                    this.filterPeople("");
                }
            });
        }
        this.q("[data-scc-agenda-file]")?.addEventListener("change", (ev) => this.agendaFile(ev.target));
        this.q("[data-scc-audio-file]")?.addEventListener("change", (ev) => this.audioFile(ev.target));
        this.poll();
        setInterval(() => this.render(), 250);
        setInterval(() => this.poll(), 3000);
    }

    error(msg) {
        const box = this.q("[data-scc-err]");
        box.textContent = msg || "";
        box.classList.toggle("d-none", !msg);
        if (msg) {
            box.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
    }

    async call(route, params) {
        try {
            const res = await rpc(route, { meeting_id: this.id, ...params });
            if (res && res.error) {
                this.error(res.error);
                return null;
            }
            this.error("");
            return res;
        } catch (e) {
            this.error(e.data?.message || e.message || String(e));
            return null;
        }
    }

    async poll() {
        const res = await rpc("/scouts/consejo/api/estado", { meeting_id: this.id, full: true }).catch(() => null);
        if (res && !res.error) {
            this.apply(res);
        }
    }

    apply(data) {
        this.data = data;
        this.receivedAt = Date.now();
        this.renderStatic();
        this.render();
    }

    elapsed() {
        const t = this.data?.turn;
        if (!t) {
            return 0;
        }
        return t.elapsed + (this.data.running ? (Date.now() - this.receivedAt) / 1000 : 0);
    }

    render() {
        const d = this.data;
        if (!d) {
            return;
        }
        const clock = this.q("[data-scc-clock]");
        const t = d.turn;
        const el = this.elapsed();
        const phase = t ? councilPhase(t.duration, el) : "idle";
        clock.className = `o_scc_clock o_scc_${phase}${d.running ? "" : " o_scc_paused"}`;
        this.q("[data-scc-time]").textContent = t ? fmtClock(t.duration - el) : "--:--";
        this.q("[data-scc-phase]").textContent = t ? (d.running ? "" : "En pausa · ") + PHASE_LABELS[phase]
            : d.state === "done" ? "Reunión terminada" : d.state === "waiting" ? "Nadie tiene la palabra" : "Esperando inicio";
        const frac = t ? Math.min(1, el / Math.max(1, t.duration)) : 0;
        const ring = this.q("[data-scc-ring]");
        ring.style.strokeDasharray = `${CIRC}`;
        ring.style.strokeDashoffset = `${CIRC * frac}`;
        if (d.session_elapsed != null && d.state !== "idle") {
            const sess = d.session_elapsed + (d.state !== "done" ? (Date.now() - this.receivedAt) / 1000 : 0);
            let txt = `Reunión: ${fmtClock(sess)} de ${Math.round(d.planned_total / 60)} min`;
            if (d.drift != null) {
                const drift = d.drift + (d.running ? (Date.now() - this.receivedAt) / 1000 : 0);
                if (Math.abs(drift) >= 60) {
                    txt += drift > 0 ? ` · ${Math.round(drift / 60)} min atrasados` : ` · ${Math.round(-drift / 60)} min adelantados`;
                }
            }
            this.q("[data-scc-total]").textContent = txt;
        }
        if (this.lastPhase && phase !== this.lastPhase && d.running) {
            if (phase === "yellow") {
                councilBeep(1, 660);
            } else if (phase === "red") {
                councilBeep(2, 880);
            } else if (phase === "over") {
                councilBeep(3, 990);
            }
        }
        this.lastPhase = phase;
    }

    renderStatic() {
        const d = this.data;
        this.q("[data-scc-speaker]").textContent = d.turn ? d.turn.speaker || "Sin nombre" : "—";
        this.q("[data-scc-point]").textContent = d.point ? `Punto ${d.index + 1} de ${d.count}: ${d.point.name}` : "";
        this.q("[data-scc-next]").textContent = d.next
            ? `Sigue: ${d.next.name}${d.next.responsible ? " · " + d.next.responsible : ""}` : "";
        // firma
        const sign = this.q("[data-scc-sign]");
        if (d.my_checkin) {
            sign.innerHTML = `<span class="o_scc_signed"><i class="fa fa-check-circle"></i> Asistencia firmada ${esc(d.my_checkin.time)}
                <small>(${esc(d.my_checkin.source)} · folio ${esc(d.my_checkin.signature)})</small></span>`;
        }
        // orden del día
        const ol = this.q("[data-scc-agenda]");
        ol.innerHTML = (d.lines || []).map((l) => `
            <li class="o_scc_ag_${l.status}" data-line="${l.id}">
                <span class="o_scc_ag_start">${esc(l.start)}</span>
                <span class="o_scc_ag_name">${esc(l.name)}${l.responsible ? `<small> · ${esc(l.responsible)}</small>` : ""}</span>
                <span class="o_scc_ag_min">${l.spent ? `<b class="${l.spent > l.duration * 60 ? "text-danger" : ""}">${fmtClock(l.spent)}</b> / ` : ""}${l.duration} min</span>
                ${this.moderate ? `<button type="button" class="btn btn-sm btn-link p-0 ms-1" data-scc-goto="${l.id}" title="Ir a este punto"><i class="fa fa-play-circle"></i></button>` : ""}
            </li>`).join("") || `<small class="text-muted">Sin puntos todavía.</small>`;
        this.q("[data-scc-plan]").textContent = d.planned_total ? `${Math.round(d.planned_total / 60)} min planeados` : "";
        // tiempos por persona
        const sp = this.q("[data-scc-speakers]");
        if (d.speakers && d.speakers.length) {
            sp.innerHTML = `<table class="table table-sm mb-0"><tbody>${d.speakers.map((s) => `
                <tr><td>${esc(s.name)}</td><td class="text-end">${fmtClock(s.seconds)}</td></tr>`).join("")}</tbody></table>
                <details class="mt-1"><summary class="small">Turnos (${d.turns.length})</summary>${d.turns.map((t) => `
                <div class="small ${t.over ? "text-danger" : ""}">${esc(t.time)} · ${esc(t.speaker || "Sin nombre")} ·
                ${fmtClock(t.seconds)} de ${fmtClock(t.planned)}${t.point ? ` · ${esc(t.point)}` : ""}</div>`).join("")}</details>`;
        }
        // pase de lista: marca quién tiene la palabra
        for (const btn of this.root.querySelectorAll(".o_scc_person")) {
            btn.classList.toggle("o_holder", Boolean(d.turn && d.turn.partner_id && String(d.turn.partner_id) === btn.dataset.partner));
        }
        // controles según estado
        const ctrl = this.q("[data-scc-ctrl]");
        if (ctrl) {
            const show = (op, on) => {
                const b = ctrl.querySelector(`[data-scc-op="${op}"]`);
                if (b) {
                    b.hidden = !on;
                }
            };
            const live = ["running", "paused"].includes(d.state);
            show("start", !d.running);
            ctrl.querySelector('[data-scc-op="start"]').innerHTML = d.state === "paused"
                ? '<i class="fa fa-play"></i> Reanudar' : d.state === "waiting"
                    ? '<i class="fa fa-play"></i> Turno del punto' : '<i class="fa fa-play"></i> Iniciar';
            show("pause", d.running);
            show("add", live);
            show("end_turn", live);
            show("prev", d.state !== "idle");
            show("next", d.state !== "idle" && d.state !== "done");
        }
        const texts = this.q("[data-scc-texts]");
        if (texts && d.last_text) {
            texts.innerHTML = d.last_text.map((t) => `<div class="o_scc_tline"><small>${esc(t.time)}${t.point ? " · " + esc(t.point) : ""}</small> ${esc(t.text)}</div>`).join("");
        }
        const count = this.q("[data-scc-tcount]");
        if (count) {
            count.textContent = d.transcript_count ? `${d.transcript_count} trozos transcritos` : "Sin transcripción todavía";
        }
        const rec = this.q("[data-scc-rec]");
        if (rec && !this.chunker && !this.dictation) {
            rec.innerHTML = d.recording ? `<span class="text-danger"><i class="fa fa-circle"></i> ${esc(d.recording)} está transcribiendo</span>` : "";
        }
    }

    filterPeople(text) {
        const q = text.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
        for (const btn of this.root.querySelectorAll(".o_scc_person")) {
            const name = btn.dataset.name.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
            btn.hidden = Boolean(q) && !name.includes(q);
        }
    }

    minutes() {
        const sel = this.q("[data-scc-minutes]");
        if (!sel || sel.value === "point") {
            return this.data?.point ? this.data.point.planned / 60 : 3;
        }
        return Number(sel.value);
    }

    async control(op, extra = {}) {
        if (op === "reset" && !window.confirm("¿Reiniciar el reloj? Se borran los tiempos y turnos registrados.")) {
            return;
        }
        if (op === "finish" && !window.confirm("¿Terminar la reunión?")) {
            return;
        }
        const res = await this.call("/scouts/consejo/api/control", { op, ...extra });
        if (res) {
            this.apply(res);
        }
    }

    giveFloor(partnerId, name) {
        return this.control("floor", { partner_id: partnerId ? Number(partnerId) : false, name, minutes: this.minutes() });
    }

    async onClick(ev) {
        const opBtn = ev.target.closest("[data-scc-op]");
        if (opBtn) {
            return this.control(opBtn.dataset.sccOp);
        }
        const gotoBtn = ev.target.closest("[data-scc-goto]");
        if (gotoBtn) {
            return this.control("goto", { line_id: Number(gotoBtn.dataset.sccGoto) });
        }
        const person = ev.target.closest(".o_scc_person");
        if (person) {
            return this.giveFloor(person.dataset.partner, person.dataset.name);
        }
        const btn = ev.target.closest("[data-scc]");
        if (!btn) {
            return;
        }
        const action = btn.dataset.scc;
        if (action === "sign") {
            const res = await this.call("/scouts/consejo/api/firmar", {});
            if (res) {
                this.poll();
            }
        } else if (action === "videocall") {
            btn.disabled = true;
            const res = await this.call("/scouts/consejo/api/videollamada", {});
            btn.disabled = false;
            if (res?.url) {
                window.location.reload();
            }
        } else if (action === "fullscreen") {
            const el = this.q("[data-scc-clock]");
            if (document.fullscreenElement) {
                document.exitFullscreen();
            } else if (el.requestFullscreen) {
                el.requestFullscreen().catch(() => el.classList.toggle("o_scc_big"));
            } else {
                el.classList.toggle("o_scc_big");
            }
        } else if (action === "edit-agenda") {
            const lines = this.data?.lines || [];
            this.q("[data-scc-agenda-text]").value = lines.map((l) => `${l.name} | ${l.duration}${l.responsible ? " | " + l.responsible : ""}`).join("\n");
            this.q("[data-scc-editor]").classList.remove("d-none");
        } else if (action === "cancel-agenda") {
            this.q("[data-scc-editor]").classList.add("d-none");
        } else if (action === "save-agenda") {
            const res = await this.call("/scouts/consejo/api/agenda", { text: this.q("[data-scc-agenda-text]").value });
            if (res) {
                this.q("[data-scc-editor]").classList.add("d-none");
                this.apply(res);
            }
        } else if (action === "template") {
            const res = await this.call("/scouts/consejo/api/agenda", { template: true, preview: true });
            if (res?.points) {
                this.q("[data-scc-agenda-text]").value = res.points.map((p) => `${p.name} | ${p.minutes}`).join("\n");
            }
        } else if (action === "mic") {
            return this.chunker ? this.stopMic() : this.startMic();
        } else if (action === "dictation") {
            return this.dictation ? this.stopDictation() : this.startDictation(btn);
        } else if (action === "paste") {
            this.q("[data-scc-paste-box]").classList.toggle("d-none");
        } else if (action === "paste-save") {
            const area = this.q("[data-scc-paste-text]");
            const res = await this.call("/scouts/consejo/api/texto", { text: area.value, source: "manual" });
            if (res) {
                area.value = "";
                this.q("[data-scc-paste-box]").classList.add("d-none");
                this.poll();
            }
        } else if (action === "minutes") {
            btn.disabled = true;
            btn.innerHTML = '<i class="fa fa-spinner fa-spin"></i> Generando acta…';
            const res = await this.call("/scouts/consejo/api/acta", {});
            btn.disabled = false;
            btn.innerHTML = '<i class="fa fa-magic"></i> Generar acta';
            if (res) {
                const out = this.q("[data-scc-minutes-out]");
                out.classList.remove("d-none");
                out.innerHTML = `${res.summary ? `<h6>Resumen</h6>${res.summary}` : ""}<h6>Desarrollo</h6>${res.minutes || ""}
                    ${res.agreements?.length ? `<h6>Acuerdos</h6><ol>${res.agreements.map((a) => `<li>${esc(a.name)}${a.responsible ? ` <small>— ${esc(a.responsible)}</small>` : ""}${a.due ? ` <small>(${esc(a.due)})</small>` : ""}</li>`).join("")}</ol>` : ""}
                    <p class="small text-muted">El acta quedó en borrador para revisarla y enviarla a aprobación.</p>`;
            }
        }
    }

    async agendaFile(input) {
        const file = input.files[0];
        input.value = "";
        if (!file) {
            return;
        }
        this.q("[data-scc-plan]").textContent = "Leyendo el archivo…";
        const data = await readFile(file);
        const res = await this.call("/scouts/consejo/api/agenda", { file: data, preview: true });
        this.q("[data-scc-plan]").textContent = "";
        if (res?.points) {
            this.q("[data-scc-agenda-text]").value = res.points.map((p) => `${p.name} | ${p.minutes}${p.responsible ? " | " + p.responsible : ""}`).join("\n");
        }
    }

    async audioFile(input) {
        const file = input.files[0];
        input.value = "";
        if (!file) {
            return;
        }
        if (file.size > 20 * 1024 * 1024) {
            this.error("El audio pesa más de 20 MB; súbelo en partes.");
            return;
        }
        const rec = this.q("[data-scc-rec]");
        rec.innerHTML = '<i class="fa fa-spinner fa-spin"></i> Transcribiendo el archivo…';
        const data = await readFile(file);
        const res = await this.call("/scouts/consejo/api/audio", { data, mimetype: file.type || "audio/mpeg", source: "file" });
        rec.textContent = res ? (res.text ? "Archivo transcrito." : "No se escuchó nada en el archivo.") : "";
        this.poll();
    }

    // ---------------------------------------------------- micrófono de este equipo
    async startMic() {
        let stream;
        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
        } catch {
            this.error("Permite el micrófono para grabar.");
            return;
        }
        const on = await this.call("/scouts/consejo/api/grabar", { on: true });
        if (!on) {
            stream.getTracks().forEach((t) => t.stop());
            return;
        }
        this.micStream = stream;
        const rec = this.q("[data-scc-rec]");
        this.chunker = new AudioChunker({
            seconds: 60,
            onLevel: (rms) => {
                rec.innerHTML = `<span class="text-danger"><i class="fa fa-circle"></i> Grabando</span>
                    <span class="o_scc_level"><span style="width:${Math.min(100, rms * 400)}%"></span></span>`;
            },
            onChunk: async (dataUrl) => {
                const speaker = this.data?.turn?.speaker;
                const res = await this.call("/scouts/consejo/api/audio", {
                    data: dataUrl, mimetype: "audio/wav", source: "mic",
                    speakers: speaker ? [{ name: speaker, from: 0, to: 60 }] : [],
                });
                if (res) {
                    this.poll();
                }
            },
        });
        await this.chunker.start();
        this.chunker.addStream("mic", stream);
        this.q('[data-scc="mic"]').innerHTML = '<i class="fa fa-stop"></i> Detener grabación';
    }

    async stopMic() {
        const chunker = this.chunker;
        this.chunker = null;
        await chunker.stop();
        this.micStream?.getTracks().forEach((t) => t.stop());
        this.q('[data-scc="mic"]').innerHTML = '<i class="fa fa-microphone"></i> Grabar con este equipo';
        this.q("[data-scc-rec]").textContent = "";
        this.call("/scouts/consejo/api/grabar", { on: false });
    }

    // ---------------------------------------------------- dictado del navegador
    startDictation(btn) {
        if (!BrowserDictation.supported) {
            this.error("Este navegador no tiene dictado; usa Chrome.");
            return;
        }
        const rec = this.q("[data-scc-rec]");
        this.buffer = [];
        this.dictation = new BrowserDictation({
            onText: (text) => {
                this.buffer.push(text);
                if (this.buffer.join(" ").length > 400) {
                    this.flushDictation();
                }
            },
            onInterim: (text) => {
                rec.innerHTML = `<span class="text-danger"><i class="fa fa-circle"></i> Dictando</span> <i>${esc(text)}</i>`;
            },
            onState: (on, msg) => {
                if (!on) {
                    this.stopDictation();
                    if (msg) {
                        this.error(msg);
                    }
                }
            },
        });
        this.dictation.start();
        this.dictTimer = setInterval(() => this.flushDictation(), 30000);
        btn.innerHTML = '<i class="fa fa-stop"></i> Detener dictado';
    }

    flushDictation() {
        if (!this.buffer?.length) {
            return;
        }
        const speaker = this.data?.turn?.speaker;
        const text = (speaker ? speaker + ": " : "") + this.buffer.join(" ");
        this.buffer = [];
        this.call("/scouts/consejo/api/texto", { text, source: "browser" }).then(() => this.poll());
    }

    stopDictation() {
        if (!this.dictation) {
            return;
        }
        this.dictation.stop();
        this.dictation = null;
        clearInterval(this.dictTimer);
        this.flushDictation();
        this.q('[data-scc="dictation"]').innerHTML = '<i class="fa fa-commenting-o"></i> Dictado del navegador';
        this.q("[data-scc-rec]").textContent = "";
    }
}

function boot() {
    const root = document.querySelector(".o_scc[data-meeting-id]");
    if (root && !root.dataset.sccReady) {
        root.dataset.sccReady = "1";
        new CouncilScreen(root);
    }
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
} else {
    boot();
}
