/** @odoo-module **/
// Tablero en los portales: los datos vienen en <script type="application/json" class="o_rss_dash_data">.
import { drawCharts } from "./rss_charts";

function start() {
    const root = document.querySelector(".o_rss_dash");
    const holder = document.querySelector(".o_rss_dash_data");
    if (!root || !holder) {
        return;
    }
    const data = JSON.parse(holder.dataset.json || "{}");
    let tries = 0;
    const draw = () => {
        if (window.Chart) {
            drawCharts(root, data);
        } else if (tries++ < 40) {
            setTimeout(draw, 150);
        }
    };
    draw();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
} else {
    start();
}
