/** @odoo-module **/
// App instalable: registra el service worker y muestra "Instalar la app".
// Android con Chrome: botón de un toque. iPhone, otros navegadores y apps como WhatsApp:
// "Ver cómo" despliega los pasos que aplican a ese celular y ese navegador.

const DISMISS_KEY = "scout_app_install_dismissed";
const DISMISS_DAYS = 14;
const ANDROID_FALLBACK_MS = 3000;
let deferredPrompt = null;

const UA = navigator.userAgent || "";
const isStandalone = () =>
    window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
// iPadOS se presenta como Mac: se reconoce por la pantalla táctil.
const isIOS = () =>
    /iphone|ipad|ipod/i.test(UA) || (/Macintosh/.test(UA) && navigator.maxTouchPoints > 1);
const isAndroid = () => /android/i.test(UA);
// Navegadores dentro de otras apps (no permiten instalar)
const isInApp = () =>
    /FBAN|FBAV|FB_IAB|FBIOS|Instagram|WhatsApp|Line\/|Twitter|TikTok|musical_ly|Snapchat|Telegram|GSA\/|; wv\)/i.test(UA);
// Chrome, Firefox, Edge u Opera en iPhone
const isIOSOtherBrowser = () => /CriOS|FxiOS|EdgiOS|OPiOS/i.test(UA);

function dismissed() {
    try {
        const ts = Number(window.localStorage.getItem(DISMISS_KEY) || 0);
        return ts && Date.now() - ts < DISMISS_DAYS * 86400000;
    } catch {
        return false;
    }
}

function bars() {
    return document.querySelectorAll(".o_scout_install");
}

// mode: "android" (botón directo) | "ios" | "ios-other" | "inapp" | "android-manual"
function show(mode) {
    if (isStandalone() || dismissed()) {
        return;
    }
    bars().forEach((bar) => {
        bar.classList.remove("d-none");
        bar.classList.toggle("o_scout_install_is_manual", mode !== "android");
        bar.classList.toggle("o_scout_install_on_ios", isIOS());
        bar.classList.toggle("o_scout_install_on_android", !isIOS());
        ["inapp", "ios", "ios-other", "android-manual"].forEach((m) =>
            bar.classList.toggle(`o_scout_install_mode_${m}`, m === mode)
        );
        const url = bar.querySelector(".o_scout_install_url");
        if (url) {
            url.value = window.location.origin + "/scouts/app";
        }
    });
}

function hide(remember) {
    bars().forEach((bar) => bar.classList.add("d-none"));
    if (remember) {
        try {
            window.localStorage.setItem(DISMISS_KEY, String(Date.now()));
        } catch {
            // sin almacenamiento (p. ej. navegación privada): solo se oculta en esta página
        }
    }
}

function toggleSteps(bar, open) {
    const steps = bar.querySelector(".o_scout_install_steps");
    const how = bar.querySelector(".o_scout_install_how");
    if (!steps) {
        return;
    }
    const willOpen = open === undefined ? steps.classList.contains("d-none") : open;
    steps.classList.toggle("d-none", !willOpen);
    if (how) {
        how.setAttribute("aria-expanded", String(willOpen));
        how.textContent = willOpen ? "Ocultar pasos" : "Ver cómo instalarla";
    }
}

async function copyLink(bar, button) {
    const input = bar.querySelector(".o_scout_install_url");
    if (!input) {
        return;
    }
    let ok = false;
    try {
        await navigator.clipboard.writeText(input.value);
        ok = true;
    } catch {
        input.focus();
        input.select();
        input.setSelectionRange(0, input.value.length);
        try {
            ok = document.execCommand("copy");
        } catch {
            ok = false;
        }
    }
    button.textContent = ok ? "¡Copiada!" : "Mantén presionado para copiar";
    setTimeout(() => (button.textContent = "Copiar liga"), 2500);
}

window.addEventListener("beforeinstallprompt", (ev) => {
    ev.preventDefault();
    deferredPrompt = ev;
    show("android");
});
window.addEventListener("appinstalled", () => hide(true));

function detectMode() {
    if (isInApp()) {
        return "inapp";
    }
    if (isIOS()) {
        return isIOSOtherBrowser() ? "ios-other" : "ios";
    }
    return null;
}

function setup() {
    if ("serviceWorker" in navigator && document.querySelector('link[rel="manifest"][href^="/scouts/"]')) {
        navigator.serviceWorker.register("/scout-sw.js", { scope: "/" }).catch(() => {});
    }
    document.addEventListener("click", async (ev) => {
        const bar = ev.target.closest(".o_scout_install");
        if (!bar) {
            return;
        }
        if (ev.target.closest(".o_scout_install_close")) {
            hide(true);
            return;
        }
        if (ev.target.closest(".o_scout_install_copy_btn")) {
            await copyLink(bar, ev.target.closest(".o_scout_install_copy_btn"));
            return;
        }
        if (ev.target.closest(".o_scout_install_url")) {
            ev.target.select();
            return;
        }
        if (ev.target.closest(".o_scout_install_btn") && deferredPrompt) {
            deferredPrompt.prompt();
            const choice = await deferredPrompt.userChoice;
            deferredPrompt = null;
            hide(choice && choice.outcome === "accepted");
            return;
        }
        // En modo manual, tocar el recuadro (o "Ver cómo") abre o cierra los pasos
        if (bar.classList.contains("o_scout_install_is_manual")
                && (ev.target.closest(".o_scout_install_how") || ev.target.closest(".o_scout_install_row"))) {
            toggleSteps(bar);
        }
    });

    if (isStandalone()) {
        return;
    }
    const mode = detectMode();
    if (mode) {
        show(mode);
    } else if (isAndroid()) {
        // Si Chrome no ofrece la instalación directa en unos segundos, se muestran los pasos manuales
        setTimeout(() => {
            if (!deferredPrompt) {
                show("android-manual");
            }
        }, ANDROID_FALLBACK_MS);
    }
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setup);
} else {
    setup();
}
