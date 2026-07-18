# -*- coding: utf-8 -*-
from collections import defaultdict, deque

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class GanttDependency(models.Model):
    """Dependency link between two tasks.

    Supports the four canonical types (FS, SS, FF, SF) and lag/lead in
    working days (negative ``lag_days`` is a lead).
    """

    _name = "mierp.gantt.dependency"
    _description = "Gantt Task Dependency"
    _order = "predecessor_task_id, successor_task_id"

    predecessor_task_id = fields.Many2one(
        "mierp.gantt.task",
        required=True,
        ondelete="cascade",
        index=True,
    )
    successor_task_id = fields.Many2one(
        "mierp.gantt.task",
        required=True,
        ondelete="cascade",
        index=True,
    )
    project_id = fields.Many2one(
        related="predecessor_task_id.project_id", store=True, index=True
    )

    type = fields.Selection(
        [
            ("FS", "Finish-to-Start"),
            ("SS", "Start-to-Start"),
            ("FF", "Finish-to-Finish"),
            ("SF", "Start-to-Finish"),
        ],
        default="FS",
        required=True,
    )
    lag_days = fields.Float(
        default=0.0,
        help="Lag in working days (positive = wait, negative = lead).",
    )

    _no_self_link = models.Constraint(
        "CHECK (predecessor_task_id != successor_task_id)",
        "A task cannot depend on itself.",
    )
    _unique_link = models.Constraint(
        "UNIQUE (predecessor_task_id, successor_task_id, type)",
        "This dependency already exists.",
    )

    @api.constrains("predecessor_task_id", "successor_task_id")
    def _check_same_project(self):
        for rec in self:
            if rec.predecessor_task_id.project_id != rec.successor_task_id.project_id:
                raise ValidationError(
                    _("Both ends of a dependency must belong to the same project.")
                )

    @api.constrains("predecessor_task_id", "successor_task_id")
    def _check_no_cycle(self):
        """Reject any link that would close a cycle.

        We do a BFS from the new successor and bail if we ever reach the
        predecessor. Cheap because per-project graphs are small.
        """
        for rec in self:
            project = rec.predecessor_task_id.project_id
            edges = self.search([("project_id", "=", project.id)])
            adj = defaultdict(list)
            for e in edges:
                adj[e.predecessor_task_id.id].append(e.successor_task_id.id)
            target = rec.predecessor_task_id.id
            seen = set()
            queue = deque([rec.successor_task_id.id])
            while queue:
                node = queue.popleft()
                if node == target:
                    raise ValidationError(
                        _("This link would close a cycle in the dependency graph.")
                    )
                if node in seen:
                    continue
                seen.add(node)
                queue.extend(adj.get(node, []))

    def to_payload(self):
        self.ensure_one()
        return {
            "id": self.id,
            "predecessor": self.predecessor_task_id.id,
            "successor": self.successor_task_id.id,
            "type": self.type,
            "lag_days": self.lag_days,
        }
