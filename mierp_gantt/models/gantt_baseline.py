# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class GanttBaseline(models.Model):
    """A frozen snapshot of a project's schedule (planned-vs-actual)."""

    _name = "mierp.gantt.baseline"
    _description = "Gantt Baseline"
    _order = "snapshot_date desc, id desc"

    name = fields.Char(required=True)
    project_id = fields.Many2one(
        "mierp.gantt.project", required=True, ondelete="cascade", index=True
    )
    snapshot_date = fields.Datetime(default=fields.Datetime.now, required=True)
    note = fields.Text()
    line_ids = fields.One2many(
        "mierp.gantt.baseline.line", "baseline_id", string="Lines"
    )
    line_count = fields.Integer(compute="_compute_line_count")
    active = fields.Boolean(default=True)
    is_active_overlay = fields.Boolean(
        string="Active Overlay",
        default=False,
        help="If checked, this baseline is the one displayed as ghost-bars in the Gantt view.",
    )

    @api.depends("line_ids")
    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)

    def action_set_overlay(self):
        for rec in self:
            (
                rec.project_id.baseline_ids - rec
            ).write({"is_active_overlay": False})
            rec.is_active_overlay = True
        return True


class GanttBaselineLine(models.Model):
    _name = "mierp.gantt.baseline.line"
    _description = "Gantt Baseline Line"
    _order = "baseline_id, task_id"

    baseline_id = fields.Many2one(
        "mierp.gantt.baseline", required=True, ondelete="cascade", index=True
    )
    task_id = fields.Many2one(
        "mierp.gantt.task", required=True, ondelete="cascade", index=True
    )
    date_start = fields.Date()
    date_end = fields.Date()
    duration_days = fields.Float()
    progress_pct = fields.Float()

    def to_payload(self):
        self.ensure_one()
        return {
            "task_id": self.task_id.id,
            "date_start": self.date_start.isoformat() if self.date_start else None,
            "date_end": self.date_end.isoformat() if self.date_end else None,
            "duration_days": self.duration_days,
            "progress_pct": self.progress_pct,
        }
