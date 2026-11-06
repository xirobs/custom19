/* App del equipo CreArt 3DP: registro PWA y confirmaciones. */
(function () {
    "use strict";
    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.register("/creart/app/sw.js", { scope: "/creart/app" }).catch(function () {});
    }
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            if (!window.confirm(form.dataset.confirm)) {
                event.preventDefault();
            }
        });
    });
    var deferred = null;
    window.addEventListener("beforeinstallprompt", function (event) {
        event.preventDefault();
        deferred = event;
        var button = document.getElementById("pwa-install");
        if (button) {
            button.hidden = false;
        }
    });
    document.addEventListener("click", function (event) {
        if (event.target && event.target.id === "pwa-install" && deferred) {
            deferred.prompt();
            deferred = null;
            event.target.hidden = true;
        }
    });
})();
