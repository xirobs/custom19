# -*- coding: utf-8 -*-
"""Plantillas de mensajes por categoría y grupos de difusión."""

from odoo import api, fields, models, _
from odoo.exceptions import UserError

MESSAGE_CATEGORIES = [
    ("collection", "Cobranza / recordatorio de pago"),
    ("overdue", "Pago atrasado"),
    ("last_payments", "Últimos pagos"),
    ("renewal", "Renovación"),
    ("welcome", "Contratación / bienvenida"),
    ("signature", "Firma de contrato"),
    ("birthday", "Cumpleaños"),
    ("profession", "Día de su profesión"),
    ("receipt", "Recibo de pago"),
    ("sales", "Prospecto / propuesta"),
    ("general", "General"),
]


class MailTemplate(models.Model):
    _inherit = "mail.template"

    insurance_category = fields.Selection(
        MESSAGE_CATEGORIES,
        string="Categoría (seguros)",
        help="Marca la plantilla para usarla desde el asistente de mensajes de seguros.",
    )


class InsuranceBroadcastGroup(models.Model):
    _name = "insurance.broadcast.group"
    _description = "Grupo de difusión (correo / WhatsApp)"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(string="Grupo", required=True, tracking=True)
    description = fields.Text(string="Descripción")
    partner_ids = fields.Many2many(
        "res.partner",
        "insurance_broadcast_group_partner_rel",
        "group_id",
        "partner_id",
        string="Integrantes",
    )
    member_count = fields.Integer(compute="_compute_member_count", string="N.º de integrantes")
    user_ids = fields.Many2many("res.users", string="Equipo responsable", default=lambda self: self.env.user)
    channel_id = fields.Many2one("discuss.channel", string="Canal de conversación", copy=False)
    ramo_id = fields.Many2one("insurance.policy.type", string="Llenar con clientes del ramo")
    insurance_company_id = fields.Many2one("insurance.company", string="Llenar con clientes de la aseguradora")
    active = fields.Boolean(default=True)

    @api.depends("partner_ids")
    def _compute_member_count(self):
        for group in self:
            group.member_count = len(group.partner_ids)

    def action_fill_members(self):
        for group in self:
            domain = [("state", "in", ["confirmed", "expired"])]
            if group.ramo_id:
                domain.append(("policy_type_id", "=", group.ramo_id.id))
            if group.insurance_company_id:
                domain.append(("insurance_company_id", "=", group.insurance_company_id.id))
            policies = self.env["insurance.policy"].search(domain)
            group.partner_ids = [(4, pid) for pid in policies.mapped("partner_id").ids]
        return True

    def action_send_message(self):
        self.ensure_one()
        if not self.partner_ids:
            raise UserError(_("El grupo no tiene integrantes."))
        return self.env["insurance.message.wizard"].action_open(self.partner_ids, group=self)

    def action_open_channel(self):
        """Canal de Conversaciones para coordinar al equipo sobre este grupo."""
        self.ensure_one()
        if not self.channel_id:
            channel = self.env["discuss.channel"].create({
                "name": self.name,
                "channel_type": "channel",
                "description": self.description or _("Grupo de difusión de seguros"),
            })
            partners = self.user_ids.mapped("partner_id") | self.env.user.partner_id
            channel._add_members(partners=partners)
            self.channel_id = channel.id
        return {
            "type": "ir.actions.act_url",
            "url": "/odoo/action-mail.action_discuss?active_id=%s" % self.channel_id.id,
            "target": "self",
        }
