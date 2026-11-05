/** @odoo-module **/
import { Component, onMounted, onPatched, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { drawCharts } from "./rss_charts";

const money = (v) => "$ " + Number(v || 0).toLocaleString("es-MX", { maximumFractionDigits: 0 });

export class RssDashboard extends Component {
    static template = "red_seguro_scout.RssDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.root = useRef("root");
        this.charts = [];
        this.money = money;
        this.state = useState({ data: null, groups: [], groupId: "", loading: true });
        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            this.state.groups = await this.orm.searchRead("rss.scout.group", [], ["id", "name"], { order: "number, name" });
            await this.load();
        });
        onMounted(() => this.draw());
        onPatched(() => this.draw());
        onWillUnmount(() => this.charts.forEach((c) => c.destroy()));
    }

    async load() {
        this.state.loading = true;
        const ids = this.state.groupId ? [parseInt(this.state.groupId)] : false;
        this.state.data = await this.orm.call("rss.dashboard", "get_data", [ids]);
        this.state.loading = false;
    }

    draw() {
        if (this.root.el && this.state.data) {
            this.charts = drawCharts(this.root.el, this.state.data, this.charts);
        }
    }

    async onGroup(ev) {
        this.state.groupId = ev.target.value;
        await this.load();
    }

    get groupDomain() {
        return this.state.groupId ? [["group_id", "=", parseInt(this.state.groupId)]] : [];
    }

    sum(list) {
        return list.reduce((a, b) => a + b, 0);
    }

    colSum(values, ci) {
        return values.reduce((a, row) => a + row[ci], 0);
    }

    open(model, name, extraDomain = [], views = ["list", "pivot", "graph", "form"], context = {}) {
        this.action.doAction({
            type: "ir.actions.act_window", name, res_model: model,
            domain: [...this.groupDomain, ...extraDomain],
            views: views.map((v) => [false, v]), context,
        });
    }

    openInsured(extra = [], name = "Asegurados") {
        this.open("rss.insured", name, extra);
    }

    openPivot(kind) {
        const ctx = {
            insured: { pivot_row_groupby: ["group_id"], pivot_column_groupby: ["section"], pivot_measures: ["__count"] },
            age: { pivot_row_groupby: ["age"], pivot_column_groupby: ["sex"], pivot_measures: ["__count"] },
            claims: { pivot_row_groupby: ["group_id"], pivot_column_groupby: ["state"],
                pivot_measures: ["__count", "amount_claimed", "amount_paid"] },
            payments: { pivot_row_groupby: ["group_id"], pivot_column_groupby: ["date:month"], pivot_measures: ["amount"] },
        }[kind];
        const model = { insured: "rss.insured", age: "rss.insured", claims: "rss.claim", payments: "rss.payment" }[kind];
        const name = { insured: "Asegurados: grupo × rama", age: "Asegurados: edad × sexo", claims: "Siniestros",
            payments: "Pagos por mes" }[kind];
        this.open(model, name, kind === "payments" ? [["state", "in", ["confirmed", "remitted"]]] : [],
            ["pivot", "graph", "list", "form"], ctx);
    }

    async downloadXlsx() {
        const ids = this.state.groupId ? [parseInt(this.state.groupId)] : [];
        await this.action.doAction({ type: "ir.actions.act_url", target: "download",
            url: "/red-seguro-scout/tablero.xlsx" + (ids.length ? "?grupo=" + ids[0] : "") });
    }
}

registry.category("actions").add("rss_dashboard", RssDashboard);
