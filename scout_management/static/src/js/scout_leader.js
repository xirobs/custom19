/** @odoo-module **/
// Portal de dirigentes: pase de lista con un toque, caja rápida y pequeños atajos para el celular.

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

const money = (value) =>
    "$ " + Number(value || 0).toLocaleString("es-MX", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

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

// ---------------------------------------------------------------- caja rápida
function setupStore(root) {
    const cart = new Map(); // id -> {name, price, qty, stock}
    const linesEl = root.querySelector(".o_sl_cart_lines");
    const totalEl = root.querySelector(".o_sl_total");
    const errorEl = root.querySelector(".o_sl_cart_error");
    const memberEl = root.querySelector(".o_sl_member");
    const buyerEl = root.querySelector(".o_sl_buyer");
    const typeInputs = root.querySelectorAll("input[name=sale_type]");
    const currentType = () => {
        const checked = root.querySelector("input[name=sale_type]:checked") || root.querySelector("input[name=sale_type]");
        return checked ? checked.value : "store";
    };
    const filterProducts = () => {
        const type = currentType();
        root.querySelectorAll(".o_sl_product").forEach((btn) => {
            btn.classList.toggle("d-none", typeInputs.length > 1 && btn.dataset.type !== type);
        });
    };
    const render = () => {
        let total = 0;
        linesEl.innerHTML = "";
        for (const [id, item] of cart) {
            total += item.qty * item.price;
            const row = document.createElement("div");
            row.className = "o_sl_cart_line";
            row.innerHTML = `<button type="button" class="o_sl_minus" data-id="${id}" aria-label="Quitar uno">−</button>
                <span class="flex-grow-1"><b>${item.qty}</b> × ${item.name}</span><span>${money(item.qty * item.price)}</span>`;
            linesEl.appendChild(row);
        }
        totalEl.textContent = money(total);
        root.querySelectorAll(".o_sl_product").forEach((btn) => {
            const item = cart.get(btn.dataset.id);
            const badge = btn.querySelector(".o_sl_product_qty");
            badge.textContent = item ? item.qty : 0;
            badge.classList.toggle("d-none", !item);
        });
        root.querySelector(".o_sl_cart").classList.toggle("o_sl_cart_empty", cart.size === 0);
    };
    const showError = (message) => {
        errorEl.textContent = message;
        errorEl.classList.toggle("d-none", !message);
    };
    root.addEventListener("click", async (ev) => {
        const product = ev.target.closest(".o_sl_product");
        if (product) {
            const id = product.dataset.id;
            const item = cart.get(id) || {
                name: product.dataset.name,
                price: Number(product.dataset.price),
                qty: 0,
                stock: !product.dataset.stock || product.dataset.stock === "none" ? null : Number(product.dataset.stock),
            };
            if (item.stock !== null && item.qty + 1 > item.stock) {
                toast(`Ya no hay más ${item.name}`);
                return;
            }
            item.qty += 1;
            cart.set(id, item);
            showError("");
            render();
            return;
        }
        const minus = ev.target.closest(".o_sl_minus");
        if (minus) {
            const item = cart.get(minus.dataset.id);
            item.qty -= 1;
            if (item.qty <= 0) {
                cart.delete(minus.dataset.id);
            }
            render();
            return;
        }
        if (ev.target.closest(".o_sl_clear")) {
            cart.clear();
            render();
            return;
        }
        const pay = ev.target.closest(".o_sl_pay");
        if (pay) {
            if (!cart.size) {
                showError("Toca los productos que se venden.");
                return;
            }
            const memberId = memberEl.value ? Number(memberEl.value) : null;
            const buyer = buyerEl.value.trim();
            if (!memberId && !buyer) {
                showError("Elige al miembro o escribe quién compra.");
                return;
            }
            root.querySelectorAll(".o_sl_pay").forEach((b) => (b.disabled = true));
            try {
                const result = await rpc("/dirigentes/api/venta", {
                    section_id: Number(root.dataset.section),
                    sale_type: currentType(),
                    member_id: memberId,
                    buyer_name: buyer,
                    payment: pay.dataset.pay,
                    lines: [...cart].map(([id, item]) => ({ product_id: Number(id), qty: item.qty })),
                });
                if (result.ok) {
                    window.location = result.url;
                    return;
                }
                showError(result.error);
            } catch (error) {
                showError("Sin conexión: la venta no se registró. Intenta de nuevo.");
            }
            root.querySelectorAll(".o_sl_pay").forEach((b) => (b.disabled = false));
        }
    });
    memberEl.addEventListener("change", () => buyerEl.classList.toggle("d-none", Boolean(memberEl.value)));
    typeInputs.forEach((input) =>
        input.addEventListener("change", () => {
            cart.clear();
            filterProducts();
            render();
        })
    );
    filterProducts();
    render();
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
    document.querySelectorAll(".o_sl_store").forEach(setupStore);
    setupHelpers();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
} else {
    start();
}
