/** @odoo-module **/
// Firma con el dedo o el mouse para la ficha de registro del portal.

function setupSignature(wrapper) {
    const canvas = wrapper.querySelector("canvas");
    const form = wrapper.closest("form");
    const input = form.querySelector(`input[name="${wrapper.dataset.input}"]`);
    const ctx = canvas.getContext("2d");
    let drawing = false;
    let dirty = false;

    const resize = () => {
        const ratio = window.devicePixelRatio || 1;
        const width = canvas.clientWidth;
        canvas.width = width * ratio;
        canvas.height = 150 * ratio;
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.lineWidth = 2;
        ctx.lineCap = "round";
        ctx.strokeStyle = "#0f3049";
        dirty = false;
        input.value = "";
    };
    const point = (ev) => {
        const rect = canvas.getBoundingClientRect();
        return [ev.clientX - rect.left, ev.clientY - rect.top];
    };
    const ensureSize = () => {
        // Si el lienzo se preparó oculto o cambió de ancho (giro del celular, estilos tardíos), se reajusta.
        const ratio = window.devicePixelRatio || 1;
        if (!dirty && canvas.clientWidth && Math.round(canvas.clientWidth * ratio) !== canvas.width) {
            resize();
        }
    };
    canvas.style.touchAction = "none";
    canvas.addEventListener("pointerdown", (ev) => {
        ev.preventDefault();
        ensureSize();
        drawing = true;
        canvas.setPointerCapture(ev.pointerId);
        ctx.beginPath();
        ctx.moveTo(...point(ev));
    });
    canvas.addEventListener("pointermove", (ev) => {
        if (!drawing) {
            return;
        }
        ctx.lineTo(...point(ev));
        ctx.stroke();
        dirty = true;
    });
    const stop = () => {
        if (drawing && dirty) {
            input.value = canvas.toDataURL("image/png");
        }
        drawing = false;
    };
    canvas.addEventListener("pointerup", stop);
    canvas.addEventListener("pointercancel", stop);
    canvas.addEventListener("pointerleave", stop);
    // Respaldo para navegadores sin eventos de puntero que desplazan la página al firmar.
    canvas.addEventListener("touchmove", (ev) => ev.preventDefault(), { passive: false });
    wrapper.querySelector(".o_scout_signature_clear").addEventListener("click", () => {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        dirty = false;
        input.value = "";
    });
    resize();
}

// Propone la sección según la edad (años y meses) y el sexo, con las edades oficiales de pase.
function setupSectionSuggestion(form) {
    const select = form.querySelector("select.o_scout_section_auto");
    const birth = form.querySelector('input[name="birthdate"]');
    const gender = form.querySelector('select[name="gender"]');
    const hint = form.querySelector(".o_scout_section_hint");
    if (!select || !birth) {
        return;
    }
    let rules = {};
    try {
        rules = JSON.parse(select.dataset.rules || "{}");
    } catch {
        rules = {};
    }
    let touched = false;
    select.addEventListener("change", () => (touched = true));
    const suggest = () => {
        if (!birth.value || touched) {
            return;
        }
        const [y, m, d] = birth.value.split("-").map(Number);
        const today = new Date();
        let months = (today.getFullYear() - y) * 12 + (today.getMonth() + 1 - m);
        if (today.getDate() < d) {
            months -= 1;
        }
        const g = gender ? gender.value : "";
        const options = Object.entries(rules)
            .filter(([, r]) => r.upper && months < r.upper && (!g || !r.gender || r.gender === g))
            .sort((a, b) => a[1].upper - b[1].upper);
        if (options.length) {
            select.value = options[0][0];
            if (hint) {
                const label = select.options[select.selectedIndex].textContent;
                hint.textContent = `Por su edad (${Math.floor(months / 12)} años ${months % 12} meses) le corresponde: ${label}.`;
            }
        }
    };
    birth.addEventListener("change", suggest);
    if (gender) {
        gender.addEventListener("change", suggest);
    }
}

function init() {
    const form = document.querySelector(".o_scout_register_form");
    if (!form) {
        return;
    }
    form.querySelectorAll(".o_scout_signature").forEach(setupSignature);
    setupSectionSuggestion(form);
    form.addEventListener("submit", (ev) => {
        if (!form.querySelector('input[name="guardian_signature"]').value) {
            ev.preventDefault();
            window.alert("Falta la firma del padre, madre o tutor.");
        }
    });
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
} else {
    init();
}
