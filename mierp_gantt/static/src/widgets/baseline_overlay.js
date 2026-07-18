/** @odoo-module **/

/** Draw ghost-bars over the live SVG showing each task's baseline range. */
export function renderBaselineOverlay(view) {
    if (!view.state.baseline || !view.state.baseline.lines) return;
    const svg = view.ganttEl.el.querySelector("svg.gantt");
    if (!svg) return;
    svg.querySelectorAll(".baseline-bar").forEach(n => n.remove());
    const SVG_NS = "http://www.w3.org/2000/svg";
    const grid = svg.querySelector(".grid");
    if (!grid) return;

    const minDate = view.state.tasks.reduce((acc, t) =>
        !acc || t.date_start < acc ? t.date_start : acc, null);
    const maxDate = view.state.tasks.reduce((acc, t) =>
        !acc || t.date_end > acc ? t.date_end : acc, null);
    const totalMs = new Date(maxDate) - new Date(minDate);
    const svgW = svg.clientWidth || svg.getBoundingClientRect().width;
    if (!totalMs || !svgW) return;
    const _xFor = (iso) =>
        Math.round((new Date(iso) - new Date(minDate)) * svgW / totalMs);

    const lineByTask = new Map(view.state.baseline.lines.map(l => [l.task_id, l]));
    const wrappers = svg.querySelectorAll(".bar-wrapper");
    wrappers.forEach((w) => {
        const id = parseInt(w.getAttribute("data-id"), 10);
        const baseline = lineByTask.get(id);
        if (!baseline) return;
        const bar = w.querySelector(".bar");
        if (!bar) return;
        const y = parseFloat(bar.getAttribute("y") || 0);
        const h = parseFloat(bar.getAttribute("height") || 0);
        const ghost = document.createElementNS(SVG_NS, "rect");
        ghost.setAttribute("class", "baseline-bar");
        const xs = _xFor(baseline.date_start);
        const xe = _xFor(baseline.date_end);
        ghost.setAttribute("x", xs);
        ghost.setAttribute("y", y - 3);
        ghost.setAttribute("width", Math.max(2, xe - xs));
        ghost.setAttribute("height", h + 6);
        ghost.setAttribute("rx", 6);
        ghost.setAttribute("ry", 6);
        // Insert ghost AFTER the wrapper so it doesn't cover the live bar.
        // (SVG paints later siblings on top — but with fill:none and
        // dasharray it's only a thin outline that doesn't obscure.)
        w.parentNode.insertBefore(ghost, w.nextSibling);
    });
}
