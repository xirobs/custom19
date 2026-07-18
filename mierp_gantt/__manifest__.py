# -*- coding: utf-8 -*-
{
    "name": "MI ERP Gantt Pro",
    "version": "19.0.1.0.0",
    "category": "Project",
    "summary": "Modern interactive Gantt chart with Critical Path, Baselines, Auto-scheduling and MS Project XML import/export. 100% MIT vendored libs.",
    "description": """
MI ERP Gantt Pro
================

A standalone, generic Gantt chart engine for Odoo 19, suitable for any module
that needs to schedule activities (construction works, software projects,
event planning, manufacturing campaigns).

Features
--------
* Beautiful Gantt rendered with Frappe Gantt (MIT) — vendored, no CDN.
* Hierarchical sidebar with WBS / Name / Start / Duration / Resources powered
  by Tabulator (MIT).
* Critical Path Method (forward + backward pass) computed client-side.
* Multi-baseline overlay to compare planned vs actual.
* Auto-scheduling of successors when a task moves, respecting working
  calendars (workdays + holidays).
* Four dependency types: FS, SS, FF, SF with lag/lead in working days.
* Resource allocation histogram (ECharts).
* Export to Microsoft Project (MSPDI XML) and import from .xml.
* Snapshot to PNG/PDF via html2canvas.
* Settings dropdown: Critical paths, Baselines, Project lines, Non-working
  highlight, Cell editing, Show progress line, Show rollups, etc.
* Multi-project selector and zoom (day / week / month / quarter).
* Designed to be extended — `mierp_construction_gantt` adds the BIM/IFC
  federation bridge. Any module can build its own bridge by inheriting
  `mierp.gantt.task` / `mierp.gantt.project`.

Built by GRUPO MI ERP SAS · Pereira, Colombia.
""",
    "author": "MI ERP APP",
    "website": "https://www.mi-erp.app",
    "license": "LGPL-3",
    "support": "info@mi-erp.app",
    "depends": [
        "base",
        "mail",
        "web",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/ir_rule.xml",
        "data/gantt_calendar_data.xml",
        "views/gantt_calendar_views.xml",
        "views/gantt_baseline_views.xml",
        "views/gantt_project_views.xml",
        "views/gantt_task_views.xml",
        "views/gantt_views.xml",
        "views/menus.xml",
        "wizards/snapshot_baseline_views.xml",
        "wizards/import_msproject_xml_views.xml",
        "wizards/export_msproject_xml_views.xml",
    ],
    "demo": [
        "data/gantt_demo_data.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "mierp_gantt/static/src/components/gantt_view/gantt_view.scss",
            "mierp_gantt/static/src/theme/mi_erp.scss",
            "mierp_gantt/static/src/components/gantt_view/gantt_view.js",
            "mierp_gantt/static/src/components/gantt_view/gantt_view.xml",
            "mierp_gantt/static/src/widgets/task_editor_popup.js",
            "mierp_gantt/static/src/widgets/task_editor_popup.xml",
            "mierp_gantt/static/src/widgets/settings_dropdown.js",
            "mierp_gantt/static/src/widgets/settings_dropdown.xml",
            "mierp_gantt/static/src/widgets/baseline_overlay.js",
            "mierp_gantt/static/src/widgets/resource_histogram.js",
            "mierp_gantt/static/src/widgets/critical_path_render.js",
            "mierp_gantt/static/src/widgets/ifc_bridge.js",
            "mierp_gantt/static/src/core/topo_sort.js",
            "mierp_gantt/static/src/core/working_days.js",
            "mierp_gantt/static/src/core/cpm.js",
            "mierp_gantt/static/src/core/auto_scheduler.js",
            "mierp_gantt/static/src/core/mspdi_export.js",
        ],
    },
    "images": [
        "static/description/thumbnail.png",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
