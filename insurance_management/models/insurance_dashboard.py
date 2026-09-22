# -*- coding: utf-8 -*-

from odoo import api, fields, models, _


class InsuranceDashboard(models.TransientModel):
    _name = "insurance.dashboard"
    _description = "Tablero de renovación y mora"

    branch_id = fields.Many2one("insurance.branch", string="Sucursal")
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    renew_count = fields.Integer(string="Pólizas por renovar", compute="_compute_kpis")
    renew_premium = fields.Monetary(string="Prima en renovación", compute="_compute_kpis", currency_field="currency_id")
    overdue_count = fields.Integer(string="Cuotas en mora", compute="_compute_kpis")
    overdue_amount = fields.Monetary(string="Monto en mora", compute="_compute_kpis", currency_field="currency_id")
    coverage_limit = fields.Monetary(string="Suma asegurada vigente", compute="_compute_kpis", currency_field="currency_id")
    coverage_used = fields.Monetary(string="Cobertura consumida", compute="_compute_kpis", currency_field="currency_id")
    coverage_left = fields.Monetary(string="Cobertura disponible", compute="_compute_kpis", currency_field="currency_id")
    active_policies = fields.Integer(string="Pólizas en servicio", compute="_compute_kpis")
    cfdi_pending = fields.Integer(string="CFDI globales por timbrar", compute="_compute_kpis")
    line_ids = fields.One2many(
        "insurance.dashboard.line",
        "dashboard_id",
        string="Por sucursal",
    )

    def _policy_domain(self):
        domain = [("state", "in", ["confirmed", "expired"])]
        if self.branch_id:
            domain.append(("branch_id", "=", self.branch_id.id))
        return domain

    @api.depends("branch_id")
    def _compute_kpis(self):
        Policy = self.env["insurance.policy"]
        Installment = self.env["insurance.installment"]
        Move = self.env["account.move"]
        for dash in self:
            policies = Policy.search(dash._policy_domain())
            renew = policies.filtered(lambda p: p.lifecycle_stage == "renewal")
            overdue = Installment.search(
                [("state", "=", "overdue")]
                + ([("policy_id.branch_id", "=", dash.branch_id.id)] if dash.branch_id else [])
            )
            dash.renew_count = len(renew)
            dash.renew_premium = sum(renew.mapped("policy_amount"))
            dash.overdue_count = len(overdue)
            dash.overdue_amount = sum(overdue.mapped("amount"))
            dash.coverage_limit = sum(policies.mapped("insured_amount")) or sum(
                policies.mapped("coverage_ids.insured_amount")
            )
            dash.coverage_used = sum(policies.mapped("coverage_consumed"))
            dash.coverage_left = sum(policies.mapped("coverage_remaining"))
            dash.active_policies = len(policies.filtered(lambda p: p.lifecycle_stage == "active"))
            cfdi_domain = [
                ("is_insurance_cfdi_global", "=", True),
                ("state", "=", "posted"),
            ]
            if "l10n_mx_edi_cfdi_state" in Move._fields:
                cfdi_domain.append(("l10n_mx_edi_cfdi_state", "not in", ["sent", "global_sent"]))
            elif "edi_state" in Move._fields:
                cfdi_domain.append(("edi_state", "not in", ["sent"]))
            if dash.branch_id:
                cfdi_domain.append(("insurance_policy_id.branch_id", "=", dash.branch_id.id))
            dash.cfdi_pending = Move.search_count(cfdi_domain) if dash._has_cfdi_state(Move) else 0

    def _has_cfdi_state(self, Move):
        return "l10n_mx_edi_cfdi_state" in Move._fields or "edi_state" in Move._fields

    def action_refresh(self):
        self.ensure_one()
        self.line_ids.unlink()
        branches = self.env["insurance.branch"].search([])
        if not branches:
            self._compute_kpis()
            return True
        lines = []
        for branch in branches:
            policies = self.env["insurance.policy"].search([
                ("state", "in", ["confirmed", "expired"]),
                ("branch_id", "=", branch.id),
            ])
            overdue = self.env["insurance.installment"].search([
                ("state", "=", "overdue"),
                ("policy_id.branch_id", "=", branch.id),
            ])
            renew = policies.filtered(lambda p: p.lifecycle_stage == "renewal")
            lines.append((0, 0, {
                "branch_id": branch.id,
                "renew_count": len(renew),
                "renew_premium": sum(renew.mapped("policy_amount")),
                "overdue_count": len(overdue),
                "overdue_amount": sum(overdue.mapped("amount")),
                "coverage_limit": sum(policies.mapped("insured_amount")),
                "coverage_used": sum(policies.mapped("coverage_consumed")),
                "coverage_left": sum(policies.mapped("coverage_remaining")),
                "active_policies": len(policies.filtered(lambda p: p.lifecycle_stage == "active")),
            }))
        self.line_ids = lines
        return True

    @api.model
    def action_open_dashboard(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Tablero"),
            "res_model": "insurance.branch",
            "view_mode": "kanban,list",
            "search_view_id": self.env.ref("insurance_management.view_insurance_branch_dashboard_search").id,
            "views": [
                (self.env.ref("insurance_management.view_insurance_branch_dashboard_kanban").id, "kanban"),
                (self.env.ref("insurance_management.view_insurance_branch_dashboard_list").id, "list"),
            ],
            "target": "current",
        }

    def action_open_renewals(self):
        domain = [("lifecycle_stage", "=", "renewal"), ("state", "in", ["confirmed", "expired"])]
        if self.branch_id:
            domain.append(("branch_id", "=", self.branch_id.id))
        return {
            "type": "ir.actions.act_window",
            "name": _("Pólizas por renovar"),
            "res_model": "insurance.policy",
            "view_mode": "kanban,list,form,calendar,pivot,graph,activity",
            "domain": domain,
        }

    def action_open_overdue(self):
        domain = [("state", "=", "overdue")]
        if self.branch_id:
            domain.append(("policy_id.branch_id", "=", self.branch_id.id))
        return {
            "type": "ir.actions.act_window",
            "name": _("Mora"),
            "res_model": "insurance.installment",
            "view_mode": "list,form",
            "domain": domain,
        }

    def action_open_active(self):
        domain = [("lifecycle_stage", "=", "active"), ("state", "=", "confirmed")]
        if self.branch_id:
            domain.append(("branch_id", "=", self.branch_id.id))
        return {
            "type": "ir.actions.act_window",
            "name": _("En servicio"),
            "res_model": "insurance.policy",
            "view_mode": "kanban,list,form,calendar,pivot,graph,activity",
            "domain": domain,
        }


class InsuranceDashboardLine(models.TransientModel):
    _name = "insurance.dashboard.line"
    _description = "Línea de tablero por sucursal"

    dashboard_id = fields.Many2one("insurance.dashboard", ondelete="cascade")
    branch_id = fields.Many2one("insurance.branch", string="Sucursal")
    active_policies = fields.Integer(string="En servicio")
    renew_count = fields.Integer(string="Por renovar")
    renew_premium = fields.Monetary(string="Prima a renovar", currency_field="currency_id")
    overdue_count = fields.Integer(string="Cuotas mora")
    overdue_amount = fields.Monetary(string="Monto mora", currency_field="currency_id")
    coverage_limit = fields.Monetary(string="Suma asegurada", currency_field="currency_id")
    coverage_used = fields.Monetary(string="Consumido", currency_field="currency_id")
    coverage_left = fields.Monetary(string="Disponible", currency_field="currency_id")
    currency_id = fields.Many2one(related="dashboard_id.currency_id")
