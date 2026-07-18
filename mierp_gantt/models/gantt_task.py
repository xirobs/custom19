# -*- coding: utf-8 -*-
from datetime import date, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class GanttTask(models.Model):
    """A task within a Gantt project.

    Tasks form a tree via ``parent_id``. Leaf tasks have explicit dates and
    durations; summary (parent) tasks roll up from their children.
    """

    _name = "mierp.gantt.task"
    _description = "Gantt Task"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "project_id, sequence, date_start, id"
    _parent_store = True
    _parent_name = "parent_id"

    name = fields.Char(required=True, tracking=True, translate=True)
    wbs = fields.Char(
        string="WBS",
        compute="_compute_wbs",
        store=True,
        recursive=True,
        help="Auto-generated work breakdown structure code (1, 1.1, 1.1.1).",
    )
    sequence = fields.Integer(default=10, index=True)

    project_id = fields.Many2one(
        "mierp.gantt.project",
        required=True,
        ondelete="cascade",
        index=True,
        tracking=True,
    )

    parent_id = fields.Many2one(
        "mierp.gantt.task",
        string="Parent",
        ondelete="cascade",
        index=True,
    )
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many("mierp.gantt.task", "parent_id", string="Sub-tasks")
    is_summary = fields.Boolean(compute="_compute_is_summary", store=True)

    date_start = fields.Date(required=True, tracking=True, index=True)
    date_end = fields.Date(required=True, tracking=True, index=True)
    duration_days = fields.Float(
        string="Duration (working days)",
        compute="_compute_duration",
        inverse="_inverse_duration",
        store=True,
    )
    progress_pct = fields.Float(
        string="% Complete",
        default=0.0,
        tracking=True,
    )
    is_milestone = fields.Boolean(default=False, tracking=True)

    state = fields.Selection(
        [
            ("planned", "Planned"),
            ("in_progress", "In Progress"),
            ("done", "Done"),
            ("blocked", "Blocked"),
        ],
        default="planned",
        tracking=True,
        index=True,
    )

    color_hex = fields.Char(
        string="Color",
        help="Optional hex colour for the bar (e.g. #B85620). Leaves the "
        "default MI ERP orange when empty.",
    )
    notes = fields.Html()

    # Predecessors: links where this task is the successor.
    predecessor_ids = fields.One2many(
        "mierp.gantt.dependency", "successor_task_id", string="Predecessors"
    )
    # Successors: links where this task is the predecessor.
    successor_ids = fields.One2many(
        "mierp.gantt.dependency", "predecessor_task_id", string="Successors"
    )

    resource_assignment_ids = fields.One2many(
        "mierp.gantt.resource.assignment", "task_id", string="Resources"
    )
    resource_names = fields.Char(
        compute="_compute_resource_names", store=False
    )

    company_id = fields.Many2one(related="project_id.company_id", store=True)

    _progress_pct_range = models.Constraint(
        "CHECK (progress_pct >= 0 AND progress_pct <= 100)",
        "Progress must be between 0 and 100.",
    )

    @api.depends("date_start", "date_end")
    def _compute_duration(self):
        for rec in self:
            if rec.date_start and rec.date_end:
                # Naive calendar-day span (working-day conversion happens in
                # the JS layer using the project calendar). +1 to be inclusive.
                rec.duration_days = max(
                    1.0, float((rec.date_end - rec.date_start).days + 1)
                )
            else:
                rec.duration_days = 0.0

    def _inverse_duration(self):
        for rec in self:
            if rec.date_start and rec.duration_days:
                rec.date_end = rec.date_start + timedelta(
                    days=max(0, int(rec.duration_days) - 1)
                )

    @api.depends("child_ids")
    def _compute_is_summary(self):
        for rec in self:
            rec.is_summary = bool(rec.child_ids)

    @api.depends("parent_id", "parent_id.wbs", "sequence")
    def _compute_wbs(self):
        # Build WBS by walking up the parent chain. Cheap enough for the
        # typical 1k-tasks scope.
        for rec in self:
            chain = []
            cur = rec
            while cur:
                # Index among siblings, ordered by sequence.
                siblings = (cur.parent_id.child_ids if cur.parent_id else
                            self.search([
                                ("project_id", "=", cur.project_id.id),
                                ("parent_id", "=", False),
                            ]))
                ordered = siblings.sorted(key=lambda r: (r.sequence, r.id))
                idx = 1
                for i, s in enumerate(ordered, start=1):
                    if s.id == cur.id:
                        idx = i
                        break
                chain.insert(0, str(idx))
                cur = cur.parent_id
            rec.wbs = ".".join(chain) if chain else ""

    @api.depends(
        "resource_assignment_ids.resource_name",
        "resource_assignment_ids.units",
    )
    def _compute_resource_names(self):
        for rec in self:
            parts = []
            for a in rec.resource_assignment_ids:
                if a.units and a.units != 1.0:
                    parts.append(f"{a.resource_name} × {a.units:g}")
                else:
                    parts.append(a.resource_name or "")
            rec.resource_names = ", ".join(p for p in parts if p)

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for rec in self:
            if rec.date_start and rec.date_end and rec.date_end < rec.date_start:
                raise ValidationError(_("End date cannot be earlier than start."))

    @api.constrains("parent_id")
    def _check_parent(self):
        if self._has_cycle():
            raise ValidationError(_("A task cannot be its own ancestor."))
        for rec in self:
            if rec.parent_id and rec.parent_id.project_id != rec.project_id:
                raise ValidationError(
                    _("Parent task must belong to the same project.")
                )

    def action_set_in_progress(self):
        self.write({"state": "in_progress"})

    def action_set_done(self):
        self.write({"state": "done", "progress_pct": 100.0})

    def to_payload(self):
        """Compact dict consumed by the OWL Gantt component."""
        self.ensure_one()
        return {
            "id": self.id,
            "name": self.name or "",
            "wbs": self.wbs or "",
            "parent_id": self.parent_id.id or False,
            "is_summary": self.is_summary,
            "date_start": self.date_start.isoformat() if self.date_start else None,
            "date_end": self.date_end.isoformat() if self.date_end else None,
            "duration_days": self.duration_days,
            "progress_pct": self.progress_pct,
            "is_milestone": self.is_milestone,
            "state": self.state,
            "color_hex": self.color_hex or "",
            "resource_names": self.resource_names or "",
            "sequence": self.sequence,
        }
