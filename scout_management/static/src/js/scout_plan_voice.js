/** @odoo-module **/
// Programa de la sesión: dictado por voz (reconocimiento del propio navegador) y editor de actividades.

async function rpc(url, params) {
    const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ jsonrpc: "2.0", method: "call", params }),
    });
    const data = await response.json();
    if (data.error) {
        throw new Error((data.error.data && data.error.data.message) || data.error.message || "Error");
    }
    return data.result;
}

const esc = (value) =>
    String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function setupPlan(root) {
    const data = JSON.parse(root.dataset.plan);
    let lines = data.lines || [];
    const listEl = root.querySelector(".o_sl_plan_lines");
    const msgEl = root.querySelector(".o_sl_plan_msgs");
    const textEl = root.querySelector(".o_sl_dictation");
    const micBtn = root.querySelector(".o_sl_mic");
    const confirmWrap = root.querySelector(".o_sl_confirm_wrap");
    let dirty = false;
    let dictationLog = "";

    const showMessages = (messages, warnings, error) => {
        const parts = [];
        (messages || []).forEach((m) => parts.push(`<div class="o_sl_msg_ok"><i class="fa fa-check"></i> ${esc(m)}</div>`));
        (warnings || []).forEach((w) => parts.push(`<div class="o_sl_msg_warn"><i class="fa fa-exclamation-triangle"></i> ${esc(w)}</div>`));
        if (error) {
            parts.push(`<div class="o_sl_msg_err"><i class="fa fa-times-circle"></i> ${esc(error)}</div>`);
        }
        msgEl.innerHTML = parts.join("");
    };
    const markDirty = () => {
        dirty = true;
        if (confirmWrap && data.approved) {
            confirmWrap.classList.remove("d-none");
        }
    };
    const toMin = (v) => {
        const m = /^(\d{1,2}):(\d{2})/.exec(v || "");
        return m ? Number(m[1]) * 60 + Number(m[2]) : null;
    };
    const overlapWarnings = () => {
        const warnings = [];
        for (let i = 1; i < lines.length; i++) {
            const prevEnd = toMin(lines[i - 1].end);
            const start = toMin(lines[i].start);
            if (prevEnd !== null && start !== null && start < prevEnd) {
                warnings.push(`"${lines[i].name}" empieza (${lines[i].start}) antes de que termine "${lines[i - 1].name}" (${lines[i - 1].end}).`);
            }
        }
        return warnings;
    };

    const render = () => {
        if (!lines.length) {
            listEl.innerHTML = '<div class="text-muted small my-3">Aún no hay actividades. Dicta el programa o agrega una actividad.</div>';
            return;
        }
        const ro = data.editable ? "" : "disabled";
        listEl.innerHTML = lines
            .map((line, i) => {
                const stageOpts = ['<option value=""></option>']
                    .concat(data.stages.map(([k, l]) => `<option value="${k}" ${line.stage === k ? "selected" : ""}>${esc(l)}</option>`))
                    .join("");
                const flags = data.flags
                    .map(([k, label]) => {
                        const on = line.flags && line.flags[k];
                        const ev = line.evidence && line.evidence[k];
                        return `<button type="button" class="o_sl_flag ${on ? "on" : ""}" data-flag="${k}" ${ro}
                            title="${ev ? "Por: " + esc(ev) : on ? "Marcado por el jefe" : "Sin evidencia en el dictado"}">${on ? "✔ " : ""}${esc(label)}</button>`;
                    })
                    .join("");
                const pending = (line.pending || []).filter((p) => !(p.startsWith("Hora de inicio") && line.start) && !(p.startsWith("Hora de término") && line.end));
                return `<div class="o_sl_pline ${pending.length ? "o_sl_pline_pending" : ""}" data-i="${i}">
                    <div class="d-flex gap-2 align-items-center">
                        <input type="time" class="form-control form-control-sm" data-k="start" value="${esc(line.start)}" ${ro} aria-label="Inicio"/>
                        <span>a</span>
                        <input type="time" class="form-control form-control-sm" data-k="end" value="${esc(line.end)}" ${ro} aria-label="Fin"/>
                        ${data.editable ? `<button type="button" class="btn btn-sm o_sl_btn_outline o_sl_up" title="Subir">↑</button>
                        <button type="button" class="btn btn-sm o_sl_btn_outline o_sl_del" title="Eliminar"><i class="fa fa-trash"></i></button>` : ""}
                    </div>
                    ${pending.length ? `<div class="o_sl_pending">${pending.map(esc).join(" · ")}</div>` : ""}
                    <input class="form-control o_sl_pname mt-1" data-k="name" value="${esc(line.name)}" placeholder="Actividad" ${ro}/>
                    <div class="row g-1 mt-1">
                        <div class="col-6"><select class="form-select form-select-sm" data-k="stage" ${ro}>${stageOpts}</select></div>
                        <div class="col-6"><input class="form-control form-control-sm" data-k="kind" list="o_sl_kinds" value="${esc(line.kind)}" placeholder="Tipo" ${ro}/></div>
                        <div class="col-12"><input class="form-control form-control-sm" data-k="objective" value="${esc(line.objective)}" placeholder="Objetivo" ${ro}/></div>
                        <div class="col-6"><input class="form-control form-control-sm" data-k="material" value="${esc(line.material)}" placeholder="Material" ${ro}/></div>
                        <div class="col-6"><input class="form-control form-control-sm" data-k="responsible" list="o_sl_leaders" value="${esc(line.responsible)}" placeholder="Responsable" ${ro}/></div>
                    </div>
                    <textarea class="form-control form-control-sm mt-1" data-k="description" rows="2" placeholder="Descripción / observaciones" ${ro}>${esc(line.description)}</textarea>
                    <div class="o_sl_flags"><small>Programa</small>${flags.split("</button>").slice(0, 3).join("</button>")}</button>
                        <small>Método</small>${flags.split("</button>").slice(3, 8).join("</button>")}</button></div>
                </div>`;
            })
            .join("") +
            `<datalist id="o_sl_kinds">${data.kinds.map((k) => `<option value="${esc(k)}">`).join("")}</datalist>
             <datalist id="o_sl_leaders">${(data.leaders || []).map((k) => `<option value="${esc(k)}">`).join("")}</datalist>`;
    };

    listEl.addEventListener("input", (ev) => {
        const el = ev.target.closest("[data-k]");
        if (!el) {
            return;
        }
        const i = Number(el.closest(".o_sl_pline").dataset.i);
        lines[i][el.dataset.k] = el.value;
        markDirty();
    });
    listEl.addEventListener("change", (ev) => {
        if (ev.target.matches('input[type="time"]')) {
            showMessages([], overlapWarnings());
            render();
        }
    });
    listEl.addEventListener("click", (ev) => {
        const card = ev.target.closest(".o_sl_pline");
        if (!card || !data.editable) {
            return;
        }
        const i = Number(card.dataset.i);
        const flag = ev.target.closest(".o_sl_flag");
        if (flag) {
            lines[i].flags = lines[i].flags || {};
            lines[i].flags[flag.dataset.flag] = !lines[i].flags[flag.dataset.flag];
            markDirty();
            render();
        } else if (ev.target.closest(".o_sl_del")) {
            lines.splice(i, 1);
            markDirty();
            render();
        } else if (ev.target.closest(".o_sl_up") && i > 0) {
            [lines[i - 1], lines[i]] = [lines[i], lines[i - 1]];
            markDirty();
            render();
        }
    });

    const addBtn = root.querySelector(".o_sl_add_line");
    if (addBtn) {
        addBtn.addEventListener("click", () => {
            const last = lines[lines.length - 1];
            lines.push({ start: last ? last.end || "" : "", end: "", stage: "", kind: "", name: "", objective: "", material: "",
                responsible: "", description: "", flags: {}, evidence: {}, pending: [] });
            markDirty();
            render();
            const inputs = listEl.querySelectorAll(".o_sl_pname");
            inputs[inputs.length - 1].focus();
        });
    }

    const interpret = async () => {
        const text = (textEl.value || "").trim();
        if (!text) {
            showMessages([], [], "Dicta o escribe las actividades.");
            return;
        }
        const btn = root.querySelector(".o_sl_interpret");
        btn.disabled = true;
        try {
            const result = await rpc("/dirigentes/api/plan/interpretar", {
                section_id: data.section_id, event_id: data.event_id, text, lines,
            });
            if (!result.ok) {
                showMessages([], [], result.error);
                return;
            }
            lines = result.lines;
            dictationLog += (dictationLog ? "\n" : "") + text;
            textEl.value = "";
            markDirty();
            render();
            showMessages(result.messages, result.warnings);
        } catch (error) {
            showMessages([], [], "Sin conexión: no se pudo interpretar. Intenta de nuevo.");
        } finally {
            btn.disabled = false;
        }
    };
    const interpretBtn = root.querySelector(".o_sl_interpret");
    if (interpretBtn) {
        interpretBtn.addEventListener("click", interpret);
        root.querySelector(".o_sl_clear_text").addEventListener("click", () => (textEl.value = ""));
    }

    const saveBtn = root.querySelector(".o_sl_save_plan");
    if (saveBtn) {
        saveBtn.addEventListener("click", async () => {
            const warnings = overlapWarnings();
            if (warnings.length) {
                showMessages([], warnings.concat(["Corrige los horarios empalmados antes de guardar."]));
                return;
            }
            saveBtn.disabled = true;
            try {
                const confirmBox = root.querySelector(".o_sl_confirm_approved");
                const result = await rpc("/dirigentes/api/plan/guardar", {
                    section_id: data.section_id, event_id: data.event_id, lines, dictation: dictationLog,
                    confirm_approved: Boolean(confirmBox && confirmBox.checked),
                });
                if (!result.ok) {
                    if (confirmWrap) {
                        confirmWrap.classList.remove("d-none");
                    }
                    showMessages([], [], result.error);
                    return;
                }
                lines = result.lines;
                dirty = false;
                render();
                showMessages([`Programa guardado: ${result.count} actividades.`]);
                window.scrollTo({ top: 0, behavior: "smooth" });
            } catch (error) {
                showMessages([], [], "Sin conexión: el programa no se guardó. Intenta de nuevo.");
            } finally {
                saveBtn.disabled = false;
            }
        });
    }
    window.addEventListener("beforeunload", (ev) => {
        if (dirty) {
            ev.preventDefault();
            ev.returnValue = "";
        }
    });

    // Micrófono: reconocimiento de voz del navegador (Chrome en Android, Safari en iPhone)
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (micBtn) {
        if (!Recognition) {
            micBtn.querySelector("span").textContent = "Dictar con el micrófono del teclado";
            micBtn.addEventListener("click", () => {
                textEl.focus();
                showMessages([], ["Este navegador no permite dictar desde la página: usa el micrófono del teclado del celular y luego toca Interpretar."]);
            });
        } else {
            let recognition = null;
            let listening = false;
            let base = "";
            const stop = () => {
                listening = false;
                micBtn.classList.remove("o_sl_listening");
                micBtn.querySelector("span").textContent = "Planificar con inteligencia artificial";
            };
            micBtn.addEventListener("click", () => {
                if (listening) {
                    recognition.stop();
                    return;
                }
                recognition = new Recognition();
                recognition.lang = "es-MX";
                recognition.continuous = true;
                recognition.interimResults = true;
                base = textEl.value ? textEl.value.trim() + " " : "";
                recognition.onresult = (event) => {
                    let finalText = "";
                    let interim = "";
                    for (let i = 0; i < event.results.length; i++) {
                        const chunk = event.results[i][0].transcript;
                        if (event.results[i].isFinal) {
                            finalText += chunk.trim() + ". ";
                        } else {
                            interim += chunk;
                        }
                    }
                    textEl.value = (base + finalText + interim).replace(/\s+/g, " ");
                };
                recognition.onerror = (event) => {
                    stop();
                    if (event.error === "not-allowed") {
                        showMessages([], [], "Permite el uso del micrófono para esta página.");
                    }
                };
                recognition.onend = () => {
                    stop();
                    if (textEl.value.trim()) {
                        interpret();
                    }
                };
                recognition.start();
                listening = true;
                micBtn.classList.add("o_sl_listening");
                micBtn.querySelector("span").textContent = "Escuchando… toca para terminar";
                showMessages(["Habla de corrido; al terminar toca el botón otra vez."]);
            });
        }
    }
    render();
    showMessages([], overlapWarnings());
}

function start() {
    document.querySelectorAll(".o_sl_plan_app[data-plan]").forEach(setupPlan);
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
} else {
    start();
}
