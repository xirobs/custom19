# -*- coding: utf-8 -*-
"""Comisiones de asesores: reglas por ramo y aseguradora (primer año / renovación)
y registro de comisión por cada pago cobrado."""

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class InsuranceCommissionRule(models.Model):
    _name = "insurance.commission.rule"
    _description = "Regla de comisión del asesor"
    _order = "agent_id, sequence, id"

    sequence = fields.Integer(default=10)
    agent_id = fields.Many2one("insurance.agent", string="Asesor", required=True, ondelete="cascade")
    insurance_company_id = fields.Many2one("insurance.company", string="Aseguradora", help="Vacío = todas.")
    ramo_id = fields.Many2one("insurance.policy.type", string="Ramo", help="Vacío = todos.")
    rate_first_year = fields.Float(string="% primer año", required=True)
    rate_renewal = fields.Float(string="% renovación", help="Vacío o 0 = mismo % del primer año.")
    note = fields.Char(string="Condición del contrato")

    @api.constrains("rate_first_year", "rate_renewal")
    def _check_rates(self):
        for rule in self:
            for rate in (rule.rate_first_year, rule.rate_renewal):
                if rate < 0 or rate > 100:
                    raise ValidationError(_("El porcentaje de comisión debe estar entre 0 y 100."))

    def _specificity(self):
        self.ensure_one()
        return (2 if self.insurance_company_id else 0) + (1 if self.ramo_id else 0)


class InsuranceCommission(models.Model):
    _name = "insurance.commission"
    _description = "Comisión generada"
    _order = "date desc, id desc"

    name = fields.Char(string="Concepto", compute="_compute_name", store=True)
    agent_id = fields.Many2one("insurance.agent", string="Asesor", required=True, index=True)
    policy_id = fields.Many2one("insurance.policy", string="Póliza", required=True, ondelete="cascade", index=True)
    installment_id = fields.Many2one("insurance.installment", string="Cuota", ondelete="set null", index=True)
    partner_id = fields.Many2one(related="policy_id.partner_id", store=True, string="Cliente")
    ramo_id = fields.Many2one(related="policy_id.policy_type_id", store=True, string="Ramo")
    insurance_company_id = fields.Many2one(related="policy_id.insurance_company_id", store=True, string="Aseguradora")
    date = fields.Date(string="Fecha de cobro", required=True, index=True)
    commission_type = fields.Selection(
        [("first_year", "Primer año"), ("renewal", "Renovación")],
        string="Tipo",
        required=True,
        default="first_year",
    )
    base_amount = fields.Monetary(string="Prima cobrada", currency_field="currency_id")
    rate = fields.Float(string="% comisión")
    amount = fields.Monetary(string="Comisión", currency_field="currency_id")
    currency_id = fields.Many2one("res.currency", required=True)
    amount_company = fields.Monetary(
        string="Comisión (moneda empresa)",
        currency_field="company_currency_id",
        compute="_compute_amount_company",
        store=True,
    )
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    company_currency_id = fields.Many2one(related="company_id.currency_id", string="Moneda empresa")
    state = fields.Selection(
        [("to_pay", "Por liquidar"), ("paid", "Liquidada"), ("cancelled", "Cancelada")],
        string="Estado",
        default="to_pay",
        required=True,
        index=True,
    )
    bill_id = fields.Many2one("account.move", string="Factura de comisión", copy=False)

    @api.depends("policy_id.name", "installment_id.number", "agent_id.name")
    def _compute_name(self):
        for com in self:
            com.name = _("%(policy)s — cuota %(number)s") % {
                "policy": com.policy_id.name or "",
                "number": com.installment_id.number or "-",
            }

    @api.depends("amount", "currency_id", "date", "company_id")
    def _compute_amount_company(self):
        for com in self:
            if com.currency_id and com.company_id and com.currency_id != com.company_id.currency_id:
                com.amount_company = com.currency_id._convert(
                    com.amount, com.company_id.currency_id, com.company_id,
                    com.date or fields.Date.context_today(com),
                )
            else:
                com.amount_company = com.amount

    def action_create_bill(self):
        """Genera una factura de proveedor por asesor con las comisiones por liquidar."""
        to_bill = self.filtered(lambda c: c.state == "to_pay" and not c.bill_id)
        if not to_bill:
            raise UserError(_("Seleccione comisiones por liquidar sin factura."))
        bills = self.env["account.move"]
        for agent in to_bill.mapped("agent_id"):
            if not agent.partner_id:
                raise UserError(_("El asesor %s no tiene contacto para facturar.") % agent.name)
            for currency in to_bill.filtered(lambda c: c.agent_id == agent).mapped("currency_id"):
                lines = to_bill.filtered(lambda c: c.agent_id == agent and c.currency_id == currency)
                bill = self.env["account.move"].create({
                    "move_type": "in_invoice",
                    "partner_id": agent.partner_id.id,
                    "currency_id": currency.id,
                    "invoice_date": fields.Date.context_today(self),
                    "ref": _("Comisiones %s") % agent.name,
                    "invoice_line_ids": [
                        (0, 0, {
                            "name": _("Comisión %(name)s (%(rate)s%%)") % {"name": line.name, "rate": line.rate},
                            "quantity": 1,
                            "price_unit": line.amount,
                        })
                        for line in lines
                    ],
                })
                lines.write({"bill_id": bill.id, "state": "paid"})
                bills |= bill
        return {
            "type": "ir.actions.act_window",
            "name": _("Facturas de comisión"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", bills.ids)],
        }

    def action_mark_paid(self):
        self.filtered(lambda c: c.state == "to_pay").write({"state": "paid"})
        return True


class InsuranceAgent(models.Model):
    _inherit = "insurance.agent"

    key_ids = fields.One2many("insurance.agent.key", "agent_id", string="Claves / carteras")
    commission_rule_ids = fields.One2many("insurance.commission.rule", "agent_id", string="Reglas de comisión")
    commission_ids = fields.One2many("insurance.commission", "agent_id", string="Comisiones")
    commission_to_pay = fields.Monetary(
        string="Comisión por liquidar",
        compute="_compute_commission_totals",
        currency_field="company_currency_id",
    )
    commission_month = fields.Monetary(
        string="Comisión del mes",
        compute="_compute_commission_totals",
        currency_field="company_currency_id",
    )
    company_currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id,
    )

    def _compute_commission_totals(self):
        first_day = fields.Date.context_today(self).replace(day=1)
        for agent in self:
            coms = agent.commission_ids.filtered(lambda c: c.state != "cancelled")
            agent.commission_to_pay = sum(coms.filtered(lambda c: c.state == "to_pay").mapped("amount_company"))
            agent.commission_month = sum(coms.filtered(lambda c: c.date and c.date >= first_day).mapped("amount_company"))

    def _get_commission_rule(self, company, ramo):
        self.ensure_one()
        candidates = self.commission_rule_ids.filtered(
            lambda r: (not r.insurance_company_id or r.insurance_company_id == company)
            and (not r.ramo_id or r.ramo_id == ramo)
        )
        if not candidates:
            return candidates
        return candidates.sorted(lambda r: (-r._specificity(), r.sequence))[:1]

    def action_open_commissions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Comisiones de %s") % self.name,
            "res_model": "insurance.commission",
            "view_mode": "list,pivot,graph,form",
            "domain": [("agent_id", "=", self.id)],
            "context": {"search_default_to_pay": 1},
        }

    def action_open_keys(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Carteras de %s") % self.name,
            "res_model": "insurance.agent.key",
            "view_mode": "list,form",
            "domain": [("agent_id", "=", self.id)],
            "context": {"default_agent_id": self.id},
        }


class InsuranceInstallmentCommission(models.Model):
    _inherit = "insurance.installment"

    commission_ids = fields.One2many("insurance.commission", "installment_id", string="Comisiones")

    def _generate_commissions(self):
        Commission = self.env["insurance.commission"].sudo()
        for line in self.filtered(lambda l: l.state == "paid" and l.paid_amount):
            policy = line.policy_id
            agent = policy.agent_id
            if not agent or line.commission_ids.filtered(lambda c: c.agent_id == agent and c.state != "cancelled"):
                continue
            emission = policy.emission_date or policy.coverage_start_date
            is_renewal = bool(policy.origin_policy_id) or bool(
                emission and line.date_from and line.date_from >= emission + relativedelta(years=1)
            )
            rule = agent._get_commission_rule(policy.insurance_company_id, policy.policy_type_id)
            if rule:
                rate = rule.rate_renewal if (is_renewal and rule.rate_renewal) else rule.rate_first_year
            else:
                rate = policy.commission_rate or agent.commission_rate
            if not rate:
                continue
            Commission.create({
                "agent_id": agent.id,
                "policy_id": policy.id,
                "installment_id": line.id,
                "date": line.payment_date or fields.Date.context_today(self),
                "commission_type": "renewal" if is_renewal else "first_year",
                "base_amount": line.paid_amount,
                "rate": rate,
                "amount": line.paid_amount * rate / 100.0,
                "currency_id": line.currency_id.id or policy.currency_id.id,
                "company_id": policy.company_id.id,
            })

    def write(self, vals):
        res = super().write(vals)
        if vals.get("state") == "paid" or "paid_amount" in vals:
            self.filtered(lambda l: l.state == "paid")._generate_commissions()
        if vals.get("state") in ("pending", "cancelled", "overdue"):
            self.mapped("commission_ids").filtered(
                lambda c: c.state == "to_pay"
            ).write({"state": "cancelled"})
        return res
