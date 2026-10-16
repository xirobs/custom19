/** @odoo-module **/
// Portal de dirigentes: pase de lista, equipos, cuadro de logros, foto y pequeños atajos para el celular.

const LABELS = {
    present: ["Asistió", "ok"],
    absent: ["Faltó", "no"],
    camp: ["Campamento", "ok"],
    outing: ["Salida", "ok"],
    na: ["No aplica", "na"],
};

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

function toast(message) {
    let el = document.querySelector(".o_sl_toast");
    if (!el) {
        el = document.createElement("div");
        el.className = "o_sl_toast";
        document.body.appendChild(el);
    }
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.remove("show"), 2600);
}

// ---------------------------------------------------------------- pase de lista
function setupAttendance(grid) {
    const counter = document.getElementById("o_sl_counter");
    const apply = (card, status) => {
        const [label, kind] = LABELS[status] || [status, "na"];
        card.dataset.status = status;
        card.classList.remove("o_sl_st_ok", "o_sl_st_no", "o_sl_st_na");
        card.classList.add(`o_sl_st_${kind}`);
        card.querySelector(".o_sl_card_state").textContent = label;
    };
    const save = async (card, status) => {
        const previous = card.dataset.status;
        apply(card, status); // respuesta inmediata en pantalla
        card.classList.add("o_sl_saving");
        try {
            const result = await rpc("/dirigentes/api/asistencia", { line_id: Number(card.dataset.line), status });
            if (!result.ok) {
                apply(card, previous);
                toast(result.error);
            } else if (counter) {
                counter.innerHTML = `<b>${result.present}</b> / ${result.counted} <small>${result.pct} %</small>`;
            }
        } catch (error) {
            apply(card, previous);
            toast("Sin conexión: no se guardó. Intenta de nuevo.");
        } finally {
            card.classList.remove("o_sl_saving");
        }
    };
    grid.addEventListener("click", (ev) => {
        const tap = ev.target.closest(".o_sl_card_tap");
        if (!tap || tap.disabled) {
            return;
        }
        const card = tap.closest(".o_sl_card");
        save(card, card.dataset.status === "present" ? "absent" : "present");
    });
    grid.addEventListener("change", (ev) => {
        const select = ev.target.closest(".o_sl_more");
        if (!select || !select.value) {
            return;
        }
        save(select.closest(".o_sl_card"), select.value);
        select.value = "";
    });
}

// ---------------------------------------------------------------- equipos: mover como ficha y cambiar cargo
function setupTeams(root) {
    const sectionId = Number(root.dataset.section);
    const sheet = root.querySelector(".o_sl_sheet");
    let current = null;
    const select = (card) => {
        root.querySelectorAll(".o_sl_tcard.selected").forEach((c) => c.classList.remove("selected"));
        current = card;
        if (!card) {
            sheet.classList.remove("show");
            return;
        }
        card.classList.add("selected");
        sheet.querySelector(".o_sl_sheet_name").textContent = card.dataset.name;
        sheet.querySelectorAll("[data-move]").forEach((b) =>
            b.classList.toggle("active", b.dataset.move === (card.closest(".o_sl_team").dataset.patrol || "0"))
        );
        sheet.querySelectorAll("[data-role]").forEach((b) => b.classList.toggle("active", b.dataset.role === card.dataset.role));
        sheet.classList.add("show");
    };
    const refresh = (result) => {
        Object.entries(result.roles || {}).forEach(([id, label]) => {
            const card = root.querySelector(`.o_sl_tcard[data-member="${id}"]`);
            if (card) {
                const badge = card.querySelector(".o_sl_role");
                badge.textContent = label;
                badge.classList.toggle("d-none", !label);
                card.dataset.role = label ? card.dataset.role : "member";
            }
        });
        root.querySelectorAll(".o_sl_team").forEach((team) => {
            const count = team.querySelectorAll(".o_sl_tcard").length;
            const el = team.querySelector(".o_sl_team_count");
            if (el) {
                el.textContent = count;
            }
            team.classList.toggle("o_sl_team_empty", !count);
        });
    };
    const send = async (card, params) => {
        card.classList.add("o_sl_saving");
        try {
            const result = await rpc("/dirigentes/api/equipo", {
                section_id: sectionId,
                member_id: Number(card.dataset.member),
                ...params,
            });
            if (!result.ok) {
                toast(result.error);
                return false;
            }
            if ("patrol_id" in params) {
                const target = root.querySelector(`.o_sl_team[data-patrol="${result.patrol_id || 0}"] .o_sl_team_cards`);
                if (target) {
                    target.appendChild(card);
                }
                card.dataset.role = "member";
            }
            if (params.role) {
                card.dataset.role = params.role;
                if (params.role !== "member") {
                    const team = card.closest(".o_sl_team_cards");
                    team.prepend(card);
                }
            }
            refresh(result);
            toast("Guardado");
            return true;
        } catch (error) {
            toast("Sin conexión: no se guardó.");
            return false;
        } finally {
            card.classList.remove("o_sl_saving");
        }
    };
    root.addEventListener("click", (ev) => {
        const card = ev.target.closest(".o_sl_tcard");
        if (card) {
            select(card === current ? null : card);
            return;
        }
        const move = ev.target.closest("[data-move]");
        if (move && current) {
            send(current, { patrol_id: Number(move.dataset.move) || false }).then(() => select(current));
            return;
        }
        const role = ev.target.closest("[data-role]");
        if (role && current) {
            send(current, { role: role.dataset.role }).then(() => select(current));
            return;
        }
        if (ev.target.closest(".o_sl_sheet_close")) {
            select(null);
        }
    });
    // Arrastrar y soltar (computadora o tableta)
    root.addEventListener("dragstart", (ev) => {
        const card = ev.target.closest(".o_sl_tcard");
        if (card) {
            ev.dataTransfer.setData("text/plain", card.dataset.member);
            card.classList.add("dragging");
        }
    });
    root.addEventListener("dragend", (ev) => {
        const card = ev.target.closest(".o_sl_tcard");
        if (card) {
            card.classList.remove("dragging");
        }
    });
    root.querySelectorAll(".o_sl_team").forEach((team) => {
        team.addEventListener("dragover", (ev) => {
            ev.preventDefault();
            team.classList.add("over");
        });
        team.addEventListener("dragleave", () => team.classList.remove("over"));
        team.addEventListener("drop", (ev) => {
            ev.preventDefault();
            team.classList.remove("over");
            const card = root.querySelector(`.o_sl_tcard[data-member="${ev.dataTransfer.getData("text/plain")}"]`);
            if (card && card.closest(".o_sl_team") !== team) {
                send(card, { patrol_id: Number(team.dataset.patrol) || false });
            }
        });
    });
}

// ---------------------------------------------------------------- cuadro de logros
const PROGRESS_MARK = { none: "", in_progress: "½", achieved: "✓" };
function setupAchievements(table) {
    const sectionId = Number(table.dataset.section);
    table.addEventListener("click", async (ev) => {
        const cell = ev.target.closest("td[data-stage]");
        if (!cell || cell.classList.contains("o_sl_saving")) {
            return;
        }
        const row = cell.closest("tr");
        cell.classList.add("o_sl_saving");
        try {
            const result = await rpc("/dirigentes/api/logro", {
                section_id: sectionId,
                member_id: Number(row.dataset.member),
                stage_id: Number(cell.dataset.stage),
            });
            if (!result.ok) {
                toast(result.error);
                return;
            }
            cell.dataset.state = result.state;
            cell.textContent = PROGRESS_MARK[result.state];
            const bar = row.querySelector(".o_sl_thermo_fill");
            if (bar) {
                bar.style.width = `${result.pct}%`;
                row.querySelector(".o_sl_thermo_txt").textContent = `${result.done}/${result.total}`;
            }
        } catch (error) {
            toast("Sin conexión: no se guardó.");
        } finally {
            cell.classList.remove("o_sl_saving");
        }
    });
}

// ---------------------------------------------------------------- foto con la cámara (se reduce en el celular)
function setupPhoto(input) {
    input.addEventListener("change", () => {
        const file = input.files && input.files[0];
        if (!file) {
            return;
        }
        const form = input.closest("form");
        const reader = new FileReader();
        reader.onload = () => {
            const img = new Image();
            img.onload = () => {
                const max = 900;
                const ratio = Math.min(1, max / Math.max(img.width, img.height));
                const canvas = document.createElement("canvas");
                canvas.width = Math.round(img.width * ratio);
                canvas.height = Math.round(img.height * ratio);
                canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
                const data = canvas.toDataURL("image/jpeg", 0.85);
                form.querySelector('input[name="photo_data"]').value = data;
                const preview = form.querySelector(".o_sl_photo_preview");
                if (preview) {
                    preview.src = data;
                    preview.classList.remove("d-none");
                }
                if (form.dataset.slPhotoAutosubmit) {
                    form.submit();
                }
            };
            img.src = reader.result;
        };
        reader.readAsDataURL(file);
    });
}

// ---------------------------------------------------------------- copiar liga
function setupCopy(button) {
    button.addEventListener("click", async () => {
        try {
            await navigator.clipboard.writeText(button.dataset.slCopy);
            toast("Liga copiada");
        } catch (error) {
            window.prompt("Copia la liga:", button.dataset.slCopy);
        }
    });
}

// ---------------------------------------------------------------- atajos
function setupHelpers() {
    document.querySelectorAll("form[data-sl-autosubmit] select").forEach((select) =>
        select.addEventListener("change", () => select.form.submit())
    );
    document.querySelectorAll("select[data-sl-nav]").forEach((select) =>
        select.addEventListener("change", () => {
            if (select.value) {
                window.location = select.value;
            }
        })
    );
    document.querySelectorAll("input[data-sl-filter]").forEach((input) =>
        input.addEventListener("input", () => {
            const term = input.value.trim().toLowerCase();
            document.querySelectorAll(input.dataset.slFilter).forEach((row) => {
                row.classList.toggle("d-none", term && !row.textContent.toLowerCase().includes(term));
            });
        })
    );
}

function start() {
    if (!document.querySelector(".o_scout_leader")) {
        return;
    }
    document.querySelectorAll("[data-sl-attendance]").forEach(setupAttendance);
    document.querySelectorAll(".o_sl_teams").forEach(setupTeams);
    document.querySelectorAll("table[data-sl-achievements]").forEach(setupAchievements);
    document.querySelectorAll("input[data-sl-photo]").forEach(setupPhoto);
    document.querySelectorAll("[data-sl-copy]").forEach(setupCopy);
    setupHelpers();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
} else {
    start();
}
