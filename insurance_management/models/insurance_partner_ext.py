# -*- coding: utf-8 -*-
"""Cliente (res.partner), asegurados, cuotas y siniestros: datos adicionales,
ventas cruzadas, felicitaciones y estado de cobranza."""

from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

COLLECTION_STATUS = [
    ("due_soon", "Por vencer"),
    ("overdue", "Vencida"),
    ("paid", "Pagada"),
    ("scheduled", "Programada"),
    ("cancelled", "Cancelada"),
]


def _next_anniversary(day_month_date, today):
    """Próxima fecha (>= hoy) con el mismo día y mes."""
    if not day_month_date:
        return False
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, day_month_date.month, day_month_date.day)
        except ValueError:  # 29 de febrero
            candidate = date(year, 3, 1)
        if candidate >= today:
            return candidate
    return False


class ResPartner(models.Model):
    _inherit = "res.partner"

    insurance_profession_id = fields.Many2one("insurance.profession", string="Profesión")
    insurance_zone = fields.Char(string="Zona", help="Zona o colonia de residencia para segmentar la cartera.")
    insurance_next_birthday = fields.Date(string="Próximo cumpleaños", compute="_compute_insurance_dates")
    insurance_age = fields.Integer(string="Edad", compute="_compute_insurance_dates")
    insurance_next_profession_day = fields.Date(string="Próximo día de su profesión", compute="_compute_insurance_dates")
    insurance_last_birthday_greeting = fields.Date(string="Última felicitación de cumpleaños", copy=False)
    insurance_last_profession_greeting = fields.Date(string="Última felicitación de profesión", copy=False)
    # Ventas cruzadas (se actualizan al instante con cada cambio de póliza)
    insurance_active_policy_count = fields.Integer(
        string="Pólizas activas",
        compute="_compute_cross_sell",
        store=True,
    )
    insurance_ramo_ids = fields.Many2many(
        "insurance.policy.type",
        "res_partner_insurance_ramo_rel",
        "partner_id",
        "ramo_id",
        string="Ramos contratados",
        compute="_compute_cross_sell",
        store=True,
    )
    insurance_ramo_count = fields.Integer(string="N.º de ramos", compute="_compute_cross_sell", store=True)
    insurance_missing_ramo_ids = fields.Many2many(
        "insurance.policy.type",
        string="Ramos por ofrecer",
        compute="_compute_missing_ramos",
    )
    insurance_premium_total = fields.Monetary(
        string="Prima emitida (moneda empresa)",
        compute="_compute_cross_sell",
        store=True,
        currency_field="insurance_company_currency_id",
    )
    insurance_company_currency_id = fields.Many2one(
        "res.currency", compute="_compute_insurance_company_currency",
    )
    insurance_pending_installment_ids = fields.One2many(
        "insurance.installment", compute="_compute_pending_installments", string="Cuotas por cobrar",
    )
    insurance_amount_due = fields.Monetary(
        string="Saldo por cobrar",
        compute="_compute_pending_installments",
        currency_field="insurance_company_currency_id",
    )
    insurance_task_ids = fields.One2many("insurance.task", "partner_id", string="Tareas")
    insurance_attention_level = fields.Selection(
        [("ok", "Al día"), ("warning", "Requiere seguimiento"), ("danger", "Atención urgente")],
        string="Atención",
        compute="_compute_insurance_attention",
    )

    def _compute_insurance_company_currency(self):
        for partner in self:
            partner.insurance_company_currency_id = self.env.company.currency_id

    @api.depends("mx_birth_date", "insurance_profession_id.celebration_day", "insurance_profession_id.celebration_month")
    def _compute_insurance_dates(self):
        today = fields.Date.context_today(self)
        for partner in self:
            birth = partner.mx_birth_date
            partner.insurance_next_birthday = _next_anniversary(birth, today)
            partner.insurance_age = relativedelta(today, birth).years if birth else 0
            prof = partner.insurance_profession_id
            if prof.celebration_day and prof.celebration_month:
                try:
                    ref = date(2000, int(prof.celebration_month), prof.celebration_day)
                except ValueError:
                    ref = False
                partner.insurance_next_profession_day = _next_anniversary(ref, today)
            else:
                partner.insurance_next_profession_day = False

    @api.depends(
        "insurance_policy_ids.state",
        "insurance_policy_ids.policy_type_id",
        "insurance_policy_ids.policy_amount",
        "insurance_policy_ids.currency_id",
    )
    def _compute_cross_sell(self):
        company = self.env.company
        today = fields.Date.context_today(self)
        for partner in self:
            active = partner.insurance_policy_ids.filtered(lambda p: p.state in ("confirmed", "expired"))
            partner.insurance_active_policy_count = len(active)
            partner.insurance_ramo_ids = active.mapped("policy_type_id")
            partner.insurance_ramo_count = len(partner.insurance_ramo_ids)
            total = 0.0
            for policy in active:
                currency = policy.currency_id or company.currency_id
                total += currency._convert(policy.policy_amount, company.currency_id, company, today) \
                    if currency != company.currency_id else policy.policy_amount
            partner.insurance_premium_total = total

    def _compute_missing_ramos(self):
        all_ramos = self.env["insurance.policy.type"].search([])
        for partner in self:
            partner.insurance_missing_ramo_ids = all_ramos - partner.insurance_ramo_ids

    def _compute_pending_installments(self):
        company = self.env.company
        today = fields.Date.context_today(self)
        for partner in self:
            lines = self.env["insurance.installment"].search([
                ("policy_id.partner_id", "=", partner.id),
                ("state", "in", ["pending", "invoiced", "overdue"]),
            ])
            partner.insurance_pending_installment_ids = lines
            total = 0.0
            for line in lines:
                due = max((line.amount or 0.0) - (line.paid_amount or 0.0), 0.0)
                currency = line.currency_id or company.currency_id
                total += currency._convert(due, company.currency_id, company, today) \
                    if currency != company.currency_id else due
            partner.insurance_amount_due = total

    def _compute_insurance_attention(self):
        for partner in self:
            levels = set(partner.insurance_policy_ids.mapped("attention_level"))
            partner.insurance_attention_level = (
                "danger" if "danger" in levels else "warning" if "warning" in levels else "ok"
            )

    # ------------------------------------------------------------------
    def action_send_insurance_message(self):
        return self.env["insurance.message.wizard"].action_open(self)

    def action_register_insurance_payment(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Realizar pago"),
            "res_model": "insurance.payment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_partner_id": self.id},
        }

    def action_open_cross_sell_policies(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Pólizas de %s") % self.name,
            "res_model": "insurance.policy",
            "view_mode": "list,kanban,form",
            "domain": ["|", ("partner_id", "=", self.id), ("insured_partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }

    def action_create_cross_sell_lead(self):
        self.ensure_one()
        missing = self.insurance_missing_ramo_ids
        return {
            "type": "ir.actions.act_window",
            "name": _("Oportunidad de venta cruzada"),
            "res_model": "crm.lead",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_type": "opportunity",
                "default_is_insurance_opportunity": True,
                "default_partner_id": self.id,
                "default_name": _("Venta cruzada — %s") % self.name,
                "default_insurance_policy_type_id": missing[:1].id if missing else False,
                "default_insurance_origin": "cross_sell",
            },
        }

    @api.model
    def _cron_greetings(self):
        """Felicitaciones automáticas de cumpleaños y día de la profesión."""
        params = self.env["ir.config_parameter"].sudo()
        today = fields.Date.context_today(self)
        customers = self.search([("is_insurance_customer", "=", True), ("email", "!=", False)])
        if params.get_param("insurance_management.auto_birthday"):
            template = self.env.ref("insurance_management.mail_template_partner_birthday", raise_if_not_found=False)
            for partner in customers.filtered(
                lambda p: p.mx_birth_date
                and (p.mx_birth_date.month, p.mx_birth_date.day) == (today.month, today.day)
                and p.insurance_last_birthday_greeting != today
            ):
                if template:
                    partner.message_post_with_source(template, message_type="comment", subtype_xmlid="mail.mt_comment")
                partner.insurance_last_birthday_greeting = today
        if params.get_param("insurance_management.auto_profession"):
            template = self.env.ref("insurance_management.mail_template_partner_profession", raise_if_not_found=False)
            for partner in customers.filtered(
                lambda p: p.insurance_profession_id
                and p.insurance_profession_id.is_celebrated_on(today)
                and p.insurance_last_profession_greeting != today
            ):
                if template:
                    partner.message_post_with_source(template, message_type="comment", subtype_xmlid="mail.mt_comment")
                partner.insurance_last_profession_greeting = today


class InsurancePolicyMember(models.Model):
    _inherit = "insurance.policy.member"

    relationship = fields.Selection(
        selection_add=[
            ("sibling", "Hermano(a)"),
            ("grandchild", "Nieto(a)"),
        ],
        ondelete={"sibling": "set default", "grandchild": "set default"},
    )
    entry_date = fields.Date(
        string="Fecha de ingreso",
        default=fields.Date.context_today,
        help="Fecha en que la persona entra a la póliza (puede ser posterior al inicio).",
    )
    exit_date = fields.Date(string="Fecha de baja")
    zone = fields.Char(string="Zona")
    age = fields.Integer(string="Edad", compute="_compute_age")
    policy_state = fields.Selection(related="policy_id.state")

    @api.depends("birth_date")
    def _compute_age(self):
        today = fields.Date.context_today(self)
        for member in self:
            member.age = relativedelta(today, member.birth_date).years if member.birth_date else 0

    @api.model_create_multi
    def create(self, vals_list):
        members = super().create(vals_list)
        for member in members.filtered(lambda m: m.policy_id.state == "confirmed"):
            member.policy_id.message_post(body=_(
                "Alta de asegurado posterior al inicio: %(name)s (%(rel)s), ingreso %(date)s."
            ) % {
                "name": member.name or "",
                "rel": dict(member._fields["relationship"]._description_selection(self.env)).get(member.relationship),
                "date": member.entry_date or "",
            })
        return members


class InsuranceInstallment(models.Model):
    _inherit = "insurance.installment"

    collection_status = fields.Selection(
        COLLECTION_STATUS,
        string="Estatus de cobro",
        compute="_compute_collection_status",
        store=True,
        index=True,
        group_expand="_expand_collection_status",
    )
    policy_type_id = fields.Many2one(related="policy_id.policy_type_id", store=True, string="Ramo")
    offer_id = fields.Many2one(related="policy_id.offer_id", store=True, string="Oferta")
    insurance_company_id = fields.Many2one(
        related="policy_id.insurance_company_id", store=True, string="Aseguradora",
    )
    agent_id = fields.Many2one(related="policy_id.agent_id", store=True, string="Asesor")
    partner_phone = fields.Char(related="partner_id.phone", string="Teléfono")
    partner_email = fields.Char(related="partner_id.email", string="Correo")
    balance = fields.Monetary(string="Saldo", compute="_compute_balance", currency_field="currency_id")
    last_reminder_date = fields.Date(string="Último recordatorio", copy=False)

    def _expand_collection_status(self, states, domain):
        # El tablero de cobranza siempre muestra las columnas Por vencer / Vencida / Pagada
        return ["due_soon", "overdue", "paid"] + [s for s in states if s not in ("due_soon", "overdue", "paid")]

    @api.depends("state", "due_date", "paid_amount", "amount")
    def _compute_collection_status(self):
        today = fields.Date.context_today(self)
        days = int(self.env["ir.config_parameter"].sudo().get_param("insurance_management.due_soon_days", 15))
        for line in self:
            if line.state == "cancelled":
                line.collection_status = "cancelled"
            elif line.state == "paid":
                line.collection_status = "paid"
            elif line.state == "overdue" or (line.due_date and line.due_date < today):
                line.collection_status = "overdue"
            elif line.due_date and line.due_date <= today + relativedelta(days=days):
                line.collection_status = "due_soon"
            else:
                line.collection_status = "scheduled"

    @api.depends("amount", "paid_amount")
    def _compute_balance(self):
        for line in self:
            line.balance = max((line.amount or 0.0) - (line.paid_amount or 0.0), 0.0)

    def action_send_message(self):
        return self.env["insurance.message.wizard"].action_open(self)

    def action_open_payment_wizard(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Registrar pago"),
            "res_model": "insurance.payment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_partner_id": self[:1].partner_id.id,
                "default_policy_id": self[:1].policy_id.id if len(self.mapped("policy_id")) == 1 else False,
                "default_installment_ids": [(6, 0, self.filtered(lambda l: l.state not in ("paid", "cancelled")).ids)],
            },
        }

    @api.model
    def _cron_refresh_collection_status(self):
        lines = self.search([("state", "in", ["pending", "invoiced", "overdue"])])
        lines._compute_collection_status()


class InsuranceClaim(models.Model):
    _inherit = "insurance.claim"

    claim_type_id = fields.Many2one(
        "insurance.claim.type",
        string="Tipo de siniestro",
        tracking=True,
        domain="['|', ('ramo_id', '=', False), ('ramo_id', '=', policy_type_id)]",
    )
    policy_type_id = fields.Many2one(related="policy_id.policy_type_id", string="Ramo")
    offer_id = fields.Many2one(related="policy_id.offer_id", string="Oferta")
    policy_insured_amount = fields.Monetary(related="policy_id.insured_amount", string="Suma asegurada")
    policy_start = fields.Date(related="policy_id.coverage_start_date", string="Inicio de vigencia")
    policy_end = fields.Date(related="policy_id.coverage_end_date", string="Fin de vigencia")
    policy_state = fields.Selection(related="policy_id.state", string="Estado de la póliza")
    policy_overdue_count = fields.Integer(related="policy_id.overdue_count", string="Cuotas vencidas")
    member_relationship = fields.Selection(related="member_id.relationship", string="Parentesco")
    member_birth_date = fields.Date(related="member_id.birth_date", string="Nacimiento del afectado")
    occurrence_place = fields.Char(string="Lugar del evento")
    contact_phone = fields.Char(string="Teléfono de contacto")

    @api.depends("policy_id")
    def _compute_partner_from_policy(self):
        for claim in self:
            if claim.policy_id and claim.partner_id not in (
                claim.policy_id.partner_id | claim.policy_id.insured_partner_id
            ):
                claim.partner_id = claim.policy_id.partner_id
            elif not claim.partner_id:
                claim.partner_id = claim.policy_id.partner_id

    @api.onchange("partner_id")
    def _onchange_partner_policy(self):
        if not self.partner_id:
            return
        policies = self.env["insurance.policy"].search([
            "|", ("partner_id", "=", self.partner_id.id), ("insured_partner_id", "=", self.partner_id.id),
            ("state", "in", ["confirmed", "expired"]),
        ])
        if self.policy_id not in policies:
            self.policy_id = policies[:1] if len(policies) == 1 else False
        if not self.contact_phone:
            self.contact_phone = self.partner_id._insurance_phone()

    def action_send_message(self):
        return self.env["insurance.message.wizard"].action_open(self)

    @api.onchange("claim_type_id")
    def _onchange_claim_type_id(self):
        if self.claim_type_id:
            self.claim_kind = self.claim_type_id.claim_kind
