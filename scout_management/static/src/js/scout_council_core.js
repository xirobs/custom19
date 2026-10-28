/** @odoo-module **/
/* Piezas compartidas del consejo en vivo: colores del reloj, formato de tiempo, grabación en trozos (WAV 16 kHz)
   y dictado del navegador. Se usa en la pantalla del consejo (portal) y dentro de la videollamada de Odoo. */

/** Verde al empezar, amarillo desde la mitad del tiempo, rojo en el último minuto; "over" = tiempo agotado. */
export function councilPhase(duration, elapsed) {
    const remaining = duration - elapsed;
    if (remaining <= 0) {
        return "over";
    }
    if (remaining <= 60) {
        return "red";
    }
    if (elapsed >= duration / 2) {
        return "yellow";
    }
    return "green";
}

export const PHASE_LABELS = {
    green: "A tiempo",
    yellow: "Mitad del tiempo",
    red: "Último minuto",
    over: "Tiempo agotado",
};

export function fmtClock(seconds) {
    const neg = seconds < 0;
    let s = Math.abs(Math.round(seconds));
    const h = Math.floor(s / 3600);
    s -= h * 3600;
    const m = Math.floor(s / 60);
    const sec = s % 60;
    const body = h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
        : `${m}:${String(sec).padStart(2, "0")}`;
    return (neg ? "+" : "") + body;
}

/** Pitido corto (cambio de color del reloj). */
let beepCtx = null;
export function councilBeep(times = 1, freq = 880) {
    try {
        beepCtx = beepCtx || new (window.AudioContext || window.webkitAudioContext)();
        for (let i = 0; i < times; i++) {
            const osc = beepCtx.createOscillator();
            const gain = beepCtx.createGain();
            osc.frequency.value = freq;
            gain.gain.value = 0.12;
            osc.connect(gain).connect(beepCtx.destination);
            const t = beepCtx.currentTime + i * 0.28;
            osc.start(t);
            osc.stop(t + 0.18);
        }
    } catch {
        // sin audio: no pasa nada
    }
    if (navigator.vibrate) {
        try {
            navigator.vibrate(times > 1 ? [150, 80, 150] : 150);
        } catch {
            // sin vibración
        }
    }
}

function encodeWav(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const write = (o, s) => {
        for (let i = 0; i < s.length; i++) {
            view.setUint8(o + i, s.charCodeAt(i));
        }
    };
    write(0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    write(8, "WAVE");
    write(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    write(36, "data");
    view.setUint32(40, samples.length * 2, true);
    let o = 44;
    for (let i = 0; i < samples.length; i++, o += 2) {
        const v = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(o, v < 0 ? v * 0x8000 : v * 0x7fff, true);
    }
    return new Uint8Array(buffer);
}

function toDataUrl(bytes, mimetype) {
    let binary = "";
    const step = 0x8000;
    for (let i = 0; i < bytes.length; i += step) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
    }
    return `data:${mimetype};base64,${btoa(binary)}`;
}

/**
 * Mezcla varias fuentes de audio (micrófono propio + participantes de la llamada) y entrega trozos WAV de
 * `seconds` segundos. Los trozos casi en silencio no se envían.
 */
export class AudioChunker {
    constructor({ seconds = 60, onChunk, onLevel } = {}) {
        this.seconds = seconds;
        this.onChunk = onChunk;
        this.onLevel = onLevel;
        this.sources = new Map();
        this.buffers = [];
        this.length = 0;
        this.peak = 0;
        this.speakers = [];
        this.chunkStart = Date.now();
        this.running = false;
    }

    async start() {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        this.ctx = new Ctx();
        if (this.ctx.state === "suspended") {
            await this.ctx.resume();
        }
        this.mix = this.ctx.createGain();
        this.proc = this.ctx.createScriptProcessor(4096, 1, 1);
        const mute = this.ctx.createGain();
        mute.gain.value = 0;
        this.mix.connect(this.proc);
        this.proc.connect(mute).connect(this.ctx.destination);
        this.proc.onaudioprocess = (ev) => {
            if (!this.running) {
                return;
            }
            const data = new Float32Array(ev.inputBuffer.getChannelData(0));
            let sum = 0;
            for (let i = 0; i < data.length; i += 16) {
                sum += data[i] * data[i];
            }
            const rms = Math.sqrt(sum / (data.length / 16));
            this.peak = Math.max(this.peak, rms);
            if (this.onLevel) {
                this.onLevel(rms);
            }
            this.buffers.push(data);
            this.length += data.length;
            if (this.length >= this.ctx.sampleRate * this.seconds) {
                this.flush();
            }
        };
        this.running = true;
        this.chunkStart = Date.now();
    }

    addStream(key, stream) {
        if (!this.ctx || !stream || this.sources.has(key) || !stream.getAudioTracks().length) {
            return;
        }
        try {
            const node = this.ctx.createMediaStreamSource(stream);
            node.connect(this.mix);
            this.sources.set(key, { node, stream });
        } catch {
            // flujo no válido (aún sin pistas)
        }
    }

    removeStream(key) {
        const src = this.sources.get(key);
        if (src) {
            try {
                src.node.disconnect();
            } catch {
                // ya desconectado
            }
            this.sources.delete(key);
        }
    }

    syncStreams(map) {
        for (const key of [...this.sources.keys()]) {
            const wanted = map.get(key);
            if (!wanted || wanted !== this.sources.get(key).stream) {
                this.removeStream(key);
            }
        }
        for (const [key, stream] of map) {
            this.addStream(key, stream);
        }
    }

    /** Anota quién habla (segundos desde el inicio del trozo). */
    markSpeakers(names) {
        const t = (Date.now() - this.chunkStart) / 1000;
        for (const name of names) {
            const last = [...this.speakers].reverse().find((s) => s.name === name);
            if (last && t - last.to < 2) {
                last.to = t;
            } else {
                this.speakers.push({ name, from: Math.max(0, t - 0.5), to: t });
            }
        }
    }

    flush() {
        if (!this.length) {
            return;
        }
        const rate = this.ctx.sampleRate;
        const all = new Float32Array(this.length);
        let o = 0;
        for (const b of this.buffers) {
            all.set(b, o);
            o += b.length;
        }
        const peak = this.peak;
        const speakers = this.speakers;
        const seconds = this.length / rate;
        this.buffers = [];
        this.length = 0;
        this.peak = 0;
        this.speakers = [];
        this.chunkStart = Date.now();
        if (peak < 0.008 || seconds < 3) {
            return; // silencio: no se gasta en transcribir
        }
        const target = 16000;
        const ratio = rate / target;
        const out = new Float32Array(Math.floor(all.length / ratio));
        for (let i = 0; i < out.length; i++) {
            const start = Math.floor(i * ratio);
            const end = Math.min(all.length, Math.floor((i + 1) * ratio));
            let acc = 0;
            for (let j = start; j < end; j++) {
                acc += all[j];
            }
            out[i] = acc / Math.max(1, end - start);
        }
        const dataUrl = toDataUrl(encodeWav(out, target), "audio/wav");
        if (this.onChunk) {
            this.onChunk(dataUrl, seconds, speakers);
        }
    }

    async stop() {
        if (!this.running) {
            return;
        }
        this.flush();
        this.running = false;
        for (const key of [...this.sources.keys()]) {
            this.removeStream(key);
        }
        try {
            this.proc.disconnect();
            await this.ctx.close();
        } catch {
            // ya cerrado
        }
    }
}

/** Dictado del navegador (Chrome/Edge/Safari): gratis, solo escucha el micrófono de este equipo. */
export class BrowserDictation {
    static get supported() {
        return Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
    }

    constructor({ onText, onInterim, onState } = {}) {
        this.onText = onText;
        this.onInterim = onInterim;
        this.onState = onState;
        this.active = false;
    }

    start() {
        const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!Rec) {
            throw new Error("Este navegador no tiene dictado. Usa Chrome.");
        }
        this.active = true;
        const rec = new Rec();
        rec.lang = "es-MX";
        rec.continuous = true;
        rec.interimResults = true;
        rec.onresult = (ev) => {
            let interim = "";
            for (let i = ev.resultIndex; i < ev.results.length; i++) {
                const r = ev.results[i];
                if (r.isFinal) {
                    const text = r[0].transcript.trim();
                    if (text && this.onText) {
                        this.onText(text);
                    }
                } else {
                    interim += r[0].transcript;
                }
            }
            if (this.onInterim) {
                this.onInterim(interim);
            }
        };
        rec.onend = () => {
            if (this.active) {
                setTimeout(() => this.active && this.start(), 300); // Chrome corta cada ~60 s
            } else if (this.onState) {
                this.onState(false);
            }
        };
        rec.onerror = (ev) => {
            if (ev.error === "not-allowed" || ev.error === "service-not-allowed") {
                this.active = false;
                if (this.onState) {
                    this.onState(false, "Permite el micrófono para dictar.");
                }
            }
        };
        this.rec = rec;
        rec.start();
        if (this.onState) {
            this.onState(true);
        }
    }

    stop() {
        this.active = false;
        try {
            this.rec?.stop();
        } catch {
            // ya detenido
        }
    }
}
