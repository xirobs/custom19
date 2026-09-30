# -*- coding: utf-8 -*-
"""Agenda del equipo: tareas asignadas con semáforo (rojo = atrasada, verde = al día)."""

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _

TASK_TYPES = [
    ("call", "Llamada"),
    ("meeting", "Cita"),
    ("collection", "Cobranza"),
    ("renewal", "Renovación"),
    ("signature", "Firma de contrato"),
    ("claim", "Siniestro"),
    ("sale", "Venta / propuesta"),
    ("followup", "Seguimiento"),
    ("other", "Otro"),
]


class InsuranceTask(models.Model):
    _name = "insurance.task"
    _description = "Tarea / agenda del equipo de seguros"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_deadline, priority desc, id"

    name = fields.Char(string="Título", required=True, tracking=True)
    description = fields.Html(string="Descripción")
    task_type = fields.Selection(TASK_TYPES, string="Tipo", default="followup", required=True, tracking=True)
    priority = fields.Selection([("0", "Normal"), ("1", "Alta")], string="Prioridad", default="0")
    date_deadline = fields.Date(string="Fecha", required=True, default=fields.Date.context_today, tracking=True, index=True)
    date_start = fields.Datetime(string="Hora (cita)")
    user_id = fields.Many2one(
        "res.users", string="Asignado a", default=lambda self: self.env.user, tracking=True, index=True,
    )
    partner_id = fields.Many2one("res.partner", string="Cliente / contacto", tracking=True, index=True)
    email = fields.Char(related="partner_id.email", string="Correo", readonly=False)
    phone = fields.Char(related="partner_id.phone", string="Teléfono", readonly=False)
    policy_id = fields.Many2one(
        "insurance.policy",
        string="Póliza",
        domain="['|', ('partner_id', '=?', partner_id), ('insured_partner_id', '=?', partner_id)]",
    )
    claim_id = fields.Many2one("insurance.claim", string="Siniestro")
    lead_id = fields.Many2one("crm.lead", string="Oportunidad")
    calendar_event_id = fields.Many2one("calendar.event", string="Cita en calendario", copy=False)
    state = fields.Selection(
        [("todo", "Pendiente"), ("done", "Realizada"), ("cancelled", "Cancelada")],
        string="Estado",
        default="todo",
        required=True,
        tracking=True,
        index=True,
    )
    date_done = fields.Date(string="Realizada el", copy=False)
    result = fields.Text(string="Resultado")
    status_color = fields.Selection(
        [("green", "Al día"), ("orange", "Vence hoy"), ("red", "Atrasada"), ("done", "Realizada")],
        string="Semáforo",
        compute="_compute_status_color",
        search="_search_status_color",
    )
    color = fields.Integer(compute="_compute_status_color")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)

    @api.depends("state", "date_deadline")
    def _compute_status_color(self):
        today = fields.Date.context_today(self)
        for task in self:
            if task.state in ("done", "cancelled"):
                task.status_color, task.color = "done", 10
            elif task.date_deadline and task.date_deadline < today:
                task.status_color, task.color = "red", 1
            elif task.date_deadline == today:
                task.status_color, task.color = "orange", 2
            else:
                task.status_color, task.color = "green", 10

    def _search_status_color(self, operator, value):
        today = fields.Date.context_today(self)
        mapping = {
            "red": [("state", "=", "todo"), ("date_deadline", "<", today)],
            "orange": [("state", "=", "todo"), ("date_deadline", "=", today)],
            "green": [("state", "=", "todo"), ("date_deadline", ">", today)],
            "done": [("state", "in", ["done", "cancelled"])],
        }
        values = [value] if isinstance(value, str) else list(value or [])
        ids = set()
        for key in values:
            if key in mapping:
                ids.update(self.search(mapping[key]).ids)
        if operator in ("!=", "not in"):
            return [("id", "not in", list(ids))]
        return [("id", "in", list(ids))]

    @api.onchange("policy_id")
    def _onchange_policy_id(self):
        if self.policy_id and not self.partner_id:
            self.partner_id = self.policy_id.partner_id

    def action_done(self):
        self.write({"state": "done", "date_done": fields.Date.context_today(self)})
        return True

    def action_reopen(self):
        self.write({"state": "todo", "date_done": False})
        return True

    def action_send_message(self):
        return self.env["insurance.message.wizard"].action_open(self)

    def action_schedule_meeting(self):
        self.ensure_one()
        if not self.calendar_event_id:
            start = self.date_start or fields.Datetime.to_datetime(self.date_deadline) + relativedelta(hours=10)
            self.calendar_event_id = self.env["calendar.event"].create({
                "name": self.name,
                "start": start,
                "stop": start + relativedelta(hours=1),
                "user_id": self.user_id.id,
                "partner_ids": [(6, 0, (self.partner_id | self.user_id.partner_id).ids)],
                "description": self.description or "",
            })
        return {
            "type": "ir.actions.act_window",
            "name": _("Cita"),
            "res_model": "calendar.event",
            "res_id": self.calendar_event_id.id,
            "view_mode": "form",
        }

    @api.model_create_multi
    def create(self, vals_list):
        tasks = super().create(vals_list)
        for task in tasks.filtered(lambda t: t.user_id and t.user_id != self.env.user):
            task.message_subscribe(partner_ids=task.user_id.partner_id.ids)
            task.message_post(
                body=_("Se te asignó la tarea «%(name)s» para el %(date)s.") % {
                    "name": task.name, "date": task.date_deadline,
                },
                partner_ids=task.user_id.partner_id.ids,
                subtype_xmlid="mail.mt_comment",
            )
        return tasks
