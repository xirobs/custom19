/** @odoo-module **/

import { Component, onMounted, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const REFRESH_MS = 60000;

export class InsuranceControlDashboard extends Component {
    static template = "insurance_management.ControlDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        const today = new Date();
        this.state = useState({
            data: null,
            loading: true,
            year: today.getFullYear(),
            month: today.getMonth() + 1,
            updatedAt: "",
        });
        this.incomeCanvas = useRef("incomeChart");
        this.trendCanvas = useRef("trendChart");
        this.charts = [];

        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
        onMounted(() => {
            this.timer = setInterval(() => this.load(), REFRESH_MS);
        });
        onWillUnmount(() => {
            clearInterval(this.timer);
            this.destroyCharts();
        });
        useEffect(
            () => {
                this.renderCharts();
            },
            () => [this.state.data]
        );
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "insurance.control.dashboard",
                "get_dashboard_data",
                [this.state.year, this.state.month]
            );
            this.state.updatedAt = new Date().toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" });
        } finally {
            this.state.loading = false;
        }
    }

    // ---------------- Periodo ----------------
    async shiftMonth(step) {
        let month = this.state.month + step;
        let year = this.state.year;
        if (month < 1) {
            month = 12;
            year -= 1;
        } else if (month > 12) {
            month = 1;
            year += 1;
        }
        this.state.month = month;
        this.state.year = year;
        await this.load();
    }

    // ---------------- Formato ----------------
    fmt(value, money = false) {
        if (value === null || value === undefined) {
            return "—";
        }
        return new Intl.NumberFormat("es-MX", {
            minimumFractionDigits: money ? 2 : 0,
            maximumFractionDigits: money ? 2 : 0,
        }).format(value);
    }

    deltaClass(delta) {
        if (delta === null || delta === undefined) {
            return "text-muted";
        }
        return delta >= 0 ? "text-success" : "text-danger";
    }

    deltaIcon(delta) {
        if (delta === null || delta === undefined) {
            return "fa-minus";
        }
        return delta >= 0 ? "fa-arrow-up" : "fa-arrow-down";
    }

    goalClass(pct) {
        if (pct === null || pct === undefined) {
            return "";
        }
        return pct >= 100 ? "bg-success" : pct >= 80 ? "bg-warning" : "bg-danger";
    }

    // ---------------- Navegación ----------------
    open(xmlid, context = {}) {
        this.actionService.doAction(xmlid, { additionalContext: context });
    }

    openPolicy(id) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "insurance.policy",
            res_id: id,
            views: [[false, "form"]],
        });
    }

    openCollection(currency, status) {
        const ctx = {};
        if (status) {
            ctx[`search_default_status_${status}`] = 1;
        }
        if (currency) {
            ctx[`search_default_${currency.toLowerCase()}`] = 1;
        }
        this.open("insurance_management.action_insurance_collection_board", ctx);
    }

    get shortcuts() {
        return [
            { label: "Cobranza", icon: "fa-money", xmlid: "insurance_management.action_insurance_collection_board" },
            { label: "Renovaciones", icon: "fa-refresh", xmlid: "insurance_management.action_insurance_renewals" },
            { label: "Qué hacer hoy", icon: "fa-check-square-o", xmlid: "insurance_management.action_insurance_task_today" },
            { label: "Agenda", icon: "fa-calendar", xmlid: "insurance_management.action_insurance_task" },
            { label: "Citas", icon: "fa-calendar-check-o", xmlid: "calendar.action_calendar_event" },
            { label: "Atención", icon: "fa-exclamation-triangle", xmlid: "insurance_management.action_insurance_policy_attention" },
            { label: "Pólizas", icon: "fa-shield", xmlid: "insurance_management.action_insurance_policy" },
            { label: "CRM", icon: "fa-handshake-o", xmlid: "insurance_management.action_insurance_crm_leads" },
            { label: "Ventas cruzadas", icon: "fa-users", xmlid: "insurance_management.action_insurance_cross_sell" },
            { label: "Siniestros", icon: "fa-medkit", xmlid: "insurance_management.action_insurance_claim" },
            { label: "Comisiones", icon: "fa-percent", xmlid: "insurance_management.action_insurance_commission" },
            { label: "Cargar carátula", icon: "fa-file-pdf-o", xmlid: "insurance_management.action_insurance_policy_pdf_import" },
        ];
    }

    // ---------------- Gráficas ----------------
    destroyCharts() {
        for (const chart of this.charts) {
            chart.destroy();
        }
        this.charts = [];
    }

    renderCharts() {
        this.destroyCharts();
        const data = this.state.data;
        if (!data || !window.Chart) {
            return;
        }
        const style = getComputedStyle(document.body);
        const text = style.getPropertyValue("--o-color-900") || "#374151";
        const grid = "rgba(128,128,128,0.15)";
        const common = {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: { legend: { position: "bottom", labels: { color: text, boxWidth: 12 } } },
            scales: {
                x: { grid: { display: false }, ticks: { color: text } },
                y: { beginAtZero: true, grid: { color: grid }, ticks: { color: text } },
            },
        };
        if (this.incomeCanvas.el) {
            const c = data.income_chart;
            this.charts.push(
                new window.Chart(this.incomeCanvas.el, {
                    type: "bar",
                    data: {
                        labels: c.labels,
                        datasets: [
                            { type: "bar", label: `${data.year}`, data: c.current, backgroundColor: "#2a78d6", borderRadius: 4, order: 2 },
                            { type: "line", label: `${data.year - 1}`, data: c.previous, borderColor: "#9ca3af", backgroundColor: "#9ca3af", tension: 0.3, pointRadius: 2, order: 1 },
                            { type: "line", label: "Meta", data: c.goal, borderColor: "#e0892b", backgroundColor: "#e0892b", borderDash: [6, 4], pointRadius: 0, order: 0 },
                        ],
                    },
                    options: common,
                })
            );
        }
        if (this.trendCanvas.el) {
            const t = data.trend_chart;
            this.charts.push(
                new window.Chart(this.trendCanvas.el, {
                    type: "line",
                    data: {
                        labels: t.labels,
                        datasets: [
                            { label: "Prospectos", data: t.prospects, borderColor: "#7c5cc4", backgroundColor: "#7c5cc4", tension: 0.3 },
                            { label: "Clientes nuevos", data: t.customers, borderColor: "#1f9d6b", backgroundColor: "#1f9d6b", tension: 0.3 },
                            { label: "Pólizas nuevas", data: t.policies, borderColor: "#2a78d6", backgroundColor: "#2a78d6", tension: 0.3 },
                        ],
                    },
                    options: { ...common, scales: { ...common.scales, y: { ...common.scales.y, ticks: { color: text, precision: 0 } } } },
                })
            );
        }
    }
}

registry.category("actions").add("insurance_management.control_dashboard", InsuranceControlDashboard);
