# -*- coding: utf-8 -*-

from odoo import fields, models


class InsurancePolicyType(models.Model):
    _name = "insurance.policy.type"
    _description = "Tipo de póliza"
    _order = "name"

    name = fields.Char(string="Tipo de seguro", required=True)
    code = fields.Char(string="Código de póliza", required=True)
    description = fields.Text(string="Descripción")
    active = fields.Boolean(default=True)
    scheme_ids = fields.One2many("insurance.scheme", "policy_type_id", string="Esquemas")
    scheme_count = fields.Integer(compute="_compute_scheme_count")

    _sql_constraints = [
        ("code_unique", "UNIQUE(code)", "El código de tipo de póliza debe ser único."),
    ]

    def _compute_scheme_count(self):
        for record in self:
            record.scheme_count = len(record.scheme_ids)
