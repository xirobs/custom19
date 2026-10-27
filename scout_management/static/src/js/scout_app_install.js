/** @odoo-module **/
// App instalable: registra el service worker y muestra "Instalar la app" (Android: un toque; iPhone: instrucciones).

const DISMISS_KEY = "scout_app_install_dismissed";
const DISMISS_DAYS = 14;
let deferredPrompt = null;

const isStandalone = () =>
    window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;

function dismissed() {
    try {
        const ts = Number(window.localStorage.getItem(DISMISS_KEY) || 0);
        return ts && Date.now() - ts < DISMISS_DAYS * 86400000;
    } catch {
        return false;
    }
}

function show(mode) {
    if (isStandalone() || dismissed()) {
        return;
    }
    document.querySelectorAll(".o_scout_install").forEach((bar) => {
        bar.classList.remove("d-none");
        bar.classList.toggle("o_scout_install_is_ios", mode === "ios");
    });
}

function hide(remember) {
    document.querySelectorAll(".o_scout_install").forEach((bar) => bar.classList.add("d-none"));
    if (remember) {
        try {
            window.localStorage.setItem(DISMISS_KEY, String(Date.now()));
        } catch {
            // sin almacenamiento: solo se oculta en esta página
        }
    }
}

window.addEventListener("beforeinstallprompt", (ev) => {
    ev.preventDefault();
    deferredPrompt = ev;
    show("android");
});
window.addEventListener("appinstalled", () => hide(true));

function setup() {
    if ("serviceWorker" in navigator && document.querySelector('link[rel="manifest"][href^="/scouts/"]')) {
        navigator.serviceWorker.register("/scout-sw.js", { scope: "/" }).catch(() => {});
    }
    document.addEventListener("click", async (ev) => {
        if (ev.target.closest(".o_scout_install_close")) {
            hide(true);
            return;
        }
        if (ev.target.closest(".o_scout_install_btn") && deferredPrompt) {
            deferredPrompt.prompt();
            const choice = await deferredPrompt.userChoice;
            deferredPrompt = null;
            hide(choice && choice.outcome === "accepted");
        }
    });
    if (isIOS() && !isStandalone()) {
        show("ios");
    }
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setup);
} else {
    setup();
}
