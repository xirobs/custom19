/* App del equipo CreArt 3DP: PWA, pestañas de la ficha, confirmaciones y ayudas de captura. */
(function () {
    "use strict";

    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.register("/creart/app/sw.js", { scope: "/creart/app" }).catch(function () {});
    }

    // Confirmación antes de acciones sensibles.
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            if (!window.confirm(form.dataset.confirm)) {
                event.preventDefault();
            }
        });
    });

    // Evita doble envío y da respuesta visual al tocar.
    document.querySelectorAll("form").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            if (event.defaultPrevented || form.method.toLowerCase() !== "post") {
                return;
            }
            var button = form.querySelector("button[type=submit]");
            if (button) {
                window.setTimeout(function () { button.disabled = true; button.style.opacity = ".6"; }, 0);
            }
        });
    });

    // Pestañas de la ficha de trabajo sin recargar.
    var tabs = document.querySelectorAll(".tabs-inline [data-tab]");
    var panels = document.querySelectorAll("[data-panel]");
    function showTab(name) {
        tabs.forEach(function (tab) { tab.classList.toggle("on", tab.dataset.tab === name); });
        panels.forEach(function (panel) { panel.hidden = panel.dataset.panel !== name; });
        document.querySelectorAll("input[name=seccion]").forEach(function (input) {
            if (input.closest(".stepper")) { input.value = name; }
        });
    }
    tabs.forEach(function (tab) {
        tab.addEventListener("click", function (event) {
            event.preventDefault();
            showTab(tab.dataset.tab);
            var url = new URL(window.location.href);
            url.searchParams.set("seccion", tab.dataset.tab);
            url.searchParams.delete("ok");
            window.history.replaceState(null, "", url);
        });
    });

    // Quita el aviso de "guardado" de la URL para no repetirlo al recargar.
    if (window.location.search.indexOf("ok=") !== -1) {
        var clean = new URL(window.location.href);
        clean.searchParams.delete("ok");
        window.history.replaceState(null, "", clean);
    }

    // Accesorios: muestra % de uso o cantidad según el modo.
    document.querySelectorAll("select[data-usage-mode]").forEach(function (select) {
        var form = select.closest("form");
        function sync() {
            form.querySelectorAll("[data-show]").forEach(function (field) {
                field.hidden = field.dataset.show !== select.value;
            });
        }
        select.addEventListener("change", sync);
        sync();
    });

    // Campos numéricos vacíos: muestra 0 como guía.
    document.querySelectorAll("input[type=number]:not([placeholder])").forEach(function (input) {
        input.placeholder = "0";
    });

    // Gantt: arranca mostrando el día de hoy.
    var gantt = document.querySelector(".gantt");
    var todayCell = gantt && gantt.querySelector(".g-day.today");
    if (gantt && todayCell && gantt.scrollWidth > gantt.clientWidth) {
        var label = gantt.querySelector(".g-head .g-label");
        var cell = todayCell.getBoundingClientRect();
        var box = gantt.getBoundingClientRect();
        var offset = cell.left - box.left + gantt.scrollLeft - (label ? label.offsetWidth : 0) - 2 * cell.width;
        gantt.scrollLeft = Math.max(offset, 0);
    }

    // Etapa activa visible en el carrusel de etapas.
    var current = document.querySelector(".stepper .st.on");
    if (current && current.scrollIntoView) {
        current.scrollIntoView({ block: "nearest", inline: "center" });
    }

    // Cierra el menú de cuenta al tocar fuera.
    document.addEventListener("click", function (event) {
        document.querySelectorAll("details.menu[open]").forEach(function (menu) {
            if (!menu.contains(event.target)) { menu.removeAttribute("open"); }
        });
    });

    // Instalación (Android/Chrome/Edge).
    var deferred = null;
    window.addEventListener("beforeinstallprompt", function (event) {
        event.preventDefault();
        deferred = event;
        var button = document.getElementById("pwa-install");
        if (button) { button.hidden = false; }
    });
    document.addEventListener("click", function (event) {
        if (event.target && event.target.id === "pwa-install" && deferred) {
            deferred.prompt();
            deferred = null;
            event.target.hidden = true;
        }
    });
})();
