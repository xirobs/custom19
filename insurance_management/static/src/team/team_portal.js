/** @odoo-module **/
// Portal del equipo de seguros: arrastrar y soltar en los tableros, "mover a" en el celular,
// búsqueda de pólizas, ofertas por ramo, requisitos del trámite y subida automática de carátulas.

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
    let el = document.querySelector(".o_it_toast");
    if (!el) {
        el = document.createElement("div");
        el.className = "o_it_toast";
        el.setAttribute("role", "status");
        document.body.appendChild(el);
    }
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.remove("show"), 2800);
}

function refreshCounts(board) {
    board.querySelectorAll(".o_it_col").forEach((col) => {
        const counter = col.querySelector(".o_it_col_head b");
        const cards = col.querySelectorAll(".o_it_card").length;
        if (counter) {
            counter.textContent = cards;
        }
        const empty = col.querySelector(".o_it_col_empty");
        if (empty) {
            empty.hidden = cards > 0;
        }
    });
}

async function moveCard(board, card, column) {
    const target = board.querySelector(`.o_it_col[data-column="${column}"] .o_it_col_body`);
    const origin = card.parentElement;
    if (!target || target === origin) {
        return;
    }
    const next = card.nextSibling;
    target.prepend(card);
    refreshCounts(board);
    card.classList.add("o_it_saving");
    try {
        const result = await rpc("/equipo/api/mover", {
            block: board.dataset.block,
            record_id: Number(card.dataset.id),
            column,
        });
        if (!result.ok) {
            origin.insertBefore(card, next);
            refreshCounts(board);
            toast(result.error);
            return;
        }
        const select = card.querySelector(".o_it_move");
        if (select) {
            select.value = "";
            [...select.options].forEach((opt) => (opt.hidden = opt.value === column));
        }
        toast("Listo, se movió.");
    } catch {
        origin.insertBefore(card, next);
        refreshCounts(board);
        toast("Sin conexión: no se guardó. Intenta de nuevo.");
    } finally {
        card.classList.remove("o_it_saving");
    }
}

function setupBoard(board) {
    let dragged = null;
    board.addEventListener("dragstart", (ev) => {
        const card = ev.target.closest(".o_it_card[draggable='true']");
        if (!card) {
            return;
        }
        dragged = card;
        card.classList.add("o_it_dragging");
        ev.dataTransfer.effectAllowed = "move";
        ev.dataTransfer.setData("text/plain", card.dataset.id);
    });
    board.addEventListener("dragend", () => {
        if (dragged) {
            dragged.classList.remove("o_it_dragging");
        }
        dragged = null;
        board.querySelectorAll(".o_it_drop_target").forEach((c) => c.classList.remove("o_it_drop_target"));
    });
    board.addEventListener("dragover", (ev) => {
        const col = ev.target.closest(".o_it_col");
        if (!dragged || !col) {
            return;
        }
        ev.preventDefault();
        board.querySelectorAll(".o_it_drop_target").forEach((c) => c !== col && c.classList.remove("o_it_drop_target"));
        col.classList.add("o_it_drop_target");
    });
    board.addEventListener("drop", (ev) => {
        const col = ev.target.closest(".o_it_col");
        if (!dragged || !col) {
            return;
        }
        ev.preventDefault();
        col.classList.remove("o_it_drop_target");
        moveCard(board, dragged, col.dataset.column);
    });
    board.addEventListener("change", (ev) => {
        const select = ev.target.closest(".o_it_move");
        if (!select || !select.value) {
            return;
        }
        moveCard(board, select.closest(".o_it_card"), select.value);
    });
    // Evita que un arrastre abra la tarjeta
    board.addEventListener("click", (ev) => {
        if (ev.target.closest(".o_it_dragging")) {
            ev.preventDefault();
        }
    });
}

function setupPolicySearch(input) {
    const form = input.closest("form");
    const hidden = form.querySelector(".o_it_policy_id");
    const list = document.getElementById(input.getAttribute("list"));
    const sync = () => {
        const option = [...list.options].find((o) => o.value === input.value);
        hidden.value = option ? option.dataset.id : "";
        input.setCustomValidity(input.required && input.value && !option ? "Elige una póliza de la lista." : "");
    };
    input.addEventListener("input", sync);
    input.addEventListener("change", sync);
}

function setupOffers(form) {
    const ramo = form.querySelector(".o_it_ramo");
    const offer = form.querySelector(".o_it_offer");
    if (!ramo || !offer) {
        return;
    }
    const filter = () => {
        [...offer.options].forEach((opt) => {
            if (opt.value) {
                opt.hidden = Boolean(ramo.value) && opt.dataset.ramo !== ramo.value;
            }
        });
        if (offer.selectedOptions[0] && offer.selectedOptions[0].hidden) {
            offer.value = "";
        }
    };
    ramo.addEventListener("change", filter);
    filter();
}

function setupRequirements(form) {
    const box = form.querySelector(".o_it_req");
    if (!box) {
        return;
    }
    const show = () => {
        const checked = form.querySelector("input[name='type_id']:checked");
        const text = checked ? checked.dataset.req : "";
        box.querySelector("span").textContent = text || "";
        box.classList.toggle("d-none", !text);
    };
    form.addEventListener("change", (ev) => ev.target.name === "type_id" && show());
    show();
}

function init() {
    const root = document.querySelector(".o_ins_team");
    if (!root) {
        return;
    }
    root.querySelectorAll(".o_it_board[data-block]").forEach(setupBoard);
    root.querySelectorAll(".o_it_policy_search").forEach(setupPolicySearch);
    root.querySelectorAll("form").forEach((form) => {
        setupOffers(form);
        setupRequirements(form);
    });
    // Carátula: se sube en cuanto eligen el archivo
    root.querySelectorAll(".o_it_autosubmit").forEach((input) =>
        input.addEventListener("change", () => {
            if (input.files.length) {
                toast("Leyendo la carátula…");
                input.form.submit();
            }
        })
    );
    // Muestra el nombre de los archivos elegidos en las notas
    root.querySelectorAll(".o_it_file").forEach((input) =>
        input.addEventListener("change", () => {
            const names = input.closest("form").querySelector(".o_it_file_names");
            if (names) {
                names.textContent = [...input.files].map((f) => f.name).join(", ");
            }
        })
    );
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
} else {
    init();
}
