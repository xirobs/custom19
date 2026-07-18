# -*- coding: utf-8 -*-
from odoo import _, fields, models


class GanttSnapshotBaseline(models.TransientModel):
    _name = "mierp.gantt.snapshot.baseline"
    _description = "Snapshot Gantt Baseline"

    project_id = fields.Many2one(
        "mierp.gantt.project", required=True, ondelete="cascade"
    )
    name = fields.Char(required=True, default=lambda self: _("Baseline"))
    note = fields.Text()
    set_as_overlay = fields.Boolean(default=True)

    def action_create(self):
        self.ensure_one()
        Baseline = self.env["mierp.gantt.baseline"]
        Line = self.env["mierp.gantt.baseline.line"]
        baseline = Baseline.create(
            {
                "name": self.name,
                "project_id": self.project_id.id,
                "note": self.note,
            }
        )
        for t in self.project_id.task_ids:
            Line.create(
                {
                    "baseline_id": baseline.id,
                    "task_id": t.id,
                    "date_start": t.date_start,
                    "date_end": t.date_end,
                    "duration_days": t.duration_days,
                    "progress_pct": t.progress_pct,
                }
            )
        if self.set_as_overlay:
            baseline.action_set_overlay()
        return {
            "type": "ir.actions.act_window",
            "res_model": "mierp.gantt.baseline",
            "res_id": baseline.id,
            "view_mode": "form",
            "target": "current",
        }
