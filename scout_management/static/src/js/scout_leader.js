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
        const ficha = sheet.querySelector(".o_sl_sheet_ficha");
        if (ficha) {
            ficha.href = `/dirigentes/${sectionId}/miembro/${card.dataset.member}`;
        }
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
            const group = cell.dataset.group;
            if (group) {
                const out = row.querySelector(`[data-count="${group}"]`);
                if (out) {
                    const done = row.querySelectorAll(`td[data-group="${group}"][data-state="achieved"]`).length;
                    out.textContent = `${done}/${out.dataset.need}`;
                    out.classList.toggle("o_sl_count_ok", done >= Number(out.dataset.need));
                }
            }
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

// ---------------------------------------------------------------- foto con la cámara (se reduce en el celular) e INE
function setupPhoto(input) {
    input.addEventListener("change", () => {
        const file = input.files && input.files[0];
        if (!file) {
            return;
        }
        const form = input.closest("form");
        const target = form.querySelector(`input[name="${input.dataset.slTarget || "photo_data"}"]`);
        const preview = input.dataset.slPreview ? form.querySelector(input.dataset.slPreview) : form.querySelector(".o_sl_photo_preview");
        const max = Number(input.dataset.slMax || 900);
        const reader = new FileReader();
        reader.onload = () => {
            if (file.type === "application/pdf") {
                target.value = reader.result;
                if (preview) {
                    preview.classList.add("d-none");
                }
                const label = input.closest("label");
                if (label) {
                    label.classList.add("o_sl_done_file");
                }
                return;
            }
            const img = new Image();
            img.onload = () => {
                const ratio = Math.min(1, max / Math.max(img.width, img.height));
                const canvas = document.createElement("canvas");
                canvas.width = Math.round(img.width * ratio);
                canvas.height = Math.round(img.height * ratio);
                canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
                const data = canvas.toDataURL("image/jpeg", 0.85);
                target.value = data;
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

function setupRequireIne(form) {
    form.addEventListener("submit", (ev) => {
        const front = form.querySelector('input[name="ine_front_data"]');
        if (front && !front.value && form.dataset.slRequireIne === "1") {
            ev.preventDefault();
            ev.stopImmediatePropagation();
            toast("Falta la foto de la INE del tutor (frente).");
            front.closest(".o_sl_ine_box").scrollIntoView({ behavior: "smooth", block: "center" });
        }
    }, true);
}

// ---------------------------------------------------------------- copiar liga
function setupCopy(button) {
    button.addEventListener("click", async () => {
        try {
            await navigator.clipboard.writeText(button.dataset.slCopy);
            toast(button.dataset.slCopyMsg || "Copiado");
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
                const hit = !term || row.textContent.toLowerCase().includes(term);
                row.classList.toggle("d-none", !hit);
                const box = row.closest("details");
                if (box && term && hit) {
                    box.open = true;
                }
            });
        })
    );
}

function start() {
    // Fotos: también en el portal de familias y en la ficha de registro.
    document.querySelectorAll(".o_scout_photo_pick input[data-sl-photo]").forEach(setupPhoto);
    // Copiar la CLABE de los datos de transferencia
    document.querySelectorAll(".o_scout_copy[data-copy]").forEach((btn) =>
        btn.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(btn.dataset.copy);
                btn.innerHTML = '<i class="fa fa-check"></i> Copiada';
            } catch {
                window.prompt("Copia la CLABE:", btn.dataset.copy);
            }
        })
    );
    if (!document.querySelector(".o_scout_leader")) {
        return;
    }
    document.querySelectorAll("[data-sl-attendance]").forEach(setupAttendance);
    document.querySelectorAll(".o_sl_teams").forEach(setupTeams);
    document.querySelectorAll("table[data-sl-achievements]").forEach(setupAchievements);
    document.querySelectorAll("input[data-sl-photo]:not(.o_scout_photo_pick input)").forEach(setupPhoto);
    document.querySelectorAll("form[data-sl-require-ine]").forEach(setupRequireIne);
    document.querySelectorAll("[data-sl-copy]").forEach(setupCopy);
    setupAppNavigation();
    // Aviso por WhatsApp: marca a quién ya se le abrió el chat
    document.querySelectorAll(".o_sl_wa_send").forEach((link) =>
        link.addEventListener("click", () => {
            const row = link.closest(".o_sl_wa_row");
            row && row.classList.add("o_sl_wa_done");
            link.innerHTML = '<i class="fa fa-check"></i> Enviado';
        })
    );
    setupHelpers();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
} else {
    start();
}

// ---------------------------------------------------------------- navegación dentro de la app instalada
function isStandaloneApp() {
    return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
}

function goBack(fallback) {
    const sameOrigin = document.referrer && document.referrer.startsWith(window.location.origin);
    if (window.history.length > 1 && sameOrigin) {
        window.history.back();
    } else {
        window.location.href = fallback || "/scouts/app";
    }
}

function isPdfLink(a) {
    if (a.dataset.slPdf) {
        return true;
    }
    const href = a.getAttribute("href") || "";
    return /^\/(?!\/)/.test(href) && /(\.pdf|\/pdf)(\?|#|$)/i.test(href);
}

function setupAppNavigation() {
    // Botón "regresar" del encabezado del portal de dirigentes
    document.querySelectorAll("[data-sl-back]").forEach((btn) =>
        btn.addEventListener("click", (ev) => {
            ev.preventDefault();
            goBack(btn.dataset.slBack);
        })
    );
    // Banners publicitarios: se pueden ocultar 7 días
    document.querySelectorAll(".o_scout_banner[data-banner]").forEach((banner) => {
        const key = "scout_banner_hidden_" + banner.dataset.banner;
        try {
            if (Number(window.localStorage.getItem(key) || 0) > Date.now()) {
                banner.remove();
                return;
            }
        } catch {
            // sin almacenamiento
        }
        const close = banner.querySelector(".o_scout_banner_close");
        close && close.addEventListener("click", () => {
            try {
                window.localStorage.setItem(key, String(Date.now() + 7 * 86400000));
            } catch {
                // sin almacenamiento: solo se oculta ahora
            }
            banner.remove();
        });
    });
    if (!isStandaloneApp()) {
        return;
    }
    document.documentElement.classList.add("o_scout_standalone");
    // Los PDF se abren en un visor con botón para regresar (sin él, la app se quedaba atorada en el PDF)
    document.addEventListener("click", (ev) => {
        const a = ev.target.closest("a[href]");
        if (!a || !isPdfLink(a) || a.hasAttribute("download")) {
            return;
        }
        ev.preventDefault();
        const back = window.location.pathname + window.location.search;
        window.location.href = "/scouts/pdf?u=" + encodeURIComponent(a.getAttribute("href")) +
            "&back=" + encodeURIComponent(back);
    }, true);
    // Páginas fuera del portal de dirigentes (familias, chat, aula): botón flotante para regresar
    const roots = ["/", "/scouts/app", "/dirigentes", "/my", "/my/home", "/my/scouts", "/web/login", "/odoo"];
    const path = window.location.pathname.replace(/\/$/, "") || "/";
    if (!document.querySelector(".o_sl_head") && !roots.includes(path) && !/^\/dirigentes\/\d+$/.test(path)) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "o_scout_fab_back";
        btn.setAttribute("aria-label", "Regresar");
        btn.innerHTML = '<i class="fa fa-chevron-left"></i> Regresar';
        btn.addEventListener("click", () => goBack("/scouts/app"));
        document.body.appendChild(btn);
    }
}

