# -*- coding: utf-8 -*-
"""Cadena automática de actividades CreArt en oportunidades CRM."""

from datetime import timedelta

from odoo import api, fields, models, _


# Etapas (sinónimos incluidos)
STAGE_COTIZACION_ENVIADA = {
    "Propuesta enviada",
    "Cotización enviada",
    "Conceptualización / Propuesta Enviada",
}
STAGE_NEGOCIACION_SEGUIMIENTO = {
    "Negociación / Seguimiento",
}

# Siguiente actividad: (xmlid del tipo, días desde completar la anterior)
CREART_ACTIVITY_CHAIN = {
    "crm_extended_3D.creart_activity_confirm_day1": (
        "crm_extended_3D.creart_activity_followup_3_7_15",
        3,
    ),
    "crm_extended_3D.creart_activity_followup_3_7_15": (
        "crm_extended_3D.creart_activity_call_day10",
        7,
    ),
    "crm_extended_3D.creart_activity_call_day10": (
        "crm_extended_3D.creart_activity_close_cycle_day30",
        20,
    ),
}


class CrmLeadActivityAutomation(models.Model):
    _inherit = "crm.lead"

    creart_negotiation_since = fields.Date(
        string="En negociación desde",
        copy=False,
        help="Fecha de entrada a Negociación / Seguimiento (control de inactividad).",
    )
    creart_stale_review_notified = fields.Boolean(
        string="Alerta inactividad enviada",
        copy=False,
    )

    def _creart_activity_type_from_xmlid(self, xmlid):
        return self.env.ref(xmlid, raise_if_not_found=False)

    def _creart_schedule_activity(self, activity_xmlid, date_deadline, force=False):
        """Programa una actividad si no existe otra abierta del mismo tipo."""
        self.ensure_one()
        activity_type = self._creart_activity_type_from_xmlid(activity_xmlid)
        if not activity_type:
            return self.env["mail.activity"]
        domain = [
            ("res_model", "=", "crm.lead"),
            ("res_id", "=", self.id),
            ("activity_type_id", "=", activity_type.id),
            ("active", "=", True),
        ]
        if not force and self.env["mail.activity"].search_count(domain):
            return self.env["mail.activity"]
        return self.env["mail.activity"].create({
            "res_model_id": self.env["ir.model"]._get("crm.lead").id,
            "res_id": self.id,
            "activity_type_id": activity_type.id,
            "date_deadline": date_deadline,
            "summary": activity_type.summary or activity_type.name,
            "user_id": self.user_id.id or self.env.uid,
            "note": activity_type.default_note,
        })

    def _creart_on_cotizacion_enviada(self):
        """Al enviar cotización: Confirmación día 1 vence hoy."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        self._creart_schedule_activity(
            "crm_extended_3D.creart_activity_confirm_day1",
            today,
        )

    def _creart_chain_next_activity(self, completed_type_xmlid):
        """Programa la siguiente actividad de la secuencia CreArt."""
        self.ensure_one()
        chain = CREART_ACTIVITY_CHAIN.get(completed_type_xmlid)
        if not chain:
            return self.env["mail.activity"]
        next_xmlid, delay_days = chain
        deadline = fields.Date.context_today(self) + timedelta(days=delay_days)
        return self._creart_schedule_activity(next_xmlid, deadline)

    @api.model
    def _creart_check_stale_negotiation_leads(self):
        """Notifica a Dirección Comercial leads >30 días en negociación sin actividad."""
        today = fields.Date.context_today(self)
        threshold = today - timedelta(days=30)
        Activity = self.env["mail.activity"]
        director_group = self.env.ref(
            "crm_extended_3D.group_creart_commercial_director",
            raise_if_not_found=False,
        )
        directors = director_group.users if director_group else self.env["res.users"]

        stale_leads = self.search([
            ("type", "=", "opportunity"),
            ("active", "=", True),
            ("stage_id.name", "in", list(STAGE_NEGOCIACION_SEGUIMIENTO)),
            ("creart_negotiation_since", "!=", False),
            ("creart_negotiation_since", "<=", threshold),
            ("creart_stale_review_notified", "=", False),
        ])

        for lead in stale_leads:
            recent_activity = Activity.with_context(active_test=False).search([
                ("res_model", "=", "crm.lead"),
                ("res_id", "=", lead.id),
                "|",
                ("date_deadline", ">=", threshold),
                ("date_done", ">=", fields.Datetime.now() - timedelta(days=30)),
            ], limit=1)
            if recent_activity:
                continue

            body = _(
                "La oportunidad <b>%(name)s</b> lleva más de 30 días en "
                "<i>Negociación / Seguimiento</i> sin actividades recientes. "
                "Decida si se marca como <b>perdida</b> o se <b>reactiva</b> el seguimiento."
            ) % {"name": lead.display_name}
            partner_ids = directors.mapped("partner_id").ids
            lead.message_post(
                body=body,
                message_type="comment",
                subtype_xmlid="mail.mt_note",
                partner_ids=partner_ids,
            )
            todo_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
            for user in directors:
                self.env["mail.activity"].create({
                    "res_model_id": self.env["ir.model"]._get("crm.lead").id,
                    "res_id": lead.id,
                    "activity_type_id": todo_type.id if todo_type else lead._creart_activity_type_from_xmlid(
                        "crm_extended_3D.creart_activity_close_cycle_day30"
                    ).id,
                    "date_deadline": today,
                    "summary": _("Revisar inactividad: perdida o reactivación"),
                    "user_id": user.id,
                    "note": body,
                })
            lead.creart_stale_review_notified = True


class MailActivityCreart(models.Model):
    _inherit = "mail.activity"

    def _creart_completed_type_xmlid(self, activity):
        """Resuelve xmlid del tipo de actividad completada."""
        data = self.env["ir.model.data"].search([
            ("model", "=", "mail.activity.type"),
            ("res_id", "=", activity.activity_type_id.id),
        ], limit=1)
        return f"{data.module}.{data.name}" if data else None

    def _action_done(self, feedback=False, attachment_ids=None):
        to_chain = []
        for activity in self.filtered(lambda a: a.res_model == "crm.lead" and a.active):
            xmlid = self._creart_completed_type_xmlid(activity)
            if xmlid and xmlid in CREART_ACTIVITY_CHAIN:
                to_chain.append((activity.res_id, xmlid))

        messages, next_activities = super()._action_done(
            feedback=feedback, attachment_ids=attachment_ids
        )

        if self.env.context.get("creart_skip_activity_chain"):
            return messages, next_activities

        Lead = self.env["crm.lead"]
        for lead_id, xmlid in to_chain:
            lead = Lead.browse(lead_id).exists()
            if lead:
                lead._creart_chain_next_activity(xmlid)

        return messages, next_activities
