# -*- coding: utf-8 -*-

from odoo import fields, models


class InsurancePolicyType(models.Model):
    _name = "insurance.policy.type"
    _description = "Ramo de seguro"
    _order = "name"

    name = fields.Char(string="Ramo", required=True)
    code = fields.Char(string="Código de póliza", required=True)
    description = fields.Text(string="Descripción")
    active = fields.Boolean(default=True)
    scheme_ids = fields.One2many("insurance.scheme", "policy_type_id", string="Esquemas")
    scheme_count = fields.Integer(compute="_compute_scheme_count")
    offer_ids = fields.One2many("insurance.offer", "ramo_id", string="Ofertas")
    offer_count = fields.Integer(string="N.º de ofertas", compute="_compute_offer_count")

    _sql_constraints = [
        ("code_unique", "UNIQUE(code)", "El código de tipo de póliza debe ser único."),
    ]

    def _compute_scheme_count(self):
        for record in self:
            record.scheme_count = len(record.scheme_ids)

    def _compute_offer_count(self):
        for record in self:
            record.offer_count = len(record.offer_ids.filtered(lambda o: not o.parent_id))
