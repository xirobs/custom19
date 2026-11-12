/** @odoo-module **/
// Foto del asegurado desde el celular o la computadora: «Tomar foto» abre la cámara y
// «Elegir de galería o archivo» abre la galería/archivos. Antes de enviarla se reduce a 1200 px
// y se convierte a JPG (sirve también para fotos HEIC del iPhone y no se sube un archivo pesado).

const MAX_SIDE = 1200;

function toJpeg(file) {
    return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const img = new Image();
        img.onload = () => {
            const scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
            const canvas = document.createElement("canvas");
            canvas.width = Math.round(img.naturalWidth * scale);
            canvas.height = Math.round(img.naturalHeight * scale);
            const ctx = canvas.getContext("2d");
            ctx.fillStyle = "#fff";
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
            URL.revokeObjectURL(url);
            resolve(canvas.toDataURL("image/jpeg", 0.85));
        };
        img.onerror = () => {
            URL.revokeObjectURL(url);
            reject(new Error("photo"));
        };
        img.src = url;
    });
}

document.addEventListener("change", async (ev) => {
    const input = ev.target;
    if (!input.matches || !input.matches(".o_rss_photo_input") || !input.files || !input.files[0]) {
        return;
    }
    const box = input.closest(".o_rss_photo_picker");
    if (!box) {
        return;
    }
    const hidden = box.querySelector(".o_rss_photo_data");
    const preview = box.querySelector(".o_rss_photo_preview");
    const status = box.querySelector(".o_rss_photo_status");
    // Solo una foto a la vez: limpia la otra opción
    box.querySelectorAll(".o_rss_photo_input").forEach((other) => {
        if (other !== input) {
            other.value = "";
        }
    });
    try {
        const dataUrl = await toJpeg(input.files[0]);
        hidden.value = dataUrl;
        input.value = ""; // ya va reducida en photo_data; no se sube el archivo original
        if (preview) {
            preview.src = dataUrl;
            preview.style.display = "";
        }
        if (status) {
            status.textContent = "Foto lista. Presiona Guardar.";
        }
        const autosubmit = box.dataset.autosubmit === "1";
        if (autosubmit) {
            box.closest("form")?.requestSubmit();
        }
    } catch {
        // El navegador no pudo abrirla: se envía el archivo tal cual y el servidor la revisa
        hidden.value = "";
        if (status) {
            status.textContent = "Foto elegida. Presiona Guardar.";
        }
    }
});
