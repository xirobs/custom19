/** @odoo-module **/

/** Wire the Gantt with an xeokit viewer if any is present in the page.
 *
 *  This module is generic — when the user installs the
 *  `mierp_construction_gantt` bridge, it sets `window.MIERP_XEOKIT_VIEWER`
 *  on its viewer init so this listener picks it up automatically. */
export function bridgeIfcViewer(view) {
    document.addEventListener("mierp-gantt:task-click", (ev) => {
        const viewer = window.MIERP_XEOKIT_VIEWER;
        if (!viewer || !ev.detail) return;
        const t = ev.detail.task;
        const ids = (t && t.bim_element_ids) || [];
        if (!ids.length) return;
        try {
            viewer.scene.setObjectsHighlighted(ids, true);
            if (viewer.cameraFlight && viewer.scene.getAABB) {
                viewer.cameraFlight.flyTo({
                    aabb: viewer.scene.getAABB(ids),
                    duration: 0.6,
                });
            }
        } catch (_) { /* viewer may not be ready */ }
    });

    // Public 4D animation helper exposed for the bridge module to call.
    view.play4D = (durationMs = 10000) => {
        const viewer = window.MIERP_XEOKIT_VIEWER;
        if (!viewer) return;
        const tasks = view.state.tasks;
        if (!tasks.length) return;
        const start = +new Date(tasks.reduce((a, t) => !a || t.date_start < a ? t.date_start : a, null));
        const end   = +new Date(tasks.reduce((a, t) => !a || t.date_end   > a ? t.date_end   : a, null));
        if (!start || !end || end <= start) return;
        const t0 = performance.now();
        const animate = (t) => {
            const ratio = (t - t0) / durationMs;
            if (ratio > 1) return;
            const cursor = new Date(start + ratio * (end - start));
            const cursorIso = cursor.toISOString().slice(0, 10);
            const colorMap = {
                pending:     [0.7, 0.7, 0.7],
                in_progress: [1.0, 0.8, 0.2],
                done:        [0.2, 0.8, 0.4],
            };
            for (const tk of tasks) {
                let status = "pending";
                if (tk.date_start <= cursorIso && tk.date_end >= cursorIso) status = "in_progress";
                if (tk.date_end < cursorIso) status = "done";
                try {
                    viewer.scene.setObjectsColorized(tk.bim_element_ids || [], colorMap[status]);
                } catch (_) {}
            }
            requestAnimationFrame(animate);
        };
        requestAnimationFrame(animate);
    };
}
