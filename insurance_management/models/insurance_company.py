# -*- coding: utf-8 -*-

from odoo import fields, models


class InsuranceCompany(models.Model):
    _name = "insurance.company"
    _description = "Compañía aseguradora"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(string="Nombre", required=True, tracking=True)
    code = fields.Char(string="Código", tracking=True)
    partner_id = fields.Many2one("res.partner", string="Contacto", tracking=True)
    phone = fields.Char(string="Teléfono", related="partner_id.phone", readonly=False)
    email = fields.Char(string="Correo electrónico", related="partner_id.email", readonly=False)
    website = fields.Char(string="Sitio web", related="partner_id.website", readonly=False)
    vat = fields.Char(string="RFC", related="partner_id.vat", readonly=False)
    country_id = fields.Many2one(
        "res.country",
        string="País",
        default=lambda self: self.env.ref("base.mx", raise_if_not_found=False),
    )
    active = fields.Boolean(default=True)
    note = fields.Text(string="Notas")
    policy_ids = fields.One2many("insurance.policy", "insurance_company_id", string="Pólizas")
    policy_count = fields.Integer(compute="_compute_policy_count")

    def _compute_policy_count(self):
        for company in self:
            company.policy_count = len(company.policy_ids)

    def action_open_policies(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Pólizas",
            "res_model": "insurance.policy",
            "view_mode": "list,form",
            "domain": [("insurance_company_id", "=", self.id)],
            "context": {"default_insurance_company_id": self.id},
        }
