# -*- coding: utf-8 -*-
"""Puente CreArt 3DP ↔ MI ERP Gantt (planificación visual)."""

from datetime import timedelta

from odoo import api, fields, models, _


CREART_GANTT_QUEUE_CODE = "CREART_QUEUE"
CREART_PRIORITY_COLORS = {
    "3": "#d9534f",
    "2": "#f0ad4e",
    "1": "#5bc0de",
    "0": "#6c757d",
}


class GanttTaskCreart(models.Model):
    _inherit = "mierp.gantt.task"

    creart_lead_id = fields.Many2one("crm.lead", string="Oportunidad CreArt", index=True, ondelete="cascade")
    creart_project_task_id = fields.Many2one(
        "project.task", string="Tarea Odoo", index=True, ondelete="set null"
    )


class ProjectTaskGantt(models.Model):
    _inherit = "project.task"

    gantt_task_id = fields.Many2one("mierp.gantt.task", copy=False, ondelete="set null")


class CrmLeadGantt(models.Model):
    _inherit = "crm.lead"

    gantt_project_id = fields.Many2one(
        "mierp.gantt.project",
        string="Proyecto Gantt",
        copy=False,
        ondelete="set null",
    )

    def _creart_gantt_calendar(self):
        return self.env.ref(
            "crm_extended_3D.creart_gantt_calendar",
            raise_if_not_found=False,
        ) or self.env["mierp.gantt.calendar"].search([], limit=1)

    def _creart_dt_to_date(self, dt):
        if not dt:
            return fields.Date.context_today(self)
        tz = self._get_planning_timezone()
        return fields.Datetime.context_timestamp(
            self.with_context(tz=tz), dt
        ).date()

    def _creart_gantt_dates(self, start_dt, end_dt):
        start = self._creart_dt_to_date(start_dt)
        end = self._creart_dt_to_date(end_dt) if end_dt else start
        if end < start:
            end = start
        return start, end

    def _creart_gantt_state(self, task):
        if task.state in ("1_done",):
            return "done", 100.0
        if task.state in ("01_in_progress", "02_changes_requested", "03_approved"):
            return "in_progress", max(getattr(task, "progress", 0.0) or 0.0, 10.0)
        if task.state in ("1_canceled",):
            return "blocked", getattr(task, "progress", 0.0) or 0.0
        return "planned", getattr(task, "progress", 0.0) or 0.0

    def _creart_gantt_color(self, lead):
        return CREART_PRIORITY_COLORS.get(lead.priority or "1", "#5bc0de")

    def _creart_get_or_create_lead_gantt_project(self):
        self.ensure_one()
        GanttProject = self.env["mierp.gantt.project"]
        project = self.gantt_project_id
        if not project:
            project = GanttProject.search([
                ("res_model", "=", "crm.lead"),
                ("res_id", "=", self.id),
            ], limit=1)
        label = self.lead_sequence or self.name
        vals = {
            "name": _("CreArt Gantt — %s") % label,
            "code": label,
            "calendar_id": self._creart_gantt_calendar().id,
            "date_start": self._creart_dt_to_date(self.planning_date_start) or fields.Date.context_today(self),
            "date_end": self._creart_dt_to_date(self.planning_date_end),
            "state": "active",
            "res_model": "crm.lead",
            "res_id": self.id,
        }
        if project:
            project.write({k: v for k, v in vals.items() if v})
        else:
            project = GanttProject.create(vals)
            self.gantt_project_id = project.id
        return project

    @api.model
    def _creart_get_queue_gantt_project(self):
        GanttProject = self.env["mierp.gantt.project"]
        project = GanttProject.search([("code", "=", CREART_GANTT_QUEUE_CODE)], limit=1)
        if project:
            return project
        return GanttProject.create({
            "name": _("CreArt 3DP — Cola de producción"),
            "code": CREART_GANTT_QUEUE_CODE,
            "calendar_id": self.env["crm.lead"]._creart_gantt_calendar().id,
            "date_start": fields.Date.context_today(self),
            "state": "active",
            "description": _(
                "Vista Gantt consolidada de todos los trabajos en cola CreArt."
            ),
        })

    def _creart_upsert_gantt_task(self, gantt_project, project_task, lead, sequence, name_prefix=""):
        GanttTask = self.env["mierp.gantt.task"]
        start, end = self._creart_gantt_dates(
            project_task.planned_date_start,
            project_task.planned_date_end,
        )
        state, progress = self._creart_gantt_state(project_task)
        display_name = f"{name_prefix}{project_task.name}" if name_prefix else project_task.name
        vals = {
            "name": display_name,
            "project_id": gantt_project.id,
            "sequence": sequence,
            "date_start": start,
            "date_end": end,
            "progress_pct": progress,
            "state": state,
            "color_hex": self._creart_gantt_color(lead),
            "creart_lead_id": lead.id,
            "creart_project_task_id": project_task.id,
        }
        gantt_task = GanttTask.search([
            ("creart_project_task_id", "=", project_task.id),
            ("project_id", "=", gantt_project.id),
        ], limit=1)
        if not gantt_task and project_task.gantt_task_id and project_task.gantt_task_id.project_id == gantt_project:
            gantt_task = project_task.gantt_task_id
        if gantt_task:
            gantt_task.write(vals)
        else:
            gantt_task = GanttTask.create(vals)
        if lead.gantt_project_id and gantt_project == lead.gantt_project_id:
            project_task.gantt_task_id = gantt_task.id
        self._creart_sync_gantt_resources(gantt_task, project_task)
        return gantt_task

    def _creart_sync_gantt_resources(self, gantt_task, project_task):
        Assignment = self.env["mierp.gantt.resource.assignment"]
        Assignment.search([("task_id", "=", gantt_task.id)]).unlink()
        for idx, user in enumerate(project_task.user_ids, start=1):
            Assignment.create({
                "task_id": gantt_task.id,
                "sequence": idx * 10,
                "resource_name": user.name,
                "resource_kind": "labour",
                "units": 1.0,
            })

    def _creart_chain_fs_dependencies(self, gantt_tasks):
        Dep = self.env["mierp.gantt.dependency"]
        if not gantt_tasks:
            return
        project = gantt_tasks[0].project_id
        Dep.search([
            ("project_id", "=", project.id),
            ("successor_task_id", "in", gantt_tasks.ids),
        ]).unlink()
        prev = None
        for task in gantt_tasks:
            if prev:
                Dep.create({
                    "predecessor_task_id": prev.id,
                    "successor_task_id": task.id,
                    "type": "FS",
                    "lag_days": 0.0,
                })
            prev = task

    def _sync_lead_gantt_project(self):
        self.ensure_one()
        if not self.planning_task_ids:
            return self.env["mierp.gantt.project"]
        project = self._creart_get_or_create_lead_gantt_project()
        gantt_tasks = self.env["mierp.gantt.task"]
        seq = 10
        for pt in self.planning_task_ids.sorted(
            key=lambda t: (t.planned_date_start or fields.Datetime.now(), t.id)
        ):
            gantt_tasks |= self._creart_upsert_gantt_task(project, pt, self, seq)
            seq += 10

        stale = self.env["mierp.gantt.task"].search([
            ("project_id", "=", project.id),
            ("creart_lead_id", "=", self.id),
            ("id", "not in", gantt_tasks.ids),
        ])
        stale.unlink()

        self._creart_chain_fs_dependencies(gantt_tasks)
        if gantt_tasks:
            project.write({
                "date_start": min(gantt_tasks.mapped("date_start")),
                "date_end": max(gantt_tasks.mapped("date_end")),
            })
        return project

    @api.model
    def _sync_creart_queue_gantt(self):
        queue_project = self._creart_get_queue_gantt_project()
        ordered_leads = self._get_ordered_queue_leads().filtered("planning_task_ids")
        gantt_tasks = self.env["mierp.gantt.task"]
        seq = 10
        for lead in ordered_leads:
            lead._sync_lead_gantt_project()
            prefix = f"[{lead.lead_sequence or lead.name}] "
            for pt in lead.planning_task_ids.sorted(
                key=lambda t: (t.planned_date_start or fields.Datetime.now(), t.id)
            ):
                gantt_tasks |= lead._creart_upsert_gantt_task(
                    queue_project, pt, lead, seq, name_prefix=prefix
                )
                seq += 10

        stale = self.env["mierp.gantt.task"].search([
            ("project_id", "=", queue_project.id),
            ("creart_lead_id", "!=", False),
            ("id", "not in", gantt_tasks.ids),
        ])
        stale.unlink()
        self._creart_chain_fs_dependencies(gantt_tasks)
        if gantt_tasks:
            queue_project.write({
                "date_start": min(gantt_tasks.mapped("date_start")),
                "date_end": max(gantt_tasks.mapped("date_end")),
                "state": "active",
            })
        return queue_project

    def action_open_gantt(self):
        self.ensure_one()
        if not self.planning_task_ids:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Sin planificación"),
                    "message": _("Genere primero el plan CreArt para ver el Gantt."),
                    "type": "warning",
                    "sticky": False,
                },
            }
        project = self._sync_lead_gantt_project()
        return project.action_open_gantt()

    @api.model
    def action_open_creart_planning_gantt(self):
        """Abre el Gantt de toda la cola de producción."""
        self._sync_creart_queue_gantt()
        project = self._creart_get_queue_gantt_project()
        return project.action_open_gantt()


class CrmLeadPlanningGanttHook(models.Model):
    _inherit = "crm.lead"

    def _sync_planning_tasks(self, resync_following=False):
        res = super()._sync_planning_tasks(resync_following=resync_following)
        for lead in self:
            if lead.planning_task_ids:
                lead._sync_lead_gantt_project()
        self.env["crm.lead"]._sync_creart_queue_gantt()
        return res

    def action_open_planning(self):
        self.ensure_one()
        if self.planning_task_ids:
            return self.action_open_gantt()
        return super().action_open_planning()

    @api.model
    def action_resync_planning_queue(self):
        res = super().action_resync_planning_queue()
        self._sync_creart_queue_gantt()
        return self.action_open_creart_planning_gantt()

    def action_create_planning_project(self):
        res = super().action_create_planning_project()
        if isinstance(res, dict) and res.get("type") == "ir.actions.act_window":
            return self.action_open_gantt()
        return res
