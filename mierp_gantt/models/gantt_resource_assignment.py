# -*- coding: utf-8 -*-
from odoo import api, fields, models


class GanttResourceAssignment(models.Model):
    """Link between a Gantt task and a resource (labour, material, equipment).

    Resources are stored as plain string names so the module can be reused
    without imposing a particular HR / inventory model. Bridge addons may
    extend the model and add a Many2one to their own resource record.
    """

    _name = "mierp.gantt.resource.assignment"
    _description = "Gantt Resource Assignment"
    _order = "task_id, sequence"

    sequence = fields.Integer(default=10)
    task_id = fields.Many2one(
        "mierp.gantt.task", required=True, ondelete="cascade", index=True
    )
    resource_name = fields.Char(required=True)
    resource_kind = fields.Selection(
        [
            ("labour", "Labour"),
            ("material", "Material"),
            ("equipment", "Equipment"),
            ("other", "Other"),
        ],
        default="labour",
        required=True,
    )
    units = fields.Float(default=1.0, help="Number of resource units assigned.")
    allocation_pct = fields.Float(
        default=100.0,
        help="Percentage of the resource's working time consumed by this task.",
    )
    cost_per_unit = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )

    @api.model
    def to_histogram_payload(self, project_id):
        """Return per-day usage of each resource across a project, ready
        for ECharts to render a stacked bar chart."""
        self.env.cr.execute(
            """
            SELECT a.resource_name,
                   a.resource_kind,
                   a.units * COALESCE(a.allocation_pct, 100.0) / 100.0 AS effective,
                   t.date_start, t.date_end
            FROM mierp_gantt_resource_assignment a
            JOIN mierp_gantt_task t ON t.id = a.task_id
            WHERE t.project_id = %s
            """,
            (project_id,),
        )
        rows = self.env.cr.dictfetchall()
        return rows
