# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class GanttProject(models.Model):
    """A schedulable container of Gantt tasks.

    Generic model — any other addon can link its own domain entity (e.g.
    ``mierp.construction.work``) to a project via a Many2one relation, or
    via the polymorphic ``res_model``/``res_id`` reference for one-off cases.
    """

    _name = "mierp.gantt.project"
    _description = "Gantt Project"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, id desc"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(index=True, tracking=True)
    description = fields.Html()

    date_start = fields.Date(required=True, tracking=True, default=fields.Date.context_today)
    date_end = fields.Date(tracking=True)

    calendar_id = fields.Many2one(
        "mierp.gantt.calendar",
        string="Working Calendar",
        required=True,
        default=lambda self: self._default_calendar(),
    )

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("on_hold", "On Hold"),
            ("done", "Done"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        tracking=True,
        index=True,
    )

    color = fields.Integer(default=0)

    task_ids = fields.One2many("mierp.gantt.task", "project_id", string="Tasks")
    task_count = fields.Integer(compute="_compute_task_count", store=False)

    baseline_ids = fields.One2many("mierp.gantt.baseline", "project_id")
    baseline_count = fields.Integer(compute="_compute_baseline_count", store=False)

    # Polymorphic link back to the consuming model.
    res_model = fields.Char(string="Linked Model")
    res_id = fields.Integer(string="Linked Record ID")

    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True
    )

    @api.model
    def _default_calendar(self):
        return self.env["mierp.gantt.calendar"].search([], limit=1).id or False

    @api.depends("task_ids")
    def _compute_task_count(self):
        for rec in self:
            rec.task_count = len(rec.task_ids)

    @api.depends("baseline_ids")
    def _compute_baseline_count(self):
        for rec in self:
            rec.baseline_count = len(rec.baseline_ids)

    def action_open_gantt(self):
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "mierp_gantt.view",
            "name": _("Gantt — %s") % self.name,
            "params": {"project_id": self.id},
        }

    def action_create_baseline(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Snapshot Baseline"),
            "res_model": "mierp.gantt.snapshot.baseline",
            "view_mode": "form",
            "target": "new",
            "context": {"default_project_id": self.id},
        }
