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
    canvas.style.touchAction = "none";
    canvas.addEventListener("pointerdown", (ev) => {
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
    canvas.addEventListener("pointerleave", stop);
    wrapper.querySelector(".o_scout_signature_clear").addEventListener("click", () => {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        dirty = false;
        input.value = "";
    });
    resize();
}

function init() {
    const form = document.querySelector(".o_scout_register_form");
    if (!form) {
        return;
    }
    form.querySelectorAll(".o_scout_signature").forEach(setupSignature);
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
