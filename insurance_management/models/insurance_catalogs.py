# -*- coding: utf-8 -*-
"""Catálogos de configuración: tipos de siniestro, tipos de retiro, profesiones,
claves de agente / carteras y metas."""

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class InsuranceClaimType(models.Model):
    _name = "insurance.claim.type"
    _description = "Tipo de siniestro"
    _order = "sequence, name"

    name = fields.Char(string="Tipo de siniestro", required=True, translate=True)
    code = fields.Char(string="Siglas")
    sequence = fields.Integer(default=10)
    ramo_id = fields.Many2one("insurance.policy.type", string="Ramo")
    claim_kind = fields.Selection(
        [
            ("gmm", "Gastos médicos"),
            ("accident", "Accidente"),
            ("auto", "Automotriz"),
            ("life", "Vida"),
            ("other", "Otro"),
        ],
        string="Grupo de documentos",
        default="other",
        required=True,
        help="Define qué documentos se piden automáticamente para este tipo de siniestro.",
    )
    description = fields.Text(string="Descripción")
    active = fields.Boolean(default=True)


class InsuranceWithdrawalType(models.Model):
    _name = "insurance.withdrawal.type"
    _description = "Tipo de retiro"
    _order = "sequence, name"

    name = fields.Char(string="Tipo de retiro", required=True, translate=True)
    code = fields.Char(string="Siglas")
    sequence = fields.Integer(default=10)
    description = fields.Text(string="Descripción")
    active = fields.Boolean(default=True)


class InsurancePolicyWithdrawal(models.Model):
    _name = "insurance.policy.withdrawal"
    _description = "Retiro / rescate de póliza"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    policy_id = fields.Many2one("insurance.policy", string="Póliza", required=True, ondelete="cascade", index=True)
    partner_id = fields.Many2one(related="policy_id.partner_id", store=True, string="Contratante")
    withdrawal_type_id = fields.Many2one("insurance.withdrawal.type", string="Tipo de retiro", required=True)
    date = fields.Date(string="Fecha de solicitud", required=True, default=fields.Date.context_today)
    paid_date = fields.Date(string="Fecha de pago")
    amount = fields.Monetary(string="Importe", currency_field="currency_id")
    currency_id = fields.Many2one(related="policy_id.currency_id")
    state = fields.Selection(
        [
            ("requested", "Solicitado"),
            ("in_process", "En trámite"),
            ("paid", "Pagado"),
            ("rejected", "Rechazado"),
        ],
        string="Estado",
        default="requested",
        required=True,
        tracking=True,
    )
    note = fields.Char(string="Observaciones")


class InsuranceProfession(models.Model):
    _name = "insurance.profession"
    _description = "Profesión (con día de felicitación)"
    _order = "name"

    name = fields.Char(string="Profesión", required=True, translate=True)
    celebration_day = fields.Integer(string="Día")
    celebration_month = fields.Selection(
        [(str(m), name) for m, name in enumerate(
            ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
             "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"], start=1)],
        string="Mes",
    )
    celebration_label = fields.Char(string="Celebración", help="Ej. Día del Médico.")
    partner_count = fields.Integer(compute="_compute_partner_count", string="Clientes")
    active = fields.Boolean(default=True)

    _name_unique = models.Constraint("UNIQUE(name)", "La profesión ya existe.")

    @api.constrains("celebration_day", "celebration_month")
    def _check_day(self):
        for rec in self:
            if rec.celebration_day and not (1 <= rec.celebration_day <= 31):
                raise ValidationError(_("El día debe estar entre 1 y 31."))

    def _compute_partner_count(self):
        data = self.env["res.partner"]._read_group(
            [("insurance_profession_id", "in", self.ids)], ["insurance_profession_id"], ["__count"]
        )
        counts = {prof.id: count for prof, count in data}
        for rec in self:
            rec.partner_count = counts.get(rec.id, 0)

    def is_celebrated_on(self, day):
        self.ensure_one()
        return bool(
            self.celebration_day and self.celebration_month
            and int(self.celebration_month) == day.month and self.celebration_day == day.day
        )


class InsuranceAgentKey(models.Model):
    """Clave de agente ante una aseguradora; agrupa su cartera de clientes."""

    _name = "insurance.agent.key"
    _description = "Clave de agente / cartera"
    _order = "insurance_company_id, name"

    name = fields.Char(string="Clave", required=True, index=True)
    portfolio_name = fields.Char(string="Nombre de la cartera")
    agent_id = fields.Many2one("insurance.agent", string="Asesor / agente", required=True, ondelete="cascade")
    insurance_company_id = fields.Many2one("insurance.company", string="Aseguradora", required=True)
    date_start = fields.Date(string="Vigente desde")
    active = fields.Boolean(default=True)
    policy_ids = fields.One2many("insurance.policy", "agent_key_id", string="Pólizas")
    policy_count = fields.Integer(compute="_compute_portfolio", string="N.º de pólizas")
    customer_count = fields.Integer(compute="_compute_portfolio", string="Clientes")

    _key_unique = models.Constraint(
        "UNIQUE(name, insurance_company_id)",
        "La clave ya está registrada para esa aseguradora.",
    )

    @api.depends("name", "insurance_company_id", "portfolio_name")
    def _compute_display_name(self):
        for key in self:
            label = "%s · %s" % (key.name, key.insurance_company_id.code or key.insurance_company_id.name or "")
            if key.portfolio_name:
                label += " (%s)" % key.portfolio_name
            key.display_name = label

    def _compute_portfolio(self):
        for key in self:
            policies = key.policy_ids.filtered(lambda p: p.state in ("confirmed", "expired"))
            key.policy_count = len(policies)
            key.customer_count = len(policies.mapped("partner_id"))

    def action_open_portfolio(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Cartera %s") % self.display_name,
            "res_model": "insurance.policy",
            "view_mode": "list,kanban,form,pivot",
            "domain": [("agent_key_id", "=", self.id)],
            "context": {"default_agent_key_id": self.id, "default_agent_id": self.agent_id.id},
        }


class InsuranceGoal(models.Model):
    """Metas mensuales/anuales: ingresos (comisiones), prima vendida y pólizas nuevas."""

    _name = "insurance.goal"
    _description = "Meta de ventas e ingresos"
    _order = "year desc, month"

    name = fields.Char(compute="_compute_name", store=True)
    year = fields.Integer(string="Año", required=True, default=lambda self: fields.Date.context_today(self).year)
    month = fields.Selection(
        [(str(m), name) for m, name in enumerate(
            ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
             "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"], start=1)],
        string="Mes",
        help="Vacío = meta anual.",
    )
    agent_id = fields.Many2one("insurance.agent", string="Asesor", help="Vacío = meta de toda la agencia.")
    income_target = fields.Monetary(string="Meta de ingresos (comisiones)", currency_field="currency_id")
    premium_target = fields.Monetary(string="Meta de prima vendida", currency_field="currency_id")
    new_policies_target = fields.Integer(string="Meta de pólizas nuevas")
    new_customers_target = fields.Integer(string="Meta de clientes nuevos")
    currency_id = fields.Many2one(
        "res.currency", required=True, default=lambda self: self.env.company.currency_id,
    )
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)

    _period_unique = models.Constraint(
        "UNIQUE(year, month, agent_id, company_id)",
        "Ya existe una meta para ese periodo y asesor.",
    )

    @api.depends("year", "month", "agent_id")
    def _compute_name(self):
        months = dict(self._fields["month"].selection)
        for goal in self:
            period = "%s %s" % (months.get(goal.month, _("Anual")), goal.year)
            goal.name = "%s — %s" % (period, goal.agent_id.name) if goal.agent_id else period

    @api.model
    def _get_target(self, year, month=None, field="income_target"):
        """Meta de agencia para el periodo. Si no hay meta mensual, usa la anual / 12."""
        domain = [("year", "=", year), ("agent_id", "=", False), ("company_id", "=", self.env.company.id)]
        if month:
            goal = self.search(domain + [("month", "=", str(month))], limit=1)
            if goal:
                return goal[field] or 0.0
            annual = self.search(domain + [("month", "=", False)], limit=1)
            return (annual[field] or 0.0) / 12.0 if annual else 0.0
        annual = self.search(domain + [("month", "=", False)], limit=1)
        if annual:
            return annual[field] or 0.0
        return sum(self.search(domain + [("month", "!=", False)]).mapped(field))
