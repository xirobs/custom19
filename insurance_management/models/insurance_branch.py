# -*- coding: utf-8 -*-

from odoo import api, fields, models, _


class InsuranceBranch(models.Model):
    _name = "insurance.branch"
    _description = "Sucursal / Rama"
    _order = "name"

    name = fields.Char(string="Nombre", required=True)
    code = fields.Char(string="Código")
    company_id = fields.Many2one(
        "res.company",
        string="Compañía",
        default=lambda self: self.env.company,
    )
    street = fields.Char(string="Dirección")
    city = fields.Char(string="Ciudad")
    phone = fields.Char(string="Teléfono")
    email = fields.Char(string="Correo electrónico")
    active = fields.Boolean(default=True)
    agent_ids = fields.One2many("insurance.agent", "branch_id", string="Agentes")
    policy_ids = fields.One2many("insurance.policy", "branch_id", string="Pólizas")
    currency_id = fields.Many2one(
        "res.currency",
        string="Moneda",
        default=lambda self: self.env.company.currency_id,
    )
    dash_active_policies = fields.Integer(
        string="En servicio",
        compute="_compute_dashboard_kpis",
        store=True,
    )
    dash_renew_count = fields.Integer(
        string="Por renovar",
        compute="_compute_dashboard_kpis",
        store=True,
    )
    dash_renew_premium = fields.Monetary(
        string="Prima a renovar",
        compute="_compute_dashboard_kpis",
        store=True,
        currency_field="currency_id",
    )
    dash_overdue_count = fields.Integer(
        string="Cuotas en mora",
        compute="_compute_dashboard_kpis",
        store=True,
    )
    dash_overdue_amount = fields.Monetary(
        string="Monto en mora",
        compute="_compute_dashboard_kpis",
        store=True,
        currency_field="currency_id",
    )
    dash_coverage_limit = fields.Monetary(
        string="Suma asegurada",
        compute="_compute_dashboard_kpis",
        store=True,
        currency_field="currency_id",
    )
    dash_coverage_used = fields.Monetary(
        string="Cobertura consumida",
        compute="_compute_dashboard_kpis",
        store=True,
        currency_field="currency_id",
    )
    dash_coverage_left = fields.Monetary(
        string="Cobertura disponible",
        compute="_compute_dashboard_kpis",
        store=True,
        currency_field="currency_id",
    )
    dash_health_state = fields.Selection(
        [
            ("ok", "Al día"),
            ("renew", "Por renovar"),
            ("overdue", "En mora"),
        ],
        string="Estado",
        compute="_compute_dashboard_kpis",
        store=True,
    )

    @api.depends(
        "policy_ids.state",
        "policy_ids.lifecycle_stage",
        "policy_ids.policy_amount",
        "policy_ids.insured_amount",
        "policy_ids.coverage_consumed",
        "policy_ids.coverage_remaining",
        "policy_ids.installment_ids.state",
        "policy_ids.installment_ids.amount",
    )
    def _compute_dashboard_kpis(self):
        Installment = self.env["insurance.installment"]
        for branch in self:
            policies = branch.policy_ids.filtered(lambda p: p.state in ("confirmed", "expired"))
            renew = policies.filtered(lambda p: p.lifecycle_stage == "renewal")
            overdue = Installment.search([
                ("state", "=", "overdue"),
                ("policy_id.branch_id", "=", branch.id),
            ])
            branch.dash_active_policies = len(policies.filtered(lambda p: p.lifecycle_stage == "active"))
            branch.dash_renew_count = len(renew)
            branch.dash_renew_premium = sum(renew.mapped("policy_amount"))
            branch.dash_overdue_count = len(overdue)
            branch.dash_overdue_amount = sum(overdue.mapped("amount"))
            branch.dash_coverage_limit = sum(policies.mapped("insured_amount"))
            branch.dash_coverage_used = sum(policies.mapped("coverage_consumed"))
            branch.dash_coverage_left = sum(policies.mapped("coverage_remaining"))
            if branch.dash_overdue_count:
                branch.dash_health_state = "overdue"
            elif branch.dash_renew_count:
                branch.dash_health_state = "renew"
            else:
                branch.dash_health_state = "ok"

    def action_open_branch_policies(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Pólizas — %s") % self.name,
            "res_model": "insurance.policy",
            "view_mode": "kanban,list,form,calendar,pivot,graph,activity",
            "domain": [("branch_id", "=", self.id), ("state", "in", ["confirmed", "expired", "draft"])],
            "context": {"default_branch_id": self.id},
        }

    def action_open_branch_renewals(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Por renovar — %s") % self.name,
            "res_model": "insurance.policy",
            "view_mode": "kanban,list,form,calendar,activity",
            "domain": [
                ("branch_id", "=", self.id),
                ("lifecycle_stage", "=", "renewal"),
            ],
        }

    def action_open_branch_overdue(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Mora — %s") % self.name,
            "res_model": "insurance.installment",
            "view_mode": "list,form",
            "domain": [
                ("state", "=", "overdue"),
                ("policy_id.branch_id", "=", self.id),
            ],
        }
