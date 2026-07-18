# MI ERP Gantt Open-Source Pro

Modern interactive Gantt chart for Odoo 19, built entirely on **MIT-licensed
vendored libraries** — no runtime royalties, no CDN dependencies.

| Stack | Library | License |
|---|---|---|
| Render engine | [Frappe Gantt](https://github.com/frappe/gantt) v0.9 | MIT |
| Sidebar table | [Tabulator](https://tabulator.info) v6.3 | MIT |
| Snapshots (PNG) | [html2canvas](https://html2canvas.hertzen.com) v1.4 | MIT |
| Resource histogram | [Apache ECharts](https://echarts.apache.org) v5 (shared) | Apache 2.0 |
| MS Project export/import | Custom MSPDI XML | Proprietary |
| Critical Path Method | Custom JS (forward + backward pass) | Proprietary |

---

## Highlights

- **Critical Path Method** computed client-side with topological sort + forward
  / backward pass. Works on FS / SS / FF / SF dependencies with lag and lead.
- **Auto-scheduling**: drag a task → all FS/SS/FF/SF successors recalculate
  while respecting working calendars (workdays + holidays).
- **Multi-baseline overlay**: snapshot the schedule, then compare planned vs
  actual with ghost bars on top of the live timeline.
- **Working calendars** for Spain, Colombia, Venezuela are shipped out of the
  box with their public holidays. Add yours from *Gantt → Configuration → Working Calendars*.
- **Settings dropdown** with 10 toggles (Bryntum-style): dependencies, labels,
  critical path, project markers, non-working highlights, baselines, progress
  line, cell editing, resource histogram, list-only mode.
- **Microsoft Project (MSPDI XML)** export and import — round-trip safe.
- **PNG snapshot** of the rendered Gantt for status reports.

## Architecture

```
mierp_gantt           ← this addon (generic, depends on web only)
└─ mierp_construction_gantt   ← optional bridge: links a construction.work
                                 to a gantt.project and exposes the IFC
                                 federation hook for xeokit.
```

Models:

- `mierp.gantt.project` — schedulable container.
- `mierp.gantt.task` — hierarchical task with date_start/end, duration,
  progress, predecessors, successors, resources.
- `mierp.gantt.dependency` — FS/SS/FF/SF link with `lag_days`.
- `mierp.gantt.calendar` + `mierp.gantt.holiday` — working time.
- `mierp.gantt.baseline` + `…line` — schedule snapshots.
- `mierp.gantt.resource.assignment` — generic labour/material/equipment
  allocation.

## Installation

```
odoo-bin -c odoo.conf -d <db> -i mierp_gantt --stop-after-init
```

Open `Gantt → Interactive Gantt` from the navbar.

## Pricing

USD 2 400 per Odoo install (perpetual). Bundled in the
[**MI ERP BIM Suite**](https://www.mi-erp.app/bim-suite) for construction firms.

## Support

`info@mi-erp.app` · GRUPO MI ERP SAS · Pereira, Colombia
