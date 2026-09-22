# -*- coding: utf-8 -*-

from odoo import api, fields, models


class InsuranceAgent(models.Model):
    _name = "insurance.agent"
    _description = "Agente de seguros"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(string="Nombre", required=True, tracking=True)
    agent_code = fields.Char(string="Código de agente", tracking=True)
    partner_id = fields.Many2one("res.partner", string="Contacto")
    user_id = fields.Many2one("res.users", string="Usuario")
    branch_id = fields.Many2one("insurance.branch", string="Rama / sucursal")
    insurance_company_id = fields.Many2one("insurance.company", string="Compañía de seguros")
    mobile = fields.Char(string="Móvil")
    email = fields.Char(string="Correo electrónico")
    date_start = fields.Date(string="Fecha de alta", default=fields.Date.context_today)
    commission_rate = fields.Float(string="Comisión (%)", default=0.0)
    active = fields.Boolean(default=True)
    policy_ids = fields.One2many("insurance.policy", "agent_id", string="Pólizas")
    policy_count = fields.Integer(compute="_compute_policy_count")

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if self.partner_id:
            self.name = self.partner_id.name
            self.mobile = self.partner_id.mobile or self.partner_id.phone
            self.email = self.partner_id.email

    def _compute_policy_count(self):
        for agent in self:
            agent.policy_count = len(agent.policy_ids)

    def action_open_policies(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Pólizas del agente",
            "res_model": "insurance.policy",
            "view_mode": "list,form",
            "domain": [("agent_id", "=", self.id)],
            "context": {"default_agent_id": self.id},
        }
