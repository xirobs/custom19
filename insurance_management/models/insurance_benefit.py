# -*- coding: utf-8 -*-

from odoo import api, fields, models, _


class InsuranceBenefit(models.Model):
    """Catálogo de beneficios con la nomenclatura de la aseguradora (VM, BAM UI, PCF A…)."""

    _name = "insurance.benefit"
    _description = "Beneficio de póliza (catálogo)"
    _order = "code"
    _rec_name = "code"

    code = fields.Char(string="Clave", required=True, index=True)
    name = fields.Char(string="Descripción")
    ramo_id = fields.Many2one("insurance.policy.type", string="Ramo")
    active = fields.Boolean(default=True)

    _code_unique = models.Constraint("UNIQUE(code)", "La clave del beneficio debe ser única.")

    @api.depends("code", "name")
    def _compute_display_name(self):
        for benefit in self:
            benefit.display_name = (
                "%s — %s" % (benefit.code, benefit.name) if benefit.name else (benefit.code or "")
            )

    @api.model
    def _get_or_create(self, code, ramo=None):
        code = (code or "").strip().upper()
        if not code:
            return self.browse()
        benefit = self.with_context(active_test=False).search([("code", "=", code)], limit=1)
        if not benefit:
            benefit = self.create({"code": code, "ramo_id": ramo.id if ramo else False})
        return benefit


class InsurancePolicyBenefit(models.Model):
    _name = "insurance.policy.benefit"
    _description = "Beneficio contratado en la póliza"
    _order = "policy_id, sequence, id"

    sequence = fields.Integer(default=10)
    policy_id = fields.Many2one("insurance.policy", required=True, ondelete="cascade", index=True)
    section = fields.Char(string="Sección", help="Ej. TITULAR, TITULAR NO FUMADOR.")
    benefit_id = fields.Many2one("insurance.benefit", string="Beneficio", required=True, ondelete="restrict")
    insured_amount = fields.Monetary(string="Suma asegurada inicial", currency_field="currency_id")
    annex = fields.Char(string="Anexo")
    effective_date = fields.Date(string="Fecha de efectividad")
    coverage_years = fields.Char(string="Cobertura (años)", help="Años de cobertura. Ej. 20 o 1REN.")
    payment_years = fields.Char(string="Periodo de pago (años)")
    premium = fields.Monetary(string="Prima inicial", currency_field="currency_id")
    no_cost = fields.Boolean(string="Sin costo")
    currency_id = fields.Many2one(related="policy_id.currency_id")
