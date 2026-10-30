/** @odoo-module **/
// App instalable de Red Seguro Scout: botón directo en Android (Chrome) y pasos guiados en
// iPhone, otros navegadores o dentro de WhatsApp/Instagram/Facebook.
// Solo actúa en páginas que tienen el aviso .o_rssi (portales del seguro).

const KEY = "rss_app_install_dismissed";
const DAYS = 14;
let deferredPrompt = null;
const UA = navigator.userAgent || "";
const standalone = () =>
    window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
const isIOS = () => /iphone|ipad|ipod/i.test(UA) || (/Macintosh/.test(UA) && navigator.maxTouchPoints > 1);
const isAndroid = () => /android/i.test(UA);
const isInApp = () =>
    /FBAN|FBAV|FB_IAB|FBIOS|Instagram|WhatsApp|Line\/|Twitter|TikTok|Snapchat|Telegram|GSA\/|; wv\)/i.test(UA);
const isIOSOther = () => /CriOS|FxiOS|EdgiOS|OPiOS/i.test(UA);

function dismissed() {
    try {
        const ts = Number(window.localStorage.getItem(KEY) || 0);
        return ts && Date.now() - ts < DAYS * 86400000;
    } catch {
        return false;
    }
}
const bars = () => document.querySelectorAll(".o_rssi");

function show(mode) {
    if (standalone() || dismissed()) {
        return;
    }
    bars().forEach((bar) => {
        bar.classList.remove("d-none");
        bar.classList.toggle("o_rssi_is_manual", mode !== "android");
        bar.classList.toggle("o_rssi_on_ios", isIOS());
        bar.classList.toggle("o_rssi_on_android", !isIOS());
        ["inapp", "ios", "ios-other", "android-manual"].forEach((m) =>
            bar.classList.toggle(`o_rssi_mode_${m}`, m === mode)
        );
        const url = bar.querySelector(".o_rssi_url");
        if (url) {
            url.value = window.location.origin + "/seguro-app";
        }
    });
}

function hide(remember) {
    bars().forEach((bar) => bar.classList.add("d-none"));
    if (remember) {
        try {
            window.localStorage.setItem(KEY, String(Date.now()));
        } catch {
            // sin almacenamiento: solo se oculta en esta página
        }
    }
}

function toggleSteps(bar) {
    const steps = bar.querySelector(".o_rssi_steps");
    const how = bar.querySelector(".o_rssi_how");
    const open = steps.classList.contains("d-none");
    steps.classList.toggle("d-none", !open);
    how.setAttribute("aria-expanded", String(open));
    how.textContent = open ? "Ocultar pasos" : "Ver cómo instalarla";
}

async function copyLink(bar, button) {
    const input = bar.querySelector(".o_rssi_url");
    let ok = false;
    try {
        await navigator.clipboard.writeText(input.value);
        ok = true;
    } catch {
        input.focus();
        input.select();
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
    if (!document.querySelector(".o_rssi")) {
        return;
    }
    ev.preventDefault();
    deferredPrompt = ev;
    show("android");
});
window.addEventListener("appinstalled", () => hide(true));

function setup() {
    if (!document.querySelector(".o_rssi")) {
        return;
    }
    document.addEventListener("click", async (ev) => {
        const bar = ev.target.closest(".o_rssi");
        if (!bar) {
            return;
        }
        if (ev.target.closest(".o_rssi_close")) {
            hide(true);
        } else if (ev.target.closest(".o_rssi_copy_btn")) {
            await copyLink(bar, ev.target.closest(".o_rssi_copy_btn"));
        } else if (ev.target.closest(".o_rssi_btn") && deferredPrompt) {
            deferredPrompt.prompt();
            const choice = await deferredPrompt.userChoice;
            deferredPrompt = null;
            hide(choice && choice.outcome === "accepted");
        } else if (bar.classList.contains("o_rssi_is_manual")
                && (ev.target.closest(".o_rssi_how") || ev.target.closest(".o_rssi_row"))) {
            toggleSteps(bar);
        }
    });
    if (standalone()) {
        return;
    }
    if (isInApp()) {
        show("inapp");
    } else if (isIOS()) {
        show(isIOSOther() ? "ios-other" : "ios");
    } else if (isAndroid()) {
        setTimeout(() => {
            if (!deferredPrompt) {
                show("android-manual");
            }
        }, 3000);
    }
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setup);
} else {
    setup();
}
