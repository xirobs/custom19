/** @odoo-module **/

/** Render a stacked-bar histogram of effective resource hours per day. */
export async function renderResourceHistogram(view, rows) {
    if (!view.histogramEl.el) return;
    // ECharts is shared with mierp_construction; load on demand if missing.
    if (!window.echarts) {
        await new Promise((resolve) => {
            const s = document.createElement("script");
            s.src = "/mierp_construction/static/lib/echarts.min.js";
            s.onload = resolve;
            s.onerror = resolve;
            document.head.appendChild(s);
        });
    }
    if (!window.echarts) return;
    if (view.histogram) {
        view.histogram.dispose();
    }
    view.histogram = window.echarts.init(view.histogramEl.el, null, { renderer: "svg" });

    const series = {};
    const dayBuckets = new Set();
    for (const r of rows) {
        if (!r.date_start || !r.date_end) continue;
        const start = new Date(r.date_start);
        const end = new Date(r.date_end);
        const cursor = new Date(start);
        while (cursor <= end) {
            const iso = cursor.toISOString().slice(0, 10);
            dayBuckets.add(iso);
            if (!series[r.resource_name]) series[r.resource_name] = {};
            series[r.resource_name][iso] = (series[r.resource_name][iso] || 0) + (r.effective || 0);
            cursor.setDate(cursor.getDate() + 1);
        }
    }
    const xAxis = [...dayBuckets].sort();
    const seriesOption = Object.entries(series).map(([name, byDay]) => ({
        name,
        type: "bar",
        stack: "load",
        emphasis: { focus: "series" },
        itemStyle: {
            shadowBlur: 10,
            shadowColor: "rgba(0,0,0,0.25)",
            shadowOffsetX: 0,
            shadowOffsetY: 4,
        },
        data: xAxis.map(d => Math.round((byDay[d] || 0) * 10) / 10),
    }));
    view.histogram.setOption({
        animationDuration: 200,
        textStyle: { color: "#1a1a1a" },
        color: ["#E8713A", "#B85620", "#F4B400", "#4b5563", "#7c3aed", "#0891b2", "#16a34a"],
        legend: { top: 0 },
        grid: { left: 40, right: 16, top: 28, bottom: 24 },
        tooltip: { trigger: "axis" },
        xAxis: { type: "category", data: xAxis, axisLabel: { fontSize: 10 } },
        yAxis: { type: "value", name: "Resource units" },
        series: seriesOption,
    });
}
