/** @odoo-module **/

/** Decorate the Frappe Gantt SVG with:
 *  - <defs> with gradient fills for normal / critical / milestone / state bars
 *  - .critical / .state-XXX / .has-custom-color classes on each .bar-wrapper
 *  - non-working day tints, project markers, progress line.
 */
export function renderCriticalPathDecorations(view) {
    const svg = view.ganttEl.el && view.ganttEl.el.querySelector("svg.gantt");
    if (!svg) return;

    const taskById = new Map(view.state.tasks.map(t => [t.id, t]));
    const wrappers = svg.querySelectorAll(".bar-wrapper");
    const critIds = view.state.criticalIds || new Set();
    wrappers.forEach((w) => {
        const id = w.getAttribute("data-id");
        if (!id) return;
        const num = parseInt(id, 10);
        const t = taskById.get(num);

        // Critical
        if (view.state.settings.criticalPaths && critIds.has(num)) {
            w.classList.add("critical");
        } else {
            w.classList.remove("critical");
        }

        // State class
        ["state-planned", "state-in_progress", "state-done", "state-blocked"].forEach(c => w.classList.remove(c));
        if (t && t.state) {
            w.classList.add(`state-${t.state}`);
        }

        // Per-task custom colour overrides everything.
        if (t && t.color_hex && /^#[0-9A-Fa-f]{6}$/.test(t.color_hex)) {
            w.classList.add("has-custom-color");
            w.style.setProperty("--bar-fill", t.color_hex);
            w.style.setProperty("--bar-stroke", _darken(t.color_hex, 0.25));
        } else {
            w.classList.remove("has-custom-color");
            w.style.removeProperty("--bar-fill");
            w.style.removeProperty("--bar-stroke");
        }
    });

    if (view.state.settings.highlightNonWorkingTime) {
        _drawNonWorking(svg, view);
    }
    if (view.state.settings.projectLines) {
        _drawProjectMarkers(view);
    }
    if (view.state.settings.showProgressLine) {
        _drawProgressLine(svg, view);
    }
}

/** Inject <defs> with gradient fills the bar SCSS references. Idempotent. */
function _ensureGradients(svg) {
    if (svg.querySelector("#mi-bar-gradient")) return;
    const SVG_NS = "http://www.w3.org/2000/svg";
    let defs = svg.querySelector("defs");
    if (!defs) {
        defs = document.createElementNS(SVG_NS, "defs");
        svg.insertBefore(defs, svg.firstChild);
    }
    const grads = [
        { id: "mi-bar-gradient",          stops: [["0%", "#F08750"], ["100%", "#B85620"]] },
        { id: "mi-bar-progress-gradient", stops: [["0%", "#B85620"], ["100%", "#8E3F15"]] },
        { id: "mi-bar-critical-gradient", stops: [["0%", "#E85D52"], ["100%", "#A82A20"]] },
        { id: "mi-bar-milestone-gradient",stops: [["0%", "#FFC93B"], ["100%", "#D89A00"]] },
        { id: "mi-bar-done-gradient",     stops: [["0%", "#3CC07A"], ["100%", "#138A47"]] },
        { id: "mi-bar-blocked-gradient",  stops: [["0%", "#9CA3AF"], ["100%", "#4B5563"]] },
        { id: "mi-bar-progress-state-gradient", stops: [["0%", "#FFB163"], ["100%", "#E8713A"]] },
    ];
    for (const g of grads) {
        const lg = document.createElementNS(SVG_NS, "linearGradient");
        lg.setAttribute("id", g.id);
        lg.setAttribute("x1", "0"); lg.setAttribute("y1", "0");
        lg.setAttribute("x2", "0"); lg.setAttribute("y2", "1");
        for (const [off, col] of g.stops) {
            const s = document.createElementNS(SVG_NS, "stop");
            s.setAttribute("offset", off);
            s.setAttribute("stop-color", col);
            lg.appendChild(s);
        }
        defs.appendChild(lg);
    }
}

function _darken(hex, ratio = 0.2) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    const f = 1 - ratio;
    const _h = (n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, "0");
    return `#${_h(r * f)}${_h(g * f)}${_h(b * f)}`;
}

function _drawNonWorking(svg, view) {
    // We rely on Frappe's grid header positioning to find day-cell widths.
    const grid = svg.querySelector(".grid");
    if (!grid) return;
    const ticks = svg.querySelectorAll(".tick");
    if (!ticks.length) return;
    // Wipe previous overlays.
    svg.querySelectorAll(".non-working").forEach(n => n.remove());
    const calendar = view.state.calendar || { workdays: [0, 1, 2, 3, 4], holidays: [] };
    // Heuristic: pull header dates from .lower-text to know which date each tick belongs to.
    // For week+ zooms, this is approximate (tints whole bands rather than days).
    const headerCells = svg.querySelectorAll(".lower-text");
    if (!headerCells.length) return;
    const gridY = parseFloat(grid.getAttribute("y") || 0);
    const gridH = parseFloat(grid.getAttribute("height") || 0);
    const SVG_NS = "http://www.w3.org/2000/svg";
    for (const cell of headerCells) {
        const txt = (cell.textContent || "").trim();
        // We only handle cell formats like "01-Jul" or "Jul 1" approximately;
        // weekend/holiday tinting is best-effort and primarily visual.
        const x = parseFloat(cell.getAttribute("x") || 0);
        const cellW = 32; // fallback width
        const date = _parseLowerText(txt, view);
        if (!date) continue;
        if (!_isWorkingJSDate(date, calendar)) {
            const rect = document.createElementNS(SVG_NS, "rect");
            rect.setAttribute("class", "non-working");
            rect.setAttribute("x", x - cellW / 2);
            rect.setAttribute("y", gridY);
            rect.setAttribute("width", cellW);
            rect.setAttribute("height", gridH);
            grid.appendChild(rect);
        }
    }
}

function _parseLowerText(t, view) {
    // Try ISO YYYY-MM-DD first, otherwise null.
    const m = /(\d{4})-(\d{2})-(\d{2})/.exec(t);
    if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
    return null;
}

function _isWorkingJSDate(d, cal) {
    const wd = d.getDay() === 0 ? 6 : d.getDay() - 1;
    if (!cal.workdays.includes(wd)) return false;
    const iso = d.toISOString().slice(0, 10);
    for (const h of cal.holidays || []) {
        if (h.date === iso) return false;
        if (h.recurring && h.date.slice(5) === iso.slice(5)) return false;
    }
    return true;
}

function _drawProjectMarkers(view) {
    const host = view.ganttEl.el;
    if (!host) return;
    host.querySelectorAll(".project-marker").forEach(n => n.remove());
    const project = view.state.projects.find(p => p.id === view.state.projectId);
    if (!project) return;
    const svg = host.querySelector("svg.gantt");
    if (!svg) return;
    const grid = svg.querySelector(".grid");
    if (!grid) return;

    const minDate = view.state.tasks.reduce((acc, t) =>
        !acc || t.date_start < acc ? t.date_start : acc, null);
    const maxDate = view.state.tasks.reduce((acc, t) =>
        !acc || t.date_end > acc ? t.date_end : acc, null);
    if (!minDate || !maxDate) return;

    const totalMs = new Date(maxDate) - new Date(minDate);
    const svgW = svg.clientWidth || svg.getBoundingClientRect().width;
    if (!totalMs || !svgW) return;

    const _xFor = (iso) => {
        const ms = new Date(iso) - new Date(minDate);
        return Math.round((ms / totalMs) * svgW);
    };

    if (project.date_start && project.date_start >= minDate) {
        _flagAt(host, _xFor(project.date_start), "Start", project.date_start);
    }
    if (project.date_end && project.date_end <= maxDate) {
        _flagAt(host, _xFor(project.date_end), "End", project.date_end);
    }
}

function _flagAt(host, x, label, dateText) {
    const div = document.createElement("div");
    div.className = "project-marker";
    div.style.left = `${x}px`;
    div.style.height = `${host.clientHeight}px`;
    const lab = document.createElement("div");
    lab.className = "label";
    lab.innerText = `${label} · ${dateText}`;
    div.appendChild(lab);
    host.appendChild(div);
}

function _drawProgressLine(svg, view) {
    svg.querySelectorAll(".progress-line").forEach(n => n.remove());
    if (!view.state.tasks.length) return;
    const today = new Date().toISOString().slice(0, 10);
    const tasks = view.state.tasks.filter(t => t.date_start <= today && t.date_end >= today);
    if (!tasks.length) return;
    const grid = svg.querySelector(".grid");
    if (!grid) return;
    const minDate = view.state.tasks.reduce((acc, t) =>
        !acc || t.date_start < acc ? t.date_start : acc, null);
    const maxDate = view.state.tasks.reduce((acc, t) =>
        !acc || t.date_end > acc ? t.date_end : acc, null);
    const totalMs = new Date(maxDate) - new Date(minDate);
    const svgW = svg.clientWidth || svg.getBoundingClientRect().width;
    if (!totalMs || !svgW) return;
    const SVG_NS = "http://www.w3.org/2000/svg";
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("class", "progress-line");
    const points = [];
    const wrappers = svg.querySelectorAll(".bar-wrapper");
    wrappers.forEach((w, i) => {
        const id = parseInt(w.getAttribute("data-id"), 10);
        const t = view.state.tasks.find(x => x.id === id);
        if (!t) return;
        const bar = w.querySelector(".bar");
        if (!bar) return;
        const x = parseFloat(bar.getAttribute("x") || 0);
        const y = parseFloat(bar.getAttribute("y") || 0);
        const w0 = parseFloat(bar.getAttribute("width") || 0);
        const h = parseFloat(bar.getAttribute("height") || 0);
        const xProgress = x + (w0 * (t.progress_pct || 0) / 100);
        points.push(`${xProgress},${y + h / 2}`);
    });
    if (points.length < 2) return;
    path.setAttribute("d", "M " + points.join(" L "));
    grid.appendChild(path);
}
