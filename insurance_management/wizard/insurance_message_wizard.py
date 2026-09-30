# -*- coding: utf-8 -*-
"""Asistente para enviar mensajes personalizados por correo o WhatsApp."""

import json
import re
from urllib.parse import quote

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

from ..models.insurance_messaging import MESSAGE_CATEGORIES

DEFAULT_CATEGORY = {
    "insurance.installment": "collection",
    "insurance.policy": "renewal",
    "res.partner": "general",
    "crm.lead": "sales",
    "insurance.task": "general",
    "insurance.claim": "general",
}


class InsuranceMessageWizard(models.TransientModel):
    _name = "insurance.message.wizard"
    _description = "Enviar mensaje personalizado (correo / WhatsApp)"

    res_model = fields.Char(required=True)
    res_ids = fields.Char(required=True, help="IDs en JSON")
    record_count = fields.Integer(compute="_compute_record_count", string="N.º de registros")
    group_id = fields.Many2one("insurance.broadcast.group", string="Grupo")
    channel = fields.Selection(
        [("email", "Correo electrónico"), ("whatsapp", "WhatsApp")],
        string="Enviar por",
        default="email",
        required=True,
    )
    category = fields.Selection(MESSAGE_CATEGORIES, string="Tipo de mensaje")
    template_id = fields.Many2one(
        "mail.template",
        string="Plantilla",
        domain="[('model', '=', res_model), ('insurance_category', '!=', False)]",
    )
    subject = fields.Char(string="Asunto")
    body = fields.Html(string="Mensaje", sanitize_style=True)
    partner_ids = fields.Many2many("res.partner", string="Destinatarios", compute="_compute_partners")
    missing_contact = fields.Char(compute="_compute_partners", string="Sin datos de contacto")
    whatsapp_enterprise = fields.Boolean(compute="_compute_whatsapp_enterprise")

    # ------------------------------------------------------------------
    @api.model
    def action_open(self, records, group=None):
        if not records:
            raise UserError(_("Seleccione al menos un registro."))
        category = DEFAULT_CATEGORY.get(records._name, "general")
        if records._name == "insurance.installment" and all(r.collection_status == "overdue" for r in records):
            category = "overdue"
        wizard = self.create({
            "res_model": records._name,
            "res_ids": json.dumps(records.ids),
            "category": category,
            "group_id": group.id if group else False,
        })
        wizard._onchange_category()
        wizard._onchange_template_id()
        return {
            "type": "ir.actions.act_window",
            "name": _("Enviar mensaje"),
            "res_model": self._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def _records(self):
        self.ensure_one()
        return self.env[self.res_model].browse(json.loads(self.res_ids or "[]")).exists()

    @api.depends("res_ids")
    def _compute_record_count(self):
        for wizard in self:
            wizard.record_count = len(json.loads(wizard.res_ids or "[]"))

    def _compute_whatsapp_enterprise(self):
        available = "whatsapp.composer" in self.env
        for wizard in self:
            wizard.whatsapp_enterprise = available

    @api.model
    def _partner_of(self, record):
        if record._name == "res.partner":
            return record
        if record._name == "crm.lead":
            return record.partner_id
        return record.partner_id if "partner_id" in record._fields else self.env["res.partner"]

    @api.depends("res_ids", "res_model", "channel")
    def _compute_partners(self):
        for wizard in self:
            partners = self.env["res.partner"]
            missing = []
            for record in wizard._records():
                partner = wizard._partner_of(record)
                partners |= partner
                if wizard.channel == "email" and not (partner.email or getattr(record, "email_from", False)):
                    missing.append(record.display_name)
                if wizard.channel == "whatsapp" and not (partner and partner._insurance_phone()):
                    missing.append(record.display_name)
            wizard.partner_ids = partners
            wizard.missing_contact = ", ".join(missing[:10]) + ("…" if len(missing) > 10 else "") if missing else False

    @api.onchange("category")
    def _onchange_category(self):
        if not self.category:
            return
        template = self.env["mail.template"].search([
            ("model", "=", self.res_model),
            ("insurance_category", "=", self.category),
        ], limit=1)
        if template:
            self.template_id = template

    @api.onchange("template_id")
    def _onchange_template_id(self):
        records = self._records()
        if not self.template_id or not records:
            return
        first = records[:1]
        self.subject = self.template_id._render_field("subject", first.ids)[first.id]
        self.body = self.template_id._render_field("body_html", first.ids, compute_lang=True)[first.id]

    # ------------------------------------------------------------------
    def _rendered(self, record):
        """(asunto, cuerpo html) para un registro: la plantilla renderizada o el texto editado."""
        if len(self._records()) == 1 or not self.template_id:
            return self.subject, self.body
        subject = self.template_id._render_field("subject", record.ids)[record.id]
        body = self.template_id._render_field("body_html", record.ids, compute_lang=True)[record.id]
        return subject, body

    def action_send_email(self):
        self.ensure_one()
        records = self._records()
        if not self.body:
            raise UserError(_("Escriba el mensaje o elija una plantilla."))
        sent = 0
        for record in records:
            partner = self._partner_of(record)
            if not partner.email:
                continue
            subject, body = self._rendered(record)
            target = record if hasattr(record, "message_post") else partner
            target.message_post(
                body=body,
                subject=subject,
                partner_ids=partner.ids,
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
                email_layout_xmlid="mail.mail_notification_light",
            )
            sent += 1
        if self.group_id:
            self.group_id.message_post(
                body=_("Mensaje «%(subject)s» enviado por correo a %(count)s integrantes.") % {
                    "subject": self.subject or "", "count": sent,
                },
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Correos enviados"),
                "message": _("Se enviaron %s correos personalizados.") % sent,
                "type": "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    @api.model
    def _whatsapp_number(self, partner):
        phone = re.sub(r"\D", "", partner._insurance_phone() or "")
        if len(phone) == 10:  # número mexicano sin lada internacional
            phone = "52" + phone
        return phone

    def action_send_whatsapp(self):
        self.ensure_one()
        records = self._records()
        # WhatsApp de Odoo Enterprise: plantillas aprobadas por Meta, envío individual o masivo
        if "whatsapp.composer" in self.env:
            target_model, target_ids = self.res_model, records.ids
            if self.group_id or self.res_model not in self._whatsapp_models():
                partners = records.mapped(lambda r: self._partner_of(r))
                target_model, target_ids = "res.partner", partners.ids
            ctx = {
                "active_model": target_model,
                "active_ids": target_ids,
                "active_id": target_ids[0] if target_ids else False,
                "default_res_model": target_model,
                "default_res_ids": repr(target_ids),
                "default_batch_mode": len(target_ids) > 1,
            }
            return {
                "type": "ir.actions.act_window",
                "name": _("Enviar WhatsApp"),
                "res_model": "whatsapp.composer",
                "view_mode": "form",
                "target": "new",
                "context": ctx,
            }
        # Sin módulo WhatsApp: abre WhatsApp Web con el mensaje listo (un destinatario)
        if len(records) != 1:
            raise UserError(_(
                "Para envíos masivos por WhatsApp instale la aplicación WhatsApp de Odoo. "
                "Sin ella, el envío es a un destinatario a la vez."
            ))
        partner = self._partner_of(records)
        number = self._whatsapp_number(partner)
        if not number:
            raise UserError(_("El contacto no tiene teléfono."))
        text = html2plaintext(self.body or "").strip()
        records.message_post(body=_("Mensaje enviado por WhatsApp:<br/>%s") % (self.body or ""),
                             message_type="comment", subtype_xmlid="mail.mt_note") \
            if hasattr(records, "message_post") else None
        return {
            "type": "ir.actions.act_url",
            "url": "https://wa.me/%s?text=%s" % (number, quote(text)),
            "target": "new",
        }

    @api.model
    def _whatsapp_models(self):
        if "whatsapp.template" not in self.env:
            return []
        try:
            return self.env["whatsapp.template"].search([("status", "=", "approved")]).mapped("model")
        except Exception:  # estructura distinta según versión del módulo
            return []
