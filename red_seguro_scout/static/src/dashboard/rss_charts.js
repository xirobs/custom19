/** @odoo-module **/
// Gráficas del tablero de Red Seguro Scout (plataforma y portales). Usa Chart.js de Odoo.

export const RSS_COLORS = ["#6E0B14", "#E9A825", "#B23A48", "#2F5D62", "#8C6A5D", "#C98B2E", "#4A1F2B", "#7FA99B"];

const money = (v) => "$ " + Number(v || 0).toLocaleString("es-MX", { maximumFractionDigits: 0 });

function isDark() {
    return document.documentElement.dataset.colorScheme === "dark"
        || document.body.classList.contains("o_dark")
        || (window.getComputedStyle(document.body).backgroundColor || "").match(/rgb\((\d+)/)?.[1] < 80;
}

const SPECS = {
    by_group: { type: "bar", stacked: true, horizontal: true },
    sex: { type: "doughnut" },
    section: { type: "bar" },
    age: { type: "bar" },
    city: { type: "doughnut" },
    new_by_month: { type: "line", label: "Altas" },
    paid_by_month: { type: "bar", label: "Cobrado", money: true },
    paid_by_group: { type: "bar", label: "Cobrado", money: true, horizontal: true },
    claims_by_state: { type: "doughnut" },
    claims_by_injury: { type: "bar", horizontal: true },
    claims_by_activity: { type: "bar", horizontal: true },
    claims_by_month: { type: "line", label: "Siniestros" },
};

function config(key, chart) {
    const spec = SPECS[key] || { type: "bar" };
    const dark = isDark();
    const text = dark ? "#e8e1d8" : "#4a3b33";
    const grid = dark ? "rgba(255,255,255,.08)" : "rgba(110,11,20,.08)";
    let datasets;
    if (chart.series) {
        datasets = chart.series.map((s, i) => ({ label: s.name, data: s.data, backgroundColor: RSS_COLORS[i % RSS_COLORS.length],
            borderRadius: 4, maxBarThickness: 28 }));
    } else if (spec.type === "doughnut") {
        datasets = [{ data: chart.data, backgroundColor: RSS_COLORS, borderColor: dark ? "#1f1b1b" : "#fff", borderWidth: 2 }];
    } else if (spec.type === "line") {
        datasets = [{ label: spec.label, data: chart.data, borderColor: RSS_COLORS[0], backgroundColor: "rgba(110,11,20,.12)",
            fill: true, tension: 0.3, pointRadius: 3, pointBackgroundColor: RSS_COLORS[1] }];
    } else {
        datasets = [{ label: spec.label || "", data: chart.data, backgroundColor: RSS_COLORS[0], borderRadius: 4, maxBarThickness: 32 }];
    }
    const axis = (stacked, isMoney) => ({ stacked, grid: { color: grid }, ticks: { color: text, precision: 0,
        callback: isMoney ? (v) => money(v) : undefined } });
    const options = {
        responsive: true, maintainAspectRatio: false, animation: { duration: 300 },
        indexAxis: spec.horizontal ? "y" : "x",
        plugins: {
            legend: { display: spec.type === "doughnut" || !!chart.series, position: "bottom", labels: { color: text, boxWidth: 12 } },
            tooltip: { callbacks: spec.money ? { label: (ctx) => money(ctx.parsed[spec.horizontal ? "x" : "y"]) } : {} },
        },
    };
    if (spec.type !== "doughnut") {
        options.scales = spec.horizontal
            ? { x: axis(spec.stacked, spec.money), y: { stacked: spec.stacked, grid: { display: false }, ticks: { color: text } } }
            : { x: { stacked: spec.stacked, grid: { display: false }, ticks: { color: text } }, y: axis(spec.stacked, spec.money) };
    } else {
        options.cutout = "62%";
    }
    return { type: spec.type, data: { labels: chart.labels, datasets }, options };
}

/** Dibuja cada <canvas data-rss-chart="clave"> dentro de root con los datos del tablero. */
export function drawCharts(root, data, previous = []) {
    previous.forEach((c) => c.destroy());
    const Chart = window.Chart;
    if (!Chart || !data) {
        return [];
    }
    const made = [];
    root.querySelectorAll("canvas[data-rss-chart]").forEach((canvas) => {
        const key = canvas.dataset.rssChart;
        const chart = data.charts[key];
        if (!chart) {
            return;
        }
        const empty = chart.series ? !chart.series.some((s) => s.data.some(Boolean)) : !chart.data.some(Boolean);
        canvas.closest(".o_rss_chart")?.classList.toggle("o_rss_chart_empty", empty);
        if (!empty) {
            made.push(new Chart(canvas, config(key, chart)));
        }
    });
    return made;
}
