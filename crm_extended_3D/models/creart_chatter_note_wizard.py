# -*- coding: utf-8 -*-
"""Notas internas estandarizadas en el chatter de oportunidades CreArt."""

from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.misc import formatLang, format_date


NOTE_TYPES = [
    ("first_contact", "Primer contacto"),
    ("quote", "Cotización"),
    ("objection", "Objeción"),
    ("won", "Cierre ganado"),
    ("lost", "Cierre perdido"),
]

URGENCY_SELECTION = [
    ("alta", "Alta"),
    ("media", "Media"),
    ("baja", "Baja"),
]


class CreartCrmChatterNoteWizard(models.TransientModel):
    _name = "creart.crm.chatter.note.wizard"
    _description = "Nota interna estandarizada CreArt"

    lead_id = fields.Many2one("crm.lead", string="Oportunidad", required=True, readonly=True)
    note_type = fields.Selection(NOTE_TYPES, string="Tipo de nota", required=True, default="first_contact")
    preview = fields.Html(string="Vista previa", compute="_compute_preview", sanitize=False)

    # Primer contacto
    channel = fields.Char(string="Canal de contacto")
    need_summary = fields.Text(string="Necesidad (resumen)")
    estimated_budget = fields.Monetary(string="Presupuesto estimado", currency_field="currency_id")
    urgency = fields.Selection(URGENCY_SELECTION, string="Urgencia")

    # Cotización
    quote_amount = fields.Monetary(string="Monto cotizado", currency_field="currency_id")
    quote_date = fields.Date(string="Fecha de envío")
    product_detail = fields.Text(string="Detalle del producto")
    delivery_days = fields.Integer(string="Días de entrega ofrecidos")

    # Objeción
    objection_type = fields.Char(string="Tipo de objeción")
    response_summary = fields.Text(string="Respuesta dada")
    next_step = fields.Char(string="Siguiente paso acordado")

    # Cierre ganado
    sale_amount = fields.Monetary(string="Monto de venta", currency_field="currency_id")
    committed_delivery_date = fields.Date(string="Fecha de entrega comprometida")
    production_notified = fields.Selection(
        [("yes", "Sí"), ("no", "No")],
        string="Notificado a producción",
        default="no",
    )

    # Cierre perdido
    lost_reason_id = fields.Many2one("crm.lost.reason", string="Motivo de pérdida")
    reactivation_date = fields.Date(string="Seguimiento de reactivación")

    currency_id = fields.Many2one(related="lead_id.company_currency", readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        lead = self.env["crm.lead"].browse(self.env.context.get("default_lead_id"))
        if lead:
            res.update(self._defaults_from_lead(lead, res.get("note_type", "first_contact")))
        return res

    @api.model
    def _defaults_from_lead(self, lead, note_type):
        """Precarga campos desde la oportunidad según el tipo de nota."""
        vals = {}
        urgency_map = {"3": "alta", "2": "alta", "1": "media", "0": "baja"}
        urgency = urgency_map.get(lead.priority or "1", "media")
        if lead.urgency_tag_ids:
            tag_name = (lead.urgency_tag_ids[0].name or "").lower()
            if "urgent" in tag_name or "urgente" in tag_name or "alta" in tag_name:
                urgency = "alta"
            elif "baja" in tag_name:
                urgency = "baja"

        channel = lead.source_id.name if lead.source_id else ""
        budget = lead.selected_total_price or lead.expected_revenue or 0.0
        product_detail = lead.name or ""
        if getattr(lead, "units", None):
            product_detail = f"{product_detail} — {lead.units:g} pza(s)".strip(" —")

        delivery_days = 0
        if lead.order_date and lead.delivery_date:
            delivery_days = (lead.delivery_date - lead.order_date).days

        vals.update({
            "channel": channel,
            "need_summary": lead.description or "",
            "estimated_budget": budget,
            "urgency": urgency,
            "quote_amount": budget,
            "quote_date": fields.Date.context_today(self),
            "product_detail": product_detail,
            "delivery_days": delivery_days or 6,
            "sale_amount": budget,
            "committed_delivery_date": lead.delivery_date,
            "reactivation_date": fields.Date.context_today(self) + timedelta(days=75),
        })
        return vals

    @api.onchange("note_type")
    def _onchange_note_type(self):
        if self.lead_id:
            defaults = self._defaults_from_lead(self.lead_id, self.note_type)
            for field, value in defaults.items():
                if field in self._fields:
                    setattr(self, field, value)

    @api.depends(
        "note_type", "channel", "need_summary", "estimated_budget", "urgency",
        "quote_amount", "quote_date", "product_detail", "delivery_days",
        "objection_type", "response_summary", "next_step",
        "sale_amount", "committed_delivery_date", "production_notified",
        "lost_reason_id", "reactivation_date", "currency_id",
    )
    def _compute_preview(self):
        for wizard in self:
            wizard.preview = wizard._build_note_html(preview=True)

    def _format_money(self, amount):
        self.ensure_one()
        if amount in (False, None):
            return "—"
        currency = self.currency_id or self.env.company.currency_id
        return formatLang(self.env, amount, currency_obj=currency)

    def _format_date(self, date_value):
        if not date_value:
            return "—"
        return format_date(self.env, date_value)

    def _urgency_label(self):
        return dict(URGENCY_SELECTION).get(self.urgency, "—")

    def _production_label(self):
        return dict(self._fields["production_notified"].selection).get(self.production_notified, "—")

    def _validate_required_fields(self):
        self.ensure_one()
        missing = []
        if self.note_type == "first_contact":
            if not self.channel:
                missing.append(_("Canal de contacto"))
            if not self.need_summary:
                missing.append(_("Necesidad (resumen)"))
            if self.estimated_budget in (False, None):
                missing.append(_("Presupuesto estimado"))
            if not self.urgency:
                missing.append(_("Urgencia"))
        elif self.note_type == "quote":
            if self.quote_amount in (False, None):
                missing.append(_("Monto cotizado"))
            if not self.quote_date:
                missing.append(_("Fecha de envío"))
            if not self.product_detail:
                missing.append(_("Detalle del producto"))
            if not self.delivery_days:
                missing.append(_("Días de entrega ofrecidos"))
        elif self.note_type == "objection":
            if not self.objection_type:
                missing.append(_("Tipo de objeción"))
            if not self.response_summary:
                missing.append(_("Respuesta dada"))
            if not self.next_step:
                missing.append(_("Siguiente paso acordado"))
        elif self.note_type == "won":
            if self.sale_amount in (False, None):
                missing.append(_("Monto de venta"))
            if not self.committed_delivery_date:
                missing.append(_("Fecha de entrega comprometida"))
            if not self.production_notified:
                missing.append(_("Notificado a producción"))
        elif self.note_type == "lost":
            if not self.lost_reason_id:
                missing.append(_("Motivo de pérdida"))
            if not self.reactivation_date:
                missing.append(_("Seguimiento de reactivación"))
        if missing:
            raise UserError(_("Complete los campos obligatorios:\n• %s") % "\n• ".join(missing))

    def _build_note_text(self):
        self.ensure_one()
        if self.note_type == "first_contact":
            return _(
                "Contacto inicial vía %(channel)s. Necesidad: %(need)s. "
                "Presupuesto estimado: %(budget)s. Urgencia: %(urgency)s."
            ) % {
                "channel": self.channel,
                "need": self.need_summary,
                "budget": self._format_money(self.estimated_budget),
                "urgency": self._urgency_label(),
            }
        if self.note_type == "quote":
            return _(
                "Cotización enviada por %(amount)s el %(date)s. Producto: %(product)s. "
                "Tiempo de entrega ofrecido: %(days)s días."
            ) % {
                "amount": self._format_money(self.quote_amount),
                "date": self._format_date(self.quote_date),
                "product": self.product_detail,
                "days": self.delivery_days,
            }
        if self.note_type == "objection":
            return _(
                "Objeción presentada: %(obj_type)s (ver Manual de Objeciones). "
                "Respuesta dada: %(response)s. Siguiente paso acordado: %(next_step)s."
            ) % {
                "obj_type": self.objection_type,
                "response": self.response_summary,
                "next_step": self.next_step,
            }
        if self.note_type == "won":
            return _(
                "Venta confirmada por %(amount)s. Fecha de entrega comprometida: %(date)s. "
                "Notificado a producción: %(notified)s."
            ) % {
                "amount": self._format_money(self.sale_amount),
                "date": self._format_date(self.committed_delivery_date),
                "notified": self._production_label(),
            }
        if self.note_type == "lost":
            return _(
                "Motivo de pérdida: %(reason)s. "
                "Se deja programado seguimiento de reactivación en %(date)s (60-90 días)."
            ) % {
                "reason": self.lost_reason_id.display_name,
                "date": self._format_date(self.reactivation_date),
            }
        return ""

    def _build_note_html(self, preview=False):
        self.ensure_one()
        title = dict(NOTE_TYPES).get(self.note_type, "")
        body = self._build_note_text() if not preview or self._has_minimum_preview_data() else _("Complete los campos para ver la nota.")
        return (
            f'<div class="creart-chatter-note">'
            f'<p><strong>{title}</strong></p>'
            f'<p>{body}</p>'
            f'</div>'
        )

    def _has_minimum_preview_data(self):
        try:
            self._validate_required_fields()
            return True
        except UserError:
            return False

    def action_post_note(self):
        self.ensure_one()
        self._validate_required_fields()
        self.lead_id._creart_post_standard_internal_note(
            dict(NOTE_TYPES)[self.note_type],
            self._build_note_text(),
        )
        return {"type": "ir.actions.act_window_close"}


class CrmLeadChatterNote(models.Model):
    _inherit = "crm.lead"

    def _creart_post_standard_internal_note(self, title, body_text):
        """Publica nota interna visible para todo el equipo en el chatter."""
        self.ensure_one()
        body_html = (
            f'<div class="creart-chatter-note">'
            f'<p><strong>{title}</strong></p>'
            f'<p>{body_text}</p>'
            f'</div>'
        )
        self.message_post(
            body=body_html,
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        return True

    def action_creart_chatter_note_wizard(self):
        self.ensure_one()
        note_type = self.env.context.get("default_note_type", "first_contact")
        return {
            "type": "ir.actions.act_window",
            "name": _("Nota interna estandarizada"),
            "res_model": "creart.crm.chatter.note.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_lead_id": self.id,
                "default_note_type": note_type,
            },
        }
