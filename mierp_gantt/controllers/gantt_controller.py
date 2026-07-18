# -*- coding: utf-8 -*-
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class GanttController(http.Controller):
    """JSON-2 endpoints feeding the OWL Gantt component.

    The endpoints are intentionally narrow and side-effect-free except for
    explicit mutation calls. The OWL component does ``rpc("/mierp/gantt/data",
    {project_id})`` once on mount and then performs targeted mutations via
    ``call_kw``.
    """

    @http.route(
        "/mierp/gantt/data", type="jsonrpc", auth="user", methods=["POST"]
    )
    def gantt_data(self, project_id=None, **kw):
        if not project_id:
            return {"error": "project_id is required"}
        project = request.env["mierp.gantt.project"].browse(int(project_id))
        if not project.exists():
            return {"error": "project not found"}
        project.check_access("read")

        tasks = project.task_ids.sorted(key=lambda r: (r.sequence, r.id))
        deps = request.env["mierp.gantt.dependency"].search(
            [("project_id", "=", project.id)]
        )
        baseline = (
            project.baseline_ids.filtered(lambda b: b.is_active_overlay)[:1]
            or request.env["mierp.gantt.baseline"]
        )

        payload = {
            "project": {
                "id": project.id,
                "name": project.name,
                "code": project.code or "",
                "date_start": project.date_start.isoformat() if project.date_start else None,
                "date_end": project.date_end.isoformat() if project.date_end else None,
                "state": project.state,
            },
            "calendar": project.calendar_id.to_payload() if project.calendar_id else None,
            "tasks": [t.to_payload() for t in tasks],
            "dependencies": [d.to_payload() for d in deps],
            "baseline": (
                {
                    "id": baseline.id,
                    "name": baseline.name,
                    "snapshot_date": baseline.snapshot_date.isoformat()
                    if baseline.snapshot_date else None,
                    "lines": [l.to_payload() for l in baseline.line_ids],
                }
                if baseline else None
            ),
        }
        return payload

    @http.route(
        "/mierp/gantt/projects", type="jsonrpc", auth="user", methods=["POST"]
    )
    def gantt_projects(self, **kw):
        """Return a flat list of available projects for the selector."""
        recs = request.env["mierp.gantt.project"].search([])
        return [
            {
                "id": p.id,
                "name": p.name,
                "code": p.code or "",
                "date_start": p.date_start.isoformat() if p.date_start else None,
                "date_end": p.date_end.isoformat() if p.date_end else None,
                "task_count": p.task_count,
            }
            for p in recs
        ]

    @http.route(
        "/mierp/gantt/histogram", type="jsonrpc", auth="user", methods=["POST"]
    )
    def gantt_histogram(self, project_id=None, **kw):
        if not project_id:
            return {"error": "project_id is required"}
        project = request.env["mierp.gantt.project"].browse(int(project_id))
        if not project.exists():
            return {"error": "project not found"}
        project.check_access("read")
        rows = request.env["mierp.gantt.resource.assignment"].sudo().to_histogram_payload(project.id)
        # Serialise dates as ISO for the frontend.
        for r in rows:
            if r.get("date_start"):
                r["date_start"] = r["date_start"].isoformat()
            if r.get("date_end"):
                r["date_end"] = r["date_end"].isoformat()
        return rows

    @http.route(
        "/mierp/gantt/save_settings", type="jsonrpc", auth="user", methods=["POST"]
    )
    def save_settings(self, settings=None, **kw):
        if settings is None:
            return {"ok": False, "error": "settings required"}
        request.env["res.users"].browse(request.env.uid).sudo().write({
            "gantt_settings_json": json.dumps(settings),
        }) if hasattr(request.env["res.users"], "gantt_settings_json") else None
        # Fallback: persist on ir.config_parameter scoped to the user.
        ICP = request.env["ir.config_parameter"].sudo()
        ICP.set_param(f"mierp_gantt.user.{request.env.uid}.settings", json.dumps(settings))
        return {"ok": True}

    @http.route(
        "/mierp/gantt/load_settings", type="jsonrpc", auth="user", methods=["POST"]
    )
    def load_settings(self, **kw):
        ICP = request.env["ir.config_parameter"].sudo()
        raw = ICP.get_param(f"mierp_gantt.user.{request.env.uid}.settings")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {}
