"""Planificación CreArt 3DP: proyecto, cola de trabajos y horario laboral."""

from odoo import api, fields, models, _  # type: ignore

from .creart_planning_schedule import CreartWorkScheduler


class ProjectTask(models.Model):
    _inherit = "project.task"

    lead_id = fields.Many2one("crm.lead", string="Oportunidad CreArt", index=True, ondelete="set null")
    lead_step_id = fields.Many2one("crm.print.process.step", string="Paso impresión", ondelete="set null")
    planned_date_start = fields.Datetime(string="Inicio planificado")
    planned_date_end = fields.Datetime(string="Fin planificado")


class CrmLeadPlanning(models.Model):
    _inherit = "crm.lead"

    project_id = fields.Many2one("project.project", string="Proyecto planificación", copy=False)
    planning_task_ids = fields.One2many("project.task", "lead_id", string="Tareas de planificación")
    planning_task_count = fields.Integer(compute="_compute_planning_stats")
    planning_progress = fields.Float(string="Avance planificación (%)", compute="_compute_planning_stats")
    planning_date_start = fields.Datetime(string="Inicio producción")
    planning_date_end = fields.Datetime(string="Fin producción")
    planning_queue_note = fields.Char(
        string="Notas de cola",
        compute="_compute_planning_queue_note",
        help="Resumen de trabajos previos que empujan el inicio del plan.",
    )

    @api.depends("planning_task_ids.state")
    def _compute_planning_stats(self):
        for lead in self:
            tasks = lead.planning_task_ids
            lead.planning_task_count = len(tasks)
            if not tasks:
                lead.planning_progress = 0.0
                continue
            done = len(tasks.filtered(lambda t: t.state in ("1_done", "1_canceled")))
            lead.planning_progress = (done / len(tasks)) * 100.0

    @api.depends("priority", "sequence", "lead_sequence", "planning_task_ids.planned_date_end")
    def _compute_planning_queue_note(self):
        for lead in self:
            ahead = lead._get_leads_ahead_in_queue()
            if not ahead:
                lead.planning_queue_note = _("Sin trabajos previos en cola.")
                continue
            names = ", ".join(ahead.mapped("lead_sequence")[:5])
            extra = len(ahead) - min(len(ahead), 5)
            if extra > 0:
                names = f"{names} (+{extra})"
            lead.planning_queue_note = _("Después de: %s") % names

    def _get_planning_timezone(self):
        tz = self.env.user.tz or self.env.company.resource_calendar_id.tz
        return tz or "America/Mexico_City"

    def _get_work_scheduler(self):
        return CreartWorkScheduler(self._get_planning_timezone())

    def _get_ordered_queue_leads(self):
        """Oportunidades CreArt en orden de cola (priority desc, sequence asc, id desc)."""
        return self.search(
            [
                ("lead_sequence", "!=", False),
                ("active", "=", True),
                ("type", "=", "opportunity"),
            ],
            order="priority desc, sequence asc, id desc",
        )

    def _get_leads_ahead_in_queue(self):
        """Trabajos con planificación que van antes que este en la cola."""
        self.ensure_one()
        ordered = self._get_ordered_queue_leads()
        ahead = self.env["crm.lead"]
        for lead in ordered:
            if lead.id == self.id:
                break
            if lead.project_id or lead.planning_task_ids:
                ahead |= lead
        return ahead

    def _get_leads_following_in_queue(self):
        self.ensure_one()
        ordered = self._get_ordered_queue_leads()
        following = self.env["crm.lead"]
        found = False
        for lead in ordered:
            if lead.id == self.id:
                found = True
                continue
            if found and (lead.project_id or lead.planning_task_ids):
                following |= lead
        return following

    def _get_queue_blocked_start(self):
        """
        Inicio mínimo según tareas pendientes de trabajos anteriores en cola.
        """
        self.ensure_one()
        scheduler = self._get_work_scheduler()
        ahead = self._get_leads_ahead_in_queue()
        if not ahead:
            return False

        Task = self.env["project.task"]
        pending_ends = Task.search(
            [
                ("lead_id", "in", ahead.ids),
                ("state", "not in", ("1_done", "1_canceled")),
                ("planned_date_end", "!=", False),
            ]
        ).mapped("planned_date_end")

        for lead in ahead:
            if lead.planning_date_end:
                pending_ends.append(lead.planning_date_end)

        if not pending_ends:
            return False
        return scheduler.advance_to_work_time(max(pending_ends))

    def _get_planning_start_datetime(self):
        """Inicio efectivo: el mayor entre deseo del usuario y fin de cola previa."""
        self.ensure_one()
        scheduler = self._get_work_scheduler()
        desired = self.planning_date_start or fields.Datetime.now()
        desired = scheduler.advance_to_work_time(desired)
        blocked = self._get_queue_blocked_start()
        if blocked and blocked > desired:
            return blocked
        return desired

    def action_create_planning_project(self):
        self.ensure_one()
        if not self.project_id:
            self.project_id = self.env["project.project"].create({
                "name": f"CreArt 3DP - {self.lead_sequence or self.name}",
                "partner_id": self.partner_id.id,
                "date_start": self.order_date or fields.Date.today(),
                "date": self.delivery_date,
            })
        if not self.print_process_step_ids:
            self.action_create_default_process_steps()
        if not self.planning_date_start:
            self.planning_date_start = self._get_planning_start_datetime()
        self._sync_planning_tasks(resync_following=True)
        return self.action_open_planning()

    def action_resync_planning_project(self):
        """Regenera tareas y fechas del plan (mismo proyecto)."""
        self.ensure_one()
        if not self.project_id:
            return self.action_create_planning_project()
        if not self.print_process_step_ids:
            self.action_create_default_process_steps()
        self._sync_planning_tasks(resync_following=True)
        return self.action_open_planning()

    @api.model
    def action_resync_planning_queue(self):
        """Replanifica toda la cola CreArt respetando horario y trabajos pendientes."""
        ordered = self._get_ordered_queue_leads()
        for lead in ordered:
            if not lead.print_process_step_ids:
                lead.action_create_default_process_steps()
            if not lead.project_id:
                lead.project_id = self.env["project.project"].create({
                    "name": f"CreArt 3DP - {lead.lead_sequence or lead.name}",
                    "partner_id": lead.partner_id.id,
                    "date_start": lead.order_date or fields.Date.today(),
                    "date": lead.delivery_date,
                })
            lead._sync_planning_tasks(resync_following=False)
        return {
            "type": "ir.actions.act_window",
            "name": _("Planificación CreArt 3DP"),
            "res_model": "project.task",
            "view_mode": "calendar,graph,list,form,kanban,pivot",
            "domain": [("lead_id", "!=", False)],
        }

    def _sync_planning_tasks(self, resync_following=False):
        self.ensure_one()
        if not self.project_id:
            return

        scheduler = self._get_work_scheduler()
        Task = self.env["project.task"]
        cursor = self._get_planning_start_datetime()

        for step in self.print_process_step_ids.sorted("sequence"):
            duration = self._get_step_duration_hours(step)
            start_dt, end_dt = scheduler.schedule_block(cursor, duration)
            vals = {
                "name": step.name,
                "project_id": self.project_id.id,
                "lead_id": self.id,
                "lead_step_id": step.id,
                "planned_date_start": start_dt,
                "planned_date_end": end_dt,
                "date_deadline": end_dt,
                "allocated_hours": duration,
                "user_ids": [(6, 0, [step.user_id.id])] if step.user_id else False,
            }
            task = Task.search([("lead_step_id", "=", step.id)], limit=1)
            if task:
                task.write(vals)
            else:
                Task.create(vals)
            cursor = end_dt

        self.planning_date_end = cursor
        starts = [d for d in self.planning_task_ids.mapped("planned_date_start") if d]
        if starts:
            self.planning_date_start = min(starts)

        if resync_following:
            for following in self._get_leads_following_in_queue():
                following._sync_planning_tasks(resync_following=False)

    def _get_step_duration_hours(self, step):
        self.ensure_one()
        name = (step.name or "").lower()
        print_hours = (
            (self.print_hours or 0)
            + (self.print_time_minutes or 0) / 60.0
            + (self.print_time_seconds or 0) / 3600.0
            + (self.dead_time_hours or 0)
            + (self.dead_time_minutes or 0) / 60.0
            + (self.dead_time_seconds or 0) / 3600.0
        )
        design_hours = (self.mo_concept_hours or 0.0) + (self.mo_design_hours or 0.0)
        post_hours = self.mo_postprocess_hours or 0.0

        if "cola" in name:
            return 0.25
        if "imprim" in name or "impres" in name:
            return max(print_hours, 0.5)
        if "post" in name:
            return max(post_hours, 0.5)
        if "dise" in name or "concept" in name or "ajust" in name:
            # Reserva 24 h: diseño + espera de aprobación del cliente.
            return max(design_hours, 24.0)
        if "calidad" in name or "control" in name:
            return 1.0
        if "entreg" in name or "listo" in name:
            return 0.5
        return 2.0

    def action_open_planning(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Planificación CreArt 3DP"),
            "res_model": "project.task",
            "view_mode": "calendar,graph,list,form,kanban,pivot",
            "domain": [("lead_id", "=", self.id)],
            "context": {
                "default_lead_id": self.id,
                "default_project_id": self.project_id.id if self.project_id else False,
            },
        }

    def action_open_project(self):
        """Abre el proyecto CreArt vinculado a la oportunidad."""
        self.ensure_one()
        if not self.project_id:
            return self.action_create_planning_project()
        return {
            "type": "ir.actions.act_window",
            "name": _("Proyecto CreArt"),
            "res_model": "project.project",
            "view_mode": "form",
            "res_id": self.project_id.id,
            "target": "current",
        }
