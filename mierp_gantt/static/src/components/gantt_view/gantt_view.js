/** @odoo-module **/
import { Component, useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { computeCriticalPath } from "../../core/cpm";
import { autoSchedule } from "../../core/auto_scheduler";
import { exportMSPDI } from "../../core/mspdi_export";
import { TaskEditorPopup } from "../../widgets/task_editor_popup";
import { renderResourceHistogram } from "../../widgets/resource_histogram";
import { renderBaselineOverlay } from "../../widgets/baseline_overlay";
import { renderCriticalPathDecorations } from "../../widgets/critical_path_render";
import { bridgeIfcViewer } from "../../widgets/ifc_bridge";

const DEFAULT_SETTINGS = {
    drawDependencies: true,
    taskLabels: true,
    criticalPaths: true,
    projectLines: true,
    highlightNonWorkingTime: true,
    showBaselines: false,
    showProgressLine: true,
    cellEditing: true,
    showHistogram: true,
    hideSchedule: false,
};

const LIB_ROOT = "/mierp_gantt/static/lib";

const _libCache = {};

async function _injectScript(url) {
    if (_libCache[url]) return _libCache[url];
    _libCache[url] = new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = url;
        s.onload = () => resolve(true);
        s.onerror = (e) => reject(e);
        document.head.appendChild(s);
    });
    return _libCache[url];
}

async function _injectStyle(url) {
    if (_libCache[url]) return _libCache[url];
    _libCache[url] = new Promise((resolve) => {
        const l = document.createElement("link");
        l.rel = "stylesheet";
        l.href = url;
        l.onload = () => resolve(true);
        document.head.appendChild(l);
    });
    return _libCache[url];
}

export class GanttView extends Component {
    static template = "mierp_gantt.GanttView";
    static components = { TaskEditorPopup };
    static props = ["*"];

    setup() {
        this.action = useService("action");
        this.notification = useService("notification");
        this.orm = useService("orm");

        this.ganttEl = useRef("ganttEl");
        this.tabulatorEl = useRef("tabulatorEl");
        this.histogramEl = useRef("histogramEl");
        this.sidebarBox = useRef("sidebarBox");

        this.state = useState({
            projectId: this.props.action?.params?.project_id || null,
            projects: [],
            tasks: [],
            dependencies: [],
            baseline: null,
            calendar: null,
            settings: { ...DEFAULT_SETTINGS },
            zoomLevel: "week",
            searchTerm: "",
            sidebarWidth: 380,
            showSettings: false,
            editingTask: null,
            criticalIds: new Set(),
        });

        onMounted(() => this._init());
        onWillUnmount(() => this._teardown());
    }

    async _init() {
        await this._ensureLibs();
        await this._loadSettings();
        await this._loadProjects();
        if (!this.state.projectId && this.state.projects.length) {
            this.state.projectId = this.state.projects[0].id;
        }
        if (this.state.projectId) {
            await this._loadData();
        }
        this._renderTabulator();
        this._renderGantt();
        this._renderHistogram();
        bridgeIfcViewer(this);
    }

    _teardown() {
        try { this.gantt && this.gantt.clear && this.gantt.clear(); } catch (_) {}
        try { this.table && this.table.destroy && this.table.destroy(); } catch (_) {}
        try { this.histogram && this.histogram.dispose && this.histogram.dispose(); } catch (_) {}
    }

    async _ensureLibs() {
        await Promise.all([
            _injectScript(`${LIB_ROOT}/frappe_gantt/frappe-gantt.umd.js`),
            _injectStyle(`${LIB_ROOT}/frappe_gantt/frappe-gantt.css`),
            _injectScript(`${LIB_ROOT}/tabulator/tabulator.min.js`),
            _injectStyle(`${LIB_ROOT}/tabulator/tabulator_simple.min.css`),
            _injectScript(`${LIB_ROOT}/html2canvas/html2canvas.min.js`),
        ]);
    }

    async _loadSettings() {
        try {
            const s = await rpc("/mierp/gantt/load_settings", {});
            if (s && typeof s === "object") {
                Object.assign(this.state.settings, s);
            }
        } catch (e) { /* keep defaults */ }
    }

    async _persistSettings() {
        try {
            await rpc("/mierp/gantt/save_settings", { settings: { ...this.state.settings } });
        } catch (e) { /* best effort */ }
    }

    async _loadProjects() {
        const projects = await rpc("/mierp/gantt/projects", {});
        this.state.projects = projects;
    }

    async _loadData() {
        const data = await rpc("/mierp/gantt/data", { project_id: this.state.projectId });
        if (data && !data.error) {
            this.state.tasks = data.tasks || [];
            this.state.dependencies = data.dependencies || [];
            this.state.calendar = data.calendar;
            this.state.baseline = data.baseline;
            const cpm = computeCriticalPath(this.state.tasks, this.state.dependencies, this.state.calendar);
            this.state.criticalIds = new Set(cpm.criticalIds);
        }
    }

    _renderTabulator() {
        if (!window.Tabulator || !this.tabulatorEl.el) return;
        if (this.table) {
            this.table.destroy();
            this.table = null;
        }
        const tree = this._buildTreeData();
        this.table = new window.Tabulator(this.tabulatorEl.el, {
            height: "100%",
            data: tree,
            dataTree: true,
            dataTreeStartExpanded: true,
            dataTreeChildField: "_children",
            dataTreeBranchElement: "<span>└─ </span>",
            layout: "fitDataStretch",
            placeholder: "No tasks",
            columns: [
                { title: "WBS",      field: "wbs",            width: 80,  frozen: true, hozAlign: "center" },
                { title: "Name",     field: "name",           minWidth: 200, editor: "input", widthGrow: 3 },
                { title: "Start",    field: "date_start",     width: 110, editor: "date" },
                { title: "End",      field: "date_end",       width: 110, editor: "date" },
                { title: "Days",     field: "duration_days",  width: 70,  editor: "number", hozAlign: "right" },
                { title: "%",        field: "progress_pct",   width: 70,  editor: "number", hozAlign: "right",
                  formatter: (cell) => `${Math.round(cell.getValue() || 0)}%` },
                { title: "Resources", field: "resource_names", widthGrow: 1 },
            ],
        });
        this.table.on("cellEdited", (cell) => this._onSidebarEdit(cell));
        this.table.on("rowClick", (e, row) => {
            const data = row.getData();
            if (this.gantt && this.gantt.scroll_current) {
                try { this.gantt.scroll_current(); } catch (_) {}
            }
            this.state.editingTask = data;
        });
    }

    _buildTreeData() {
        const byParent = new Map();
        for (const t of this.state.tasks) {
            const p = t.parent_id || 0;
            if (!byParent.has(p)) byParent.set(p, []);
            byParent.get(p).push({ ...t });
        }
        const attach = (node) => {
            const kids = byParent.get(node.id) || [];
            if (kids.length) {
                node._children = kids.map(attach);
            }
            return node;
        };
        const roots = (byParent.get(0) || byParent.get(false) || []).map(attach);
        return roots;
    }

    _renderGantt() {
        if (!window.Gantt || !this.ganttEl.el) return;
        if (this.state.settings.hideSchedule) return;
        const filterTerm = (this.state.searchTerm || "").toLowerCase();
        const tasks = this.state.tasks
            .filter((t) => !filterTerm || (t.name || "").toLowerCase().includes(filterTerm))
            .map((t) => ({
                id: String(t.id),
                name: t.name,
                start: t.date_start,
                end: t.date_end,
                progress: t.progress_pct || 0,
                dependencies: this.state.dependencies
                    .filter((d) => d.successor === t.id && d.type === "FS")
                    .map((d) => String(d.predecessor))
                    .join(","),
                custom_class: this._classFor(t),
            }));
        if (!tasks.length) {
            this.ganttEl.el.innerHTML = "";
            return;
        }
        // Frappe Gantt destroys the SVG on rerender via .refresh().
        this.ganttEl.el.innerHTML = "";
        try {
            this.gantt = new window.Gantt(this.ganttEl.el, tasks, {
                view_mode: this._viewMode(),
                language: "en",
                bar_height: 24,
                bar_corner_radius: 4,
                arrow_curve: this.state.settings.drawDependencies ? 6 : 0,
                date_format: "YYYY-MM-DD",
                readonly: !this.state.settings.cellEditing,
                on_click: (t) => this._onBarClick(t),
                on_date_change: (t, start, end) => this._onTaskDateChange(t, start, end),
                on_progress_change: (t, p) => this._onProgressChange(t, p),
                on_view_change: (m) => { this.state.zoomLevel = this._modeFromView(m); },
                custom_popup_html: (t) => this._popupHtml(t),
            });
        } catch (e) {
            console.warn("Frappe Gantt init error:", e);
        }
        // Decorations on top of the SVG: critical path classes, baseline ghost,
        // non-working highlight, project markers, progress line.
        renderCriticalPathDecorations(this);
        if (this.state.settings.showBaselines && this.state.baseline) {
            renderBaselineOverlay(this);
        }
        // Frappe Gantt's .gantt-container is hardcoded to 500px and
        // overflow:auto, which clips long projects. After it renders,
        // resize the canvas + container to fit the SVG so every task
        // row is visible without internal scroll. Lets the page scroll
        // when the project is taller than the viewport.
        this._fitCanvasToSvg();
    }

    _fitCanvasToSvg() {
        if (!this.ganttEl?.el) return;
        const container = this.ganttEl.el.querySelector(".gantt-container");
        const svg = this.ganttEl.el.querySelector("svg.gantt");
        if (!svg) return;
        const h = parseInt(svg.getAttribute("height"), 10)
            || svg.getBoundingClientRect().height;
        if (!h) return;
        if (container) {
            container.style.height = `${h}px`;
            container.style.overflow = "visible";
        }
        // The canvas (.mierp-gantt-canvas) is the scroll container that
        // Odoo's flex layout bounds. Match its min-height to the SVG so
        // the whole page grows and the user sees all bars at once.
        this.ganttEl.el.style.minHeight = `${h}px`;
    }

    _classFor(t) {
        const c = [];
        if (this.state.settings.criticalPaths && this.state.criticalIds.has(t.id)) c.push("critical");
        if (t.is_milestone) c.push("milestone");
        return c.join(" ");
    }

    _viewMode() {
        return ({ day: "Day", week: "Week", month: "Month", quarter: "Quarter Day" })[this.state.zoomLevel] || "Week";
    }
    _modeFromView(m) {
        return ({ "Day": "day", "Week": "week", "Month": "month", "Quarter Day": "quarter" })[m] || "week";
    }

    _popupHtml(taskWrap) {
        const t = this.state.tasks.find(x => String(x.id) === String(taskWrap.id || taskWrap.task?.id));
        if (!t) return "";
        const fmt = (v) => v || "—";
        const isCrit = this.state.criticalIds.has(t.id);
        return `
            <div class="mierp-gantt-popup">
                <h6>${t.name}</h6>
                <div class="popup-row"><span>WBS</span><b>${fmt(t.wbs)}</b></div>
                <div class="popup-row"><span>Start</span><b>${fmt(t.date_start)}</b></div>
                <div class="popup-row"><span>End</span><b>${fmt(t.date_end)}</b></div>
                <div class="popup-row"><span>Duration</span><b>${(t.duration_days || 0).toFixed(1)} d</b></div>
                <div class="popup-row"><span>Progress</span><b>${Math.round(t.progress_pct || 0)} %</b></div>
                ${isCrit ? '<div class="popup-row" style="color: var(--mi-red, #DB4437)"><span>⚠ Critical path</span></div>' : ""}
                ${t.resource_names ? `<div class="popup-row"><span>Resources</span><b>${t.resource_names}</b></div>` : ""}
            </div>
        `;
    }

    async _renderHistogram() {
        if (!this.state.settings.showHistogram || !this.state.tasks.length) return;
        if (!this.histogramEl.el) return;
        try {
            const rows = await rpc("/mierp/gantt/histogram", { project_id: this.state.projectId });
            renderResourceHistogram(this, rows || []);
        } catch (e) {
            console.warn("histogram error", e);
        }
    }

    // ------------------------------------------------------------------
    // Event handlers
    // ------------------------------------------------------------------

    onSelectProject(ev) {
        const id = parseInt(ev.target.value, 10);
        this.state.projectId = id;
        this._loadData().then(() => {
            this._renderTabulator();
            this._renderGantt();
            this._renderHistogram();
        });
    }

    setZoom(level) {
        this.state.zoomLevel = level;
        this._renderGantt();
    }

    onSearchInput(ev) {
        this.state.searchTerm = ev.target.value;
        this._renderGantt();
    }

    onToggleSettings() {
        this.state.showSettings = !this.state.showSettings;
    }

    toggleSetting(key, ev) {
        this.state.settings[key] = !!ev.target.checked;
        this._persistSettings();
        this._renderTabulator();
        this._renderGantt();
        this._renderHistogram();
    }

    onNewTask() {
        if (!this.state.projectId) return;
        this.orm.call("mierp.gantt.task", "create", [{
            name: "New task",
            project_id: this.state.projectId,
            date_start: new Date().toISOString().slice(0, 10),
            date_end: new Date(Date.now() + 5 * 86400000).toISOString().slice(0, 10),
        }]).then(() => {
            this._loadData().then(() => {
                this._renderTabulator();
                this._renderGantt();
            });
        });
    }

    onEditTask() {
        if (!this.state.tasks.length) return;
        this.state.editingTask = { ...(this.state.tasks[0]) };
    }

    onCloseEditor() {
        this.state.editingTask = null;
    }

    async onSaveTask(t) {
        const id = t.id;
        const vals = {
            name: t.name,
            date_start: t.date_start,
            date_end: t.date_end,
            progress_pct: t.progress_pct,
            is_milestone: !!t.is_milestone,
            color_hex: t.color_hex || false,
        };
        await this.orm.call("mierp.gantt.task", "write", [[id], vals]);
        this.state.editingTask = null;
        await this._loadData();
        this._renderTabulator();
        this._renderGantt();
    }

    async _onSidebarEdit(cell) {
        const data = cell.getData();
        const field = cell.getField();
        const map = {
            name: "name",
            date_start: "date_start",
            date_end: "date_end",
            duration_days: "duration_days",
            progress_pct: "progress_pct",
        };
        const writeField = map[field];
        if (!writeField) return;
        await this.orm.call("mierp.gantt.task", "write", [[data.id], { [writeField]: cell.getValue() }]);
        await this._loadData();
        this._renderGantt();
    }

    async _onTaskDateChange(taskWrap, start, end) {
        const id = parseInt(taskWrap.id, 10);
        const startStr = this._toIso(start);
        const endStr = this._toIso(end);
        await this.orm.call("mierp.gantt.task", "write", [[id], {
            date_start: startStr,
            date_end: endStr,
        }]);
        // Auto-schedule successors.
        const updates = autoSchedule(
            id, startStr, endStr,
            this.state.tasks, this.state.dependencies, this.state.calendar,
        );
        for (const u of updates) {
            await this.orm.call("mierp.gantt.task", "write", [[u.id], {
                date_start: u.date_start,
                date_end: u.date_end,
            }]);
        }
        await this._loadData();
        this._renderTabulator();
        this._renderGantt();
        if (updates.length) {
            this.notification.add(`Auto-scheduled ${updates.length} successor(s).`, { type: "info" });
        }
    }

    async _onProgressChange(taskWrap, progress) {
        const id = parseInt(taskWrap.id, 10);
        await this.orm.call("mierp.gantt.task", "write", [[id], { progress_pct: progress }]);
    }

    _onBarClick(taskWrap) {
        const id = parseInt(taskWrap.id, 10);
        const t = this.state.tasks.find(x => x.id === id);
        if (t) this.state.editingTask = { ...t };
        // Bus event for IFC bridge.
        document.dispatchEvent(new CustomEvent("mierp-gantt:task-click", {
            detail: { taskId: id, task: t },
        }));
    }

    _toIso(d) {
        if (!d) return null;
        if (typeof d === "string") return d.slice(0, 10);
        return new Date(d).toISOString().slice(0, 10);
    }

    onSnapshotBaseline() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "mierp.gantt.snapshot.baseline",
            view_mode: "form",
            target: "new",
            context: { default_project_id: this.state.projectId },
        });
    }

    onExportMSP() {
        try {
            const xml = exportMSPDI(
                this.state.projects.find(p => p.id === this.state.projectId) || {},
                this.state.tasks,
                this.state.dependencies,
                this.state.calendar,
            );
            const blob = new Blob([xml], { type: "application/xml" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            const name = (this.state.projects.find(p => p.id === this.state.projectId) || {}).name || "gantt";
            a.download = `${name.replace(/\s+/g, "_")}.xml`;
            a.click();
            URL.revokeObjectURL(url);
            this.notification.add("MSPDI XML exported.", { type: "success" });
        } catch (e) {
            this.notification.add(`Export failed: ${e.message || e}`, { type: "danger" });
        }
    }

    onImportMSP() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "mierp.gantt.import.msproject",
            view_mode: "form",
            target: "new",
            context: { default_project_id: this.state.projectId },
        });
    }

    async onExportPNG() {
        if (!window.html2canvas) return;
        const canvas = await window.html2canvas(this.ganttEl.el, { backgroundColor: "#ffffff" });
        const url = canvas.toDataURL("image/png");
        const a = document.createElement("a");
        a.href = url;
        a.download = "gantt.png";
        a.click();
    }

    onDividerDown(ev) {
        const startX = ev.clientX;
        const startW = this.state.sidebarWidth;
        const move = (mv) => {
            this.state.sidebarWidth = Math.max(180, Math.min(800, startW + (mv.clientX - startX)));
        };
        const up = () => {
            document.removeEventListener("mousemove", move);
            document.removeEventListener("mouseup", up);
        };
        document.addEventListener("mousemove", move);
        document.addEventListener("mouseup", up);
    }
}

registry.category("actions").add("mierp_gantt.view", GanttView);
