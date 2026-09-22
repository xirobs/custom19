# -*- coding: utf-8 -*-

from odoo import fields, models


class InsuranceSchemeFeature(models.Model):
    _name = "insurance.scheme.feature"
    _description = "Característica de la oferta"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    scheme_id = fields.Many2one("insurance.scheme", required=True, ondelete="cascade")
    name = fields.Char(string="Característica", required=True)
    included = fields.Boolean(string="Incluido", default=True)


class InsurancePolicyFeature(models.Model):
    _name = "insurance.policy.feature"
    _description = "Característica de la póliza"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    policy_id = fields.Many2one("insurance.policy", required=True, ondelete="cascade")
    name = fields.Char(string="Característica", required=True)
    included = fields.Boolean(string="Incluido", default=True)
