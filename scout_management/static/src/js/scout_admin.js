/** @odoo-module **/
/* Administración del grupo en el portal: tiendita (vender desde el celular), editor del acta y confirmaciones. */

function money(v) {
    return "$ " + (Number(v) || 0).toLocaleString("es-MX", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function setupPos(form) {
    const items = [...form.querySelectorAll(".o_ad_product")];
    const totalEl = form.querySelector("[data-ad-pos-total]");
    const submit = form.querySelector("[data-ad-pos-submit]");
    const typeSel = form.querySelector("[data-ad-pos-type]");
    const search = form.querySelector("[data-ad-pos-search]");
    const recalc = () => {
        let total = 0;
        let count = 0;
        for (const item of items) {
            const input = item.querySelector("input");
            let qty = Math.max(0, Number(input.value) || 0);
            const stock = item.dataset.stock;
            if (stock !== "" && stock !== undefined && qty > Number(stock)) {
                qty = Math.max(0, Number(stock));
                input.value = qty;
            }
            item.classList.toggle("o_ad_selected", qty > 0);
            total += qty * Number(item.dataset.price || 0);
            count += qty;
        }
        totalEl.textContent = money(total);
        submit.disabled = count <= 0;
        submit.innerHTML = count > 0 ? `<i class="fa fa-check"></i> Cobrar ${money(total)}` : '<i class="fa fa-check"></i> Cobrar';
    };
    const filter = () => {
        const q = (search.value || "").toLowerCase().trim();
        const type = typeSel.value;
        for (const item of items) {
            const qty = Number(item.querySelector("input").value) || 0;
            item.hidden = qty <= 0 && ((type && item.dataset.type !== type) || (q && !item.dataset.name.includes(q)));
        }
    };
    form.addEventListener("click", (ev) => {
        const btn = ev.target.closest("[data-ad-step]");
        if (!btn) {
            return;
        }
        const input = btn.parentElement.querySelector("input");
        input.value = Math.max(0, (Number(input.value) || 0) + Number(btn.dataset.adStep));
        recalc();
    });
    form.addEventListener("input", (ev) => {
        if (ev.target.matches(".o_ad_qty input")) {
            recalc();
        }
    });
    typeSel.addEventListener("change", filter);
    search.addEventListener("input", filter);
    const paid = form.querySelector("[data-ad-paid]");
    for (const radio of form.querySelectorAll("input[name=payment_mode]")) {
        radio.addEventListener("change", () => {
            paid.hidden = form.querySelector("input[name=payment_mode]:checked").value !== "paid";
        });
    }
    form.addEventListener("submit", () => {
        submit.disabled = true;
        submit.innerHTML = '<i class="fa fa-spinner fa-spin"></i> Registrando…';
    });
    filter();
    recalc();
}

function setupHtmlForm(form) {
    for (const btn of form.querySelectorAll("[data-ad-cmd]")) {
        btn.addEventListener("mousedown", (ev) => ev.preventDefault());
        btn.addEventListener("click", () => document.execCommand(btn.dataset.adCmd, false, btn.dataset.adArg || null));
    }
    form.addEventListener("submit", () => {
        for (const div of form.querySelectorAll("[data-ad-html]")) {
            const area = form.querySelector(`textarea[name="${div.dataset.adHtml}"]`);
            if (area && div.getAttribute("contenteditable") === "true") {
                area.value = div.innerHTML;
            }
        }
    });
}

function setupCount(form) {
    const total = form.querySelector("[data-ad-count-total]");
    const diff = form.querySelector("[data-ad-count-diff]");
    const detail = form.querySelector("[data-ad-count-detail]");
    const system = Number(diff.dataset.system || 0);
    const denoms = [...form.querySelectorAll("[data-denom]")];
    const showDiff = () => {
        if (total.value === "") {
            diff.textContent = "";
            return;
        }
        const d = Math.round((Number(total.value) - system) * 100) / 100;
        diff.innerHTML = d === 0 ? '<b class="text-success">✔ Cuadra</b>'
            : `<b class="${d > 0 ? "text-success" : "text-danger"}">${d > 0 ? "Sobran" : "Faltan"} ${money(Math.abs(d))}</b>`;
    };
    const fromDenoms = () => {
        let sum = 0;
        const parts = [];
        for (const input of denoms) {
            const n = Number(input.value) || 0;
            if (n > 0) {
                sum += n * Number(input.dataset.denom);
                parts.push(`${n} × $${input.dataset.denom}`);
            }
        }
        total.value = sum ? sum.toFixed(2) : "";
        if (parts.length && (!detail.value || detail.dataset.auto === "1")) {
            detail.value = "Conteo: " + parts.join(", ");
            detail.dataset.auto = "1";
        }
        showDiff();
    };
    denoms.forEach((input) => input.addEventListener("input", fromDenoms));
    total.addEventListener("input", showDiff);
    detail.addEventListener("input", () => (detail.dataset.auto = "0"));
}

function boot() {
    document.addEventListener("click", (ev) => {
        const btn = ev.target.closest("[data-ad-confirm]");
        if (btn && !window.confirm(btn.dataset.adConfirm)) {
            ev.preventDefault();
            ev.stopPropagation();
        }
    }, true);
    document.addEventListener("click", async (ev) => {
        const btn = ev.target.closest("[data-ad-copy]");
        if (!btn) {
            return;
        }
        try {
            await navigator.clipboard.writeText(btn.dataset.adCopy);
            btn.innerHTML = '<i class="fa fa-check"></i> Liga copiada';
        } catch {
            window.prompt("Copia la liga:", btn.dataset.adCopy);
        }
    });
    for (const form of document.querySelectorAll("[data-ad-count]")) {
        setupCount(form);
    }
    for (const form of document.querySelectorAll("[data-ad-pos]")) {
        setupPos(form);
    }
    for (const form of document.querySelectorAll("[data-ad-html-form]")) {
        setupHtmlForm(form);
    }
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
} else {
    boot();
}
