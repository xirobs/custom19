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
    if (select.dataset.locked) {
        return; // ya es miembro: conserva su sección (los pases los registran los jefes)
    }
    const hidden = form.querySelector('input[type="hidden"][name="section_id"]');
    const suggest = () => {
        if (!birth.value) {
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
        const age = `${Math.floor(months / 12)} años ${months % 12} meses`;
        if (options.length && months >= 0) {
            select.value = options[0][0];
            if (hidden) {
                hidden.value = options[0][0];
            }
            const label = select.options[select.selectedIndex].textContent;
            if (hint) {
                hint.className = "alert alert-success py-2 px-3 mb-0 small o_scout_section_hint";
                hint.innerHTML = `<i class="fa fa-check-circle"></i> Por su edad (${age})` +
                    (g ? " y sexo" : "") + ` le corresponde <b>${label}</b>. ` +
                    (g ? "" : "Elige también el sexo para confirmar la sección. ") +
                    "La sección se asigna automáticamente y no se puede cambiar.";
            }
        } else {
            select.value = "";
            if (hidden) {
                hidden.value = "";
            }
            if (hint) {
                hint.className = "alert alert-warning py-2 px-3 mb-0 small o_scout_section_hint";
                hint.innerHTML = `<i class="fa fa-exclamation-triangle"></i> Con ${age} no hay una sección del grupo para su edad. ` +
                    "Revisa la fecha de nacimiento o contacta a la jefatura.";
            }
        }
    };
    birth.addEventListener("change", suggest);
    birth.addEventListener("input", suggest);
    if (gender) {
        gender.addEventListener("change", suggest);
    }
    suggest();
}

function init() {
    // Firma del jefe de grupo para las credenciales (portal de administración)
    document.querySelectorAll(".o_scout_sig_form .o_scout_signature").forEach(setupSignature);
    const form = document.querySelector(".o_scout_register_form");
    if (!form) {
        return;
    }
    form.querySelectorAll(".o_scout_signature").forEach(setupSignature);
    setupSectionSuggestion(form);
    restoreDraft(form);
    form.addEventListener("submit", (ev) => {
        const photo = form.querySelector('input[name="photo_data"][data-required="1"]');
        if (photo && !photo.value) {
            ev.preventDefault();
            window.alert("Falta la foto del scout (de frente, para su credencial).");
            const pick = form.querySelector(".o_scout_photo_pick");
            if (pick) {
                pick.scrollIntoView({ behavior: "smooth", block: "center" });
            }
            return;
        }
        const ine = form.querySelector('input[name="ine_front"][required]');
        if (ine && !ine.files.length) {
            ev.preventDefault();
            window.alert("Falta la foto de la identificación (INE, FM3 o pasaporte).");
            ine.scrollIntoView({ behavior: "smooth", block: "center" });
            return;
        }
        if (!form.querySelector('input[name="guardian_signature"]').value) {
            ev.preventDefault();
            window.alert("Falta la firma del padre, madre o tutor.");
            return;
        }
        saveDraft(form);
    });
}

// Si el servidor regresa la ficha con un error, se recupera lo que la familia ya había escrito.
const SKIP_FIELDS = ["csrf_token", "guardian_signature", "member_signature"];

function draftKey() {
    return "scout_ficha_" + window.location.pathname;
}

function saveDraft(form) {
    const data = {};
    for (const el of form.elements) {
        if (!el.name || SKIP_FIELDS.includes(el.name) || el.type === "file" || el.type === "hidden") {
            continue;
        }
        if (el.type === "checkbox" || el.type === "radio") {
            if (el.checked) {
                data[el.name] = el.value;
            }
        } else {
            data[el.name] = el.value;
        }
    }
    try {
        window.sessionStorage.setItem(draftKey(), JSON.stringify(data));
    } catch {
        // almacenamiento no disponible: no pasa nada
    }
}

function restoreDraft(form) {
    let data = null;
    try {
        data = JSON.parse(window.sessionStorage.getItem(draftKey()) || "null");
        window.sessionStorage.removeItem(draftKey());
    } catch {
        data = null;
    }
    if (!data || !document.querySelector(".o_scout_register .alert-danger")) {
        return;
    }
    for (const el of form.elements) {
        if (!el.name || !(el.name in data) || SKIP_FIELDS.includes(el.name) || el.type === "file") {
            continue;
        }
        if (el.type === "checkbox" || el.type === "radio") {
            el.checked = data[el.name] === el.value;
        } else if (el.type !== "hidden") {
            el.value = data[el.name];
        }
        el.dispatchEvent(new Event("change", { bubbles: true }));
    }
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
} else {
    init();
}
