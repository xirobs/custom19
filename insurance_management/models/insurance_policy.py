# -*- coding: utf-8 -*-

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


PREMIUM_PERIODS = {
    "monthly": 1,
    "quarterly": 3,
    "semiannual": 6,
    "yearly": 12,
}

# Monedas permitidas en pólizas (res.currency.name)
INSURANCE_CURRENCIES = ("MXN", "USD", "UDI")


class InsurancePolicy(models.Model):
    _name = "insurance.policy"
    _description = "Venta / contratación de póliza"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "issue_date desc, id desc"

    name = fields.Char(
        string="Número de póliza",
        required=True,
        copy=False,
        default=lambda self: _("Nuevo"),
        tracking=True,
        index=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Contratante",
        required=True,
        tracking=True,
        index=True,
    )
    scheme_id = fields.Many2one(
        "insurance.scheme",
        string="Esquema de producto",
        tracking=True,
        domain="[('policy_type_id', '=', policy_type_id)]",
        help="Esquema técnico del catálogo: numeración de pólizas, coberturas, "
             "producto e impuesto de facturación. Se propone al elegir el ramo.",
    )
    # --- Ramo → Oferta → Sub-oferta ---------------------------------------
    policy_type_id = fields.Many2one(
        "insurance.policy.type",
        string="Ramo",
        tracking=True,
        index=True,
    )
    offer_id = fields.Many2one(
        "insurance.offer",
        string="Oferta",
        tracking=True,
        domain="[('ramo_id', '=', policy_type_id), ('parent_id', '=', False)]",
    )
    offer_free_text = fields.Boolean(related="offer_id.sub_offer_free_text")
    offer_has_children = fields.Boolean(compute="_compute_offer_has_children")
    sub_offer_id = fields.Many2one(
        "insurance.offer",
        string="Sub-oferta",
        tracking=True,
        domain="[('parent_id', '=', offer_id)]",
    )
    sub_offer_text = fields.Char(string="Sub-oferta (texto libre)", tracking=True)
    insurance_company_id = fields.Many2one(
        "insurance.company",
        string="Compañía de seguros",
        tracking=True,
    )
    # Se conserva para el tablero por sucursal; ya no se muestra en el formulario.
    branch_id = fields.Many2one(
        "insurance.branch",
        string="Sucursal",
        compute="_compute_branch_id",
        store=True,
        readonly=False,
    )
    agent_id = fields.Many2one("insurance.agent", string="Nombre del agente", tracking=True)
    payment_term_id = fields.Many2one("account.payment.term", string="Plazo de pago")
    duration_months = fields.Integer(
        string="Duración (meses)",
        default=12,
        required=True,
        help="La vigencia habitual es de 12 meses (1 año).",
    )
    duration_years = fields.Integer(
        string="Duración en años",
        compute="_compute_duration_years",
        store=True,
    )
    display_title = fields.Char(string="Título", compute="_compute_display_title", store=True)
    description = fields.Html(string="Descripción")
    special_note = fields.Html(string="Notas especiales")
    commission_rate = fields.Float(string="Comisión del agente (%)")
    commission_amount = fields.Monetary(
        string="Comisión",
        compute="_compute_commission_amount",
        store=True,
        currency_field="currency_id",
    )
    commission_move_id = fields.Many2one(
        "account.move",
        string="Factura de comisión",
        copy=False,
    )
    project_id = fields.Many2one("project.project", string="Proyecto de seguimiento")
    calendar_event_id = fields.Many2one("calendar.event", string="Evento de vigencia", copy=False)
    amount_to_invoice = fields.Monetary(
        string="Pendiente de facturar",
        compute="_compute_collection_stats",
        store=True,
        currency_field="currency_id",
    )
    feature_ids = fields.One2many("insurance.policy.feature", "policy_id", string="Características", copy=True)
    coverage_start_date = fields.Date(
        string="Inicio de cobertura",
        default=fields.Date.context_today,
        tracking=True,
    )
    coverage_end_date = fields.Date(
        string="Fin de cobertura",
        compute="_compute_coverage_end_date",
        store=True,
        readonly=False,
    )
    issue_date = fields.Date(
        related="coverage_start_date",
        store=True,
        string="Fecha de inicio",
    )
    emission_date = fields.Date(
        string="Fecha de emisión",
        compute="_compute_emission_date",
        store=True,
        readonly=False,
        tracking=True,
        help="Fecha de emisión de la póliza. A partir de ella corren los días de gracia del primer recibo.",
    )
    end_date = fields.Date(
        related="coverage_end_date",
        store=True,
        string="Fecha final",
    )
    next_growth_date = fields.Date(
        string="Próxima fecha de crecimiento",
        compute="_compute_next_growth_date",
        store=True,
        readonly=False,
    )
    insured_amount = fields.Monetary(
        string="Suma asegurada",
        currency_field="currency_id",
        tracking=True,
    )
    policy_amount = fields.Monetary(
        string="Prima emitida",
        currency_field="currency_id",
        required=True,
        tracking=True,
        help="Valor completo de la póliza por el año (prima total anual emitida).",
    )
    premium_type = fields.Selection(
        [
            ("monthly", "Mensual"),
            ("quarterly", "Trimestral"),
            ("semiannual", "Semestral"),
            ("yearly", "Anual"),
        ],
        string="Forma de pago",
        default="monthly",
        required=True,
        tracking=True,
    )
    installment_amount = fields.Monetary(
        string="Importe de la cuota",
        currency_field="currency_id",
        tracking=True,
        help="Se captura manualmente. Con este importe se genera la lista de cuotas.",
    )
    installment_count = fields.Integer(
        string="N.º de cuotas",
        compute="_compute_installment_count",
        store=True,
    )
    installment_total = fields.Monetary(
        string="Total de cuotas",
        compute="_compute_installment_total",
        currency_field="currency_id",
        help="Importe de la cuota × número de cuotas. Compárelo con la prima emitida.",
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Moneda",
        default=lambda self: self.env.company.currency_id,
        domain="[('name', 'in', ('MXN', 'USD', 'UDI'))]",
        tracking=True,
    )
    # --- Datos de la carátula de la aseguradora ------------------------------
    plan_basic = fields.Char(string="Plan básico", tracking=True)
    insured_partner_id = fields.Many2one("res.partner", string="Asegurado", tracking=True)
    residence = fields.Char(string="Residencia", help="Zona de residencia (ciudad, estado).")
    policy_kind = fields.Char(string="Tipo de póliza", help="Tal como aparece en la carátula. Ej. NORMAL.")
    maturity_date = fields.Date(
        string="Fecha de vencimiento",
        tracking=True,
        help="Vencimiento del contrato según la carátula (fin del plazo del seguro).",
    )
    insured_birth_date = fields.Date(string="Fecha de nacimiento")
    insured_age = fields.Integer(string="Edad")
    insured_gender = fields.Selection(
        [("female", "Femenino"), ("male", "Masculino")],
        string="Sexo",
    )
    insured_address = fields.Text(string="Domicilio")
    settlement_option = fields.Char(string="Opción de liquidación")
    benefit_ids = fields.One2many(
        "insurance.policy.benefit",
        "policy_id",
        string="Beneficios",
        copy=True,
    )
    benefit_premium_total = fields.Monetary(
        string="Prima total de beneficios",
        compute="_compute_benefit_premium_total",
        currency_field="currency_id",
    )
    caratula_document_id = fields.Many2one(
        "insurance.document",
        string="Carátula en expediente",
        copy=False,
    )
    # --- Conducto de cobro --------------------------------------------------
    collection_channel = fields.Selection(
        [
            ("direct", "Directo"),
            ("domiciled", "Domiciliado"),
        ],
        string="Conducto de cobro",
        default="direct",
        required=True,
        tracking=True,
        help="Directo: el cliente paga manualmente (liga, banco o portal). "
             "Domiciliado: cargo automático a su cuenta o tarjeta.",
    )
    direct_payment_method = fields.Selection(
        [
            ("link", "Liga de pago"),
            ("bank", "Banco"),
            ("portal", "Portal de la aseguradora"),
        ],
        string="Medio de pago directo",
        tracking=True,
    )
    domiciled_bank_account_id = fields.Many2one(
        "res.partner.bank",
        string="Cuenta / tarjeta domiciliada",
        domain="[('partner_id', '=', partner_id)]",
    )
    # --- Calendario de cobranza ----------------------------------------------
    grace_days = fields.Integer(
        string="Días de gracia",
        default=30,
        help="Días desde la emisión del recibo para pagar.",
    )
    collection_date_mode = fields.Selection(
        [
            ("grace_end", "Al terminar los días de gracia"),
            ("fixed", "Día fijo solicitado por el cliente"),
        ],
        string="Fecha de cobro",
        default="grace_end",
        required=True,
        tracking=True,
    )
    collection_day = fields.Integer(
        string="Día fijo de cobro",
        help="Día del mes en que el cliente pidió que se le cobre. "
             "Siempre debe quedar dentro de los días de gracia.",
    )
    extension_days = fields.Integer(
        string="Días de prórroga",
        default=15,
        help="Días de prórroga que corren a partir de la fecha de cobro.",
    )
    auto_charge_days = fields.Integer(
        string="Días de cobro automático",
        default=12,
        help="Dentro de la prórroga, días en que el sistema intenta el cargo automático (domiciliado).",
    )
    protection_days = fields.Integer(
        string="Días de amparo",
        default=5,
        help="Días posteriores a la prórroga en que el cliente debe pagar directo a la aseguradora.",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Compañía",
        default=lambda self: self.env.company,
        required=True,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Responsable",
        default=lambda self: self.env.user,
        tracking=True,
    )
    lead_id = fields.Many2one("crm.lead", string="Oportunidad CRM")
    coverage_mode = fields.Selection(
        [
            ("individual", "Individual"),
            ("family", "Familiar"),
        ],
        string="Contratación",
        default="individual",
        required=True,
        tracking=True,
    )
    member_ids = fields.One2many(
        "insurance.policy.member",
        "policy_id",
        string="Asegurados",
        copy=True,
    )
    member_count = fields.Integer(compute="_compute_member_count")
    origin_policy_id = fields.Many2one(
        "insurance.policy",
        string="Póliza anterior (renovación)",
        copy=False,
        index=True,
    )
    renewal_policy_ids = fields.One2many(
        "insurance.policy",
        "origin_policy_id",
        string="Renovaciones",
    )
    renewal_count = fields.Integer(compute="_compute_renewal_count")
    auto_renew = fields.Boolean(string="Renovar al vencimiento", default=True)
    lifecycle_stage = fields.Selection(
        [
            ("prospecting", "Prospección"),
            ("onboarding", "Contratación"),
            ("active", "En servicio"),
            ("claim", "Atención de siniestro"),
            ("renewal", "Renovación"),
            ("closed", "Terminado"),
        ],
        string="Etapa de seguimiento",
        compute="_compute_lifecycle_stage",
        store=True,
        index=True,
    )
    claim_ids = fields.One2many("insurance.claim", "policy_id", string="Siniestros")
    claim_count = fields.Integer(compute="_compute_claim_stats", store=True)
    claim_open_count = fields.Integer(compute="_compute_claim_stats", store=True)
    coverage_consumed = fields.Monetary(
        compute="_compute_coverage_usage",
        store=True,
        currency_field="currency_id",
        string="Cobertura consumida",
    )
    coverage_remaining = fields.Monetary(
        compute="_compute_coverage_usage",
        store=True,
        currency_field="currency_id",
        string="Cobertura disponible",
    )
    invoice_mode = fields.Selection(
        [
            ("global_ppd", "CFDI 4.0 global (PPD) + complemento de pagos"),
            ("per_installment", "CFDI por cada cuota"),
        ],
        string="Esquema de facturación SAT",
        default="global_ppd",
        required=True,
        tracking=True,
    )
    cfdi_usage = fields.Selection(
        [
            ("G03", "G03 — Gastos en general"),
            ("D07", "D07 — Primas de seguros de gastos médicos"),
            ("S01", "S01 — Sin efectos fiscales"),
        ],
        string="Uso CFDI",
        compute="_compute_cfdi_usage",
        store=True,
        readonly=False,
    )
    cfdi_payment_method = fields.Selection(
        [
            ("PUE", "PUE — Una sola exhibición"),
            ("PPD", "PPD — Parcialidades o diferido"),
        ],
        string="Método de pago SAT",
        default="PPD",
    )
    cfdi_payment_form = fields.Selection(
        [
            ("01", "01 — Efectivo"),
            ("03", "03 — Transferencia"),
            ("04", "04 — Tarjeta de crédito"),
            ("28", "28 — Tarjeta de débito"),
            ("99", "99 — Por definir"),
        ],
        string="Forma de pago SAT",
        default="99",
    )
    prima_neta = fields.Monetary(string="Prima neta", currency_field="currency_id")
    derechos = fields.Monetary(string="Derechos de póliza", currency_field="currency_id")
    recargos = fields.Monetary(string="Recargos", currency_field="currency_id")
    cfdi_global_move_id = fields.Many2one(
        "account.move",
        string="CFDI global de prima",
        copy=False,
    )
    subscription_id = fields.Many2one(
        "sale.order",
        string="Suscripción",
        copy=False,
        help="Pedido de suscripción de Odoo para la facturación recurrente de la prima.",
    )
    state = fields.Selection(
        [
            ("draft", "Borrador"),
            ("confirmed", "Activo"),
            ("expired", "Vencido"),
            ("done", "Cerrado"),
            ("cancelled", "Cancelado"),
        ],
        string="Estado",
        default="draft",
        tracking=True,
        required=True,
        copy=False,
        index=True,
    )
    note = fields.Text(string="Notas")
    installment_ids = fields.One2many(
        "insurance.installment",
        "policy_id",
        string="Cuotas de prima",
        copy=False,
    )
    installment_to_pay_ids = fields.Many2many(
        "insurance.installment",
        compute="_compute_installment_history",
        string="Cuotas por pagar",
    )
    installment_paid_ids = fields.Many2many(
        "insurance.installment",
        compute="_compute_installment_history",
        string="Cuotas pagadas",
    )
    coverage_ids = fields.One2many(
        "insurance.policy.coverage",
        "policy_id",
        string="Coberturas",
        copy=True,
    )
    document_ids = fields.One2many(
        "insurance.document",
        "policy_id",
        string="Documentación",
    )
    growth_ids = fields.One2many(
        "insurance.policy.growth",
        "policy_id",
        string="Crecimientos",
    )
    invoice_ids = fields.One2many("account.move", "insurance_policy_id", string="Facturas")
    installment_count_done = fields.Integer(compute="_compute_collection_stats", store=True)
    amount_invoiced = fields.Monetary(
        compute="_compute_collection_stats",
        store=True,
        currency_field="currency_id",
    )
    amount_paid = fields.Monetary(
        string="Prima pagada",
        compute="_compute_collection_stats",
        store=True,
        currency_field="currency_id",
        help="Suma de todos los pagos registrados por el cliente (pago completo o cuotas).",
    )
    amount_due = fields.Monetary(
        compute="_compute_collection_stats",
        store=True,
        currency_field="currency_id",
    )
    overdue_count = fields.Integer(compute="_compute_collection_stats", store=True)
    document_pending_count = fields.Integer(compute="_compute_document_stats", store=True)
    document_signed_count = fields.Integer(compute="_compute_document_stats", store=True)
    invoice_count = fields.Integer(compute="_compute_invoice_count", store=True)

    @api.depends(
        "partner_id",
        "scheme_id",
        "policy_type_id",
        "offer_id",
        "sub_offer_id",
        "sub_offer_text",
    )
    def _compute_display_title(self):
        for policy in self:
            holder = policy.partner_id.name or _("Titular")
            parts = [
                policy.policy_type_id.name,
                policy.offer_id.name,
                policy.sub_offer_id.name or policy.sub_offer_text,
            ]
            offer = " / ".join(p for p in parts if p) or policy.scheme_id.name or _("Seguro")
            policy.display_title = "%s — %s" % (holder, offer)

    @api.depends("benefit_ids.premium")
    def _compute_benefit_premium_total(self):
        for policy in self:
            policy.benefit_premium_total = sum(policy.benefit_ids.mapped("premium"))

    def action_open_caratula_import(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Cargar carátula PDF"),
            "res_model": "insurance.policy.pdf.import",
            "view_mode": "form",
            "target": "new",
            "context": {"default_policy_id": self.id},
        }

    @api.depends("offer_id")
    def _compute_offer_has_children(self):
        for policy in self:
            policy.offer_has_children = bool(policy.offer_id.child_ids)

    @api.depends("agent_id")
    def _compute_branch_id(self):
        for policy in self:
            if policy.agent_id.branch_id:
                policy.branch_id = policy.agent_id.branch_id
            else:
                policy.branch_id = policy.branch_id

    @api.depends("coverage_start_date")
    def _compute_emission_date(self):
        for policy in self:
            if not policy.emission_date:
                policy.emission_date = policy.coverage_start_date
            else:
                policy.emission_date = policy.emission_date

    @api.depends("duration_months")
    def _compute_duration_years(self):
        for policy in self:
            policy.duration_years = max(1, int(((policy.duration_months or 12) + 11) / 12))

    @api.depends("coverage_start_date", "duration_months")
    def _compute_coverage_end_date(self):
        for policy in self:
            months = policy.duration_months or 12
            if policy.coverage_start_date and months:
                policy.coverage_end_date = (
                    policy.coverage_start_date
                    + relativedelta(months=months)
                    - relativedelta(days=1)
                )
            elif not policy.coverage_end_date:
                policy.coverage_end_date = False

    @api.depends("coverage_start_date", "scheme_id.growth_percent")
    def _compute_next_growth_date(self):
        for policy in self:
            if policy.next_growth_date:
                policy.next_growth_date = policy.next_growth_date
            elif policy.scheme_id.growth_percent and policy.issue_date:
                policy.next_growth_date = policy.issue_date + relativedelta(years=1)
            else:
                policy.next_growth_date = False

    @api.depends("policy_amount", "commission_rate")
    def _compute_commission_amount(self):
        for policy in self:
            policy.commission_amount = (policy.policy_amount or 0.0) * ((policy.commission_rate or 0.0) / 100.0)

    @api.depends("premium_type", "duration_months", "duration_years")
    def _compute_installment_count(self):
        for policy in self:
            policy.installment_count = policy._get_installment_count()

    @api.depends("installment_amount", "installment_count")
    def _compute_installment_total(self):
        for policy in self:
            policy.installment_total = (policy.installment_amount or 0.0) * (policy.installment_count or 0)

    @api.depends("installment_ids", "installment_ids.state")
    def _compute_installment_history(self):
        for policy in self:
            lines = policy.installment_ids
            policy.installment_to_pay_ids = lines.filtered(
                lambda line: line.state in ("pending", "invoiced", "overdue")
            )
            policy.installment_paid_ids = lines.filtered(lambda line: line.state == "paid")

    @api.depends(
        "installment_ids.state",
        "installment_ids.amount",
        "installment_ids.paid_amount",
        "installment_ids.invoice_id",
    )
    def _compute_collection_stats(self):
        for policy in self:
            lines = policy.installment_ids.filtered(lambda l: l.state != "cancelled")
            policy.installment_count_done = len(lines.filtered(lambda l: l.state == "paid"))
            policy.amount_invoiced = sum(lines.filtered(lambda l: l.invoice_id).mapped("amount"))
            # Prima pagada = suma de todos los pagos registrados por el cliente
            policy.amount_paid = sum(lines.mapped("paid_amount"))
            policy.amount_due = sum(
                max((l.amount or 0.0) - (l.paid_amount or 0.0), 0.0)
                for l in lines.filtered(lambda l: l.state in ("pending", "invoiced", "overdue"))
            )
            policy.amount_to_invoice = sum(
                lines.filtered(lambda l: not l.invoice_id).mapped("amount")
            )
            policy.overdue_count = len(lines.filtered(lambda l: l.state == "overdue"))

    @api.depends("document_ids.state")
    def _compute_document_stats(self):
        for policy in self:
            docs = policy.document_ids.filtered(lambda d: not d.claim_id)
            policy.document_pending_count = len(docs.filtered(lambda d: d.state in ("pending", "uploaded", "rejected")))
            policy.document_signed_count = len(docs.filtered(lambda d: d.state == "signed"))

    @api.depends("invoice_ids")
    def _compute_invoice_count(self):
        for policy in self:
            policy.invoice_count = len(policy.invoice_ids)

    def _compute_member_count(self):
        for policy in self:
            policy.member_count = len(policy.member_ids)

    def _compute_renewal_count(self):
        for policy in self:
            policy.renewal_count = len(policy.renewal_policy_ids)

    @api.depends("claim_ids", "claim_ids.state")
    def _compute_claim_stats(self):
        for policy in self:
            policy.claim_count = len(policy.claim_ids)
            policy.claim_open_count = len(
                policy.claim_ids.filtered(lambda c: c.state not in ("closed", "rejected", "paid"))
            )

    @api.depends("scheme_id", "policy_type_id", "partner_id.insurance_cfdi_usage")
    def _compute_cfdi_usage(self):
        for policy in self:
            if policy.partner_id.insurance_cfdi_usage:
                policy.cfdi_usage = policy.partner_id.insurance_cfdi_usage
            elif policy.policy_type_id and policy.policy_type_id.code == "SEGGMM":
                policy.cfdi_usage = "D07"
            else:
                policy.cfdi_usage = "G03"

    @api.depends("coverage_ids.consumed_amount", "coverage_ids.insured_amount")
    def _compute_coverage_usage(self):
        for policy in self:
            policy.coverage_consumed = sum(policy.coverage_ids.mapped("consumed_amount"))
            policy.coverage_remaining = sum(policy.coverage_ids.mapped("remaining_amount"))

    @api.depends(
        "state",
        "lead_id",
        "end_date",
        "claim_ids.state",
        "auto_renew",
        "renewal_policy_ids",
    )
    def _compute_lifecycle_stage(self):
        today = fields.Date.context_today(self)
        horizon = today + relativedelta(days=60)
        for policy in self:
            if policy.state in ("cancelled", "done"):
                policy.lifecycle_stage = "closed"
            elif policy.state == "expired":
                policy.lifecycle_stage = "renewal"
            elif policy.state == "draft":
                policy.lifecycle_stage = "prospecting" if policy.lead_id else "onboarding"
            elif policy.claim_ids.filtered(lambda c: c.state not in ("closed", "rejected", "paid")):
                policy.lifecycle_stage = "claim"
            elif policy.end_date and policy.end_date <= horizon:
                policy.lifecycle_stage = "renewal"
            else:
                policy.lifecycle_stage = "active"

    def _get_installment_count(self):
        self.ensure_one()
        months = PREMIUM_PERIODS.get(self.premium_type, 1)
        total_months = self.duration_months or ((self.duration_years or 1) * 12)
        return max(1, int(total_months / months))

    def _get_collection_date(self, emission):
        """Fecha de cobro de un recibo emitido en `emission`.

        - Sin fecha fija: al terminar los días de gracia.
        - Con día fijo: el primer día del mes solicitado a partir de la emisión,
          nunca después del fin de la gracia.
        """
        self.ensure_one()
        grace_end = emission + relativedelta(days=self.grace_days or 0)
        if self.collection_date_mode == "fixed" and self.collection_day:
            candidate = emission + relativedelta(day=self.collection_day)
            if candidate < emission:
                candidate = emission + relativedelta(months=1, day=self.collection_day)
            return min(candidate, grace_end)
        return grace_end

    @api.model
    def _ensure_insurance_currencies(self):
        """Activa MXN y USD y crea la moneda UDI (Unidades de Inversión) si no existe."""
        Currency = self.env["res.currency"].sudo().with_context(active_test=False)
        for code in ("MXN", "USD"):
            currency = Currency.search([("name", "=", code)], limit=1)
            if currency and not currency.active:
                currency.active = True
        udi = Currency.search([("name", "=", "UDI")], limit=1)
        if not udi:
            Currency.create({
                "name": "UDI",
                "full_name": "Unidades de Inversión",
                "symbol": "UDI",
                "position": "after",
                "rounding": 0.01,
                "currency_unit_label": "UDIS",
                "active": True,
            })
        elif not udi.active:
            udi.active = True

    @api.onchange("policy_type_id")
    def _onchange_policy_type_id(self):
        if self.offer_id and self.offer_id.ramo_id != self.policy_type_id:
            self.offer_id = False
            self.sub_offer_id = False
            self.sub_offer_text = False
        if self.policy_type_id and self.scheme_id.policy_type_id != self.policy_type_id:
            self.scheme_id = self.env["insurance.scheme"].search(
                [("policy_type_id", "=", self.policy_type_id.id)], limit=1
            )

    @api.onchange("offer_id")
    def _onchange_offer_id(self):
        if self.sub_offer_id and self.sub_offer_id.parent_id != self.offer_id:
            self.sub_offer_id = False
        if not self.offer_id.sub_offer_free_text:
            self.sub_offer_text = False

    @api.onchange("scheme_id")
    def _onchange_scheme_id(self):
        if not self.scheme_id:
            return
        scheme = self.scheme_id
        if scheme.policy_type_id and not self.policy_type_id:
            self.policy_type_id = scheme.policy_type_id
        if scheme.duration_months:
            self.duration_months = scheme.duration_months
        elif not self.duration_months:
            self.duration_months = 12
        if scheme.insurance_company_id and not self.insurance_company_id:
            self.insurance_company_id = scheme.insurance_company_id
        if scheme.insured_amount_min and not self.insured_amount:
            self.insured_amount = scheme.insured_amount_min
        if scheme.insured_amount_min and not self.policy_amount:
            self.policy_amount = scheme.amount_min or 0.0
        if scheme.currency_id:
            self.currency_id = scheme.currency_id
        if scheme.coverage_mode in ("individual", "family"):
            self.coverage_mode = scheme.coverage_mode
        if scheme.auto_renew_default:
            self.auto_renew = True
        if not self.coverage_ids:
            self.coverage_ids = [(5, 0, 0)] + [
                (0, 0, {
                    "name": coverage.name,
                    "description": coverage.description,
                    "insured_amount": coverage.insured_amount,
                    "deductible": coverage.deductible,
                    "coinsurance": coverage.coinsurance,
                    "waiting_days": coverage.waiting_days,
                })
                for coverage in scheme.coverage_ids
            ]
        if not self.feature_ids:
            self.feature_ids = [(5, 0, 0)] + [
                (0, 0, {"name": feature.name, "included": feature.included})
                for feature in scheme.feature_ids
            ]
        if scheme.description and not self.description:
            self.description = scheme.description

    @api.onchange("agent_id")
    def _onchange_agent_id(self):
        if self.agent_id:
            self.branch_id = self.agent_id.branch_id
            if self.agent_id.insurance_company_id:
                self.insurance_company_id = self.agent_id.insurance_company_id
            if self.agent_id.commission_rate and not self.commission_rate:
                self.commission_rate = self.agent_id.commission_rate

    @api.constrains("policy_type_id", "offer_id", "sub_offer_id")
    def _check_offer_hierarchy(self):
        for policy in self:
            if policy.offer_id and policy.offer_id.ramo_id != policy.policy_type_id:
                raise ValidationError(_("La oferta %s no pertenece al ramo seleccionado.") % policy.offer_id.name)
            if policy.sub_offer_id and policy.sub_offer_id.parent_id != policy.offer_id:
                raise ValidationError(_("La sub-oferta %s no pertenece a la oferta seleccionada.") % policy.sub_offer_id.name)

    @api.constrains(
        "collection_date_mode",
        "collection_day",
        "grace_days",
        "extension_days",
        "auto_charge_days",
        "protection_days",
    )
    def _check_collection_calendar(self):
        for policy in self:
            if policy.collection_date_mode == "fixed" and not (1 <= (policy.collection_day or 0) <= 31):
                raise ValidationError(_("Indique un día fijo de cobro entre 1 y 31."))
            if min(policy.grace_days, policy.extension_days, policy.auto_charge_days, policy.protection_days) < 0:
                raise ValidationError(_("Los días del calendario de cobranza no pueden ser negativos."))
            if policy.auto_charge_days > policy.extension_days:
                raise ValidationError(_("Los días de cobro automático deben estar dentro de los días de prórroga."))

    @api.constrains("installment_amount")
    def _check_installment_amount(self):
        for policy in self:
            if policy.installment_amount < 0:
                raise ValidationError(_("El importe de la cuota no puede ser negativo."))

    @api.constrains("commission_rate")
    def _check_commission_rate(self):
        for policy in self:
            if policy.commission_rate < 0 or policy.commission_rate > 100:
                raise ValidationError(_("La comisión del agente debe estar entre 0 y 100."))

    @api.constrains("duration_years", "scheme_id")
    def _check_duration_against_scheme(self):
        for policy in self:
            scheme = policy.scheme_id
            if not scheme:
                continue
            if scheme.duration_min and policy.duration_years < scheme.duration_min:
                raise ValidationError(
                    _("La duración mínima del esquema %s es de %s años.") % (scheme.name, scheme.duration_min)
                )
            if scheme.duration_max and policy.duration_years > scheme.duration_max:
                raise ValidationError(
                    _("La duración máxima del esquema %s es de %s años.") % (scheme.name, scheme.duration_max)
                )

    @api.constrains("policy_amount", "scheme_id")
    def _check_amount_against_scheme(self):
        for policy in self:
            scheme = policy.scheme_id
            if not scheme or not policy.policy_amount:
                continue
            if scheme.amount_min and policy.policy_amount < scheme.amount_min:
                raise ValidationError(
                    _("El monto mínimo del esquema %s es %s.") % (scheme.name, scheme.amount_min)
                )
            if scheme.amount_max and policy.policy_amount > scheme.amount_max:
                raise ValidationError(
                    _("El monto máximo del esquema %s es %s.") % (scheme.name, scheme.amount_max)
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("scheme_id") and not vals.get("policy_type_id"):
                scheme = self.env["insurance.scheme"].browse(vals["scheme_id"])
                vals["policy_type_id"] = scheme.policy_type_id.id
            if vals.get("name", _("Nuevo")) in (False, "/", _("Nuevo")):
                scheme = self.env["insurance.scheme"].browse(vals.get("scheme_id"))
                vals["name"] = scheme.next_policy_number() if scheme else self.env["ir.sequence"].next_by_code("insurance.policy") or _("Nuevo")
        return super().create(vals_list)

    def action_confirm(self):
        for policy in self:
            if policy.state != "draft":
                raise UserError(_("Solo se pueden confirmar pólizas en borrador."))
            if not policy.partner_id:
                raise UserError(_("Debe indicar el titular de la póliza."))
            if not policy.policy_type_id:
                raise UserError(_("Debe indicar el ramo de la póliza."))
            if policy.name in (False, _("Nuevo"), "/"):
                policy.name = (
                    policy.scheme_id.next_policy_number()
                    if policy.scheme_id
                    else self.env["ir.sequence"].next_by_code("insurance.policy") or _("Nuevo")
                )
            policy._generate_members()
            policy._generate_coverages()
            policy._generate_features()
            if not policy.installment_ids.filtered(lambda l: l.state != "cancelled"):
                policy._generate_installments()
            policy._generate_required_documents()
            try:
                policy._sync_calendar_event()
            except Exception as err:
                policy.message_post(body=_("No se creó el evento de calendario: %s") % err)
            try:
                policy._sync_project()
            except Exception as err:
                policy.message_post(body=_("No se creó el proyecto de seguimiento: %s") % err)
            if not policy.prima_neta:
                policy.prima_neta = policy.policy_amount
            if policy.installment_count == 1:
                policy.cfdi_payment_method = "PUE"
                if policy.cfdi_payment_form == "99":
                    policy.cfdi_payment_form = "03"
            policy.state = "confirmed"
            if policy.invoice_mode == "global_ppd":
                try:
                    policy._create_cfdi_global_invoice()
                except Exception as err:
                    policy.message_post(body=_("Póliza confirmada, pero el CFDI global quedó pendiente: %s") % err)
            try:
                policy._sync_subscription()
            except Exception as err:
                policy.message_post(body=_("No se creó la suscripción automática: %s") % err)
            policy.message_post(body=_("Póliza confirmada. Se generaron cuotas, asegurados y documentos requeridos."))
        return True

    def _create_cfdi_global_invoice(self):
        from .cfdi_utils import apply_cfdi_invoice_values, apply_sat_product_code

        self.ensure_one()
        if self.cfdi_global_move_id:
            return self.cfdi_global_move_id
        product = self.scheme_id.product_id or self.env.ref(
            "insurance_management.product_insurance_premium",
            raise_if_not_found=False,
        )
        apply_sat_product_code(product)
        tax_ids = self.scheme_id.tax_id.ids
        line_vals = {
            "product_id": product.id if product else False,
            "name": _("Prima CFDI 4.0 póliza %s — vigencia %s a %s") % (
                self.name,
                self.coverage_start_date or self.issue_date,
                self.coverage_end_date or self.end_date,
            ),
            "quantity": 1,
            "price_unit": self.policy_amount,
            "tax_ids": [(6, 0, tax_ids)],
        }
        company = self.company_id or self.env.company
        journal = self.env["insurance.installment"]._get_sale_journal(company)
        vals = {
            "move_type": "out_invoice",
            "partner_id": self.partner_id.id,
            "company_id": company.id,
            "journal_id": journal.id,
            "invoice_date": self.issue_date or fields.Date.context_today(self),
            "invoice_date_due": self.issue_date or fields.Date.context_today(self),
            "invoice_origin": self.name,
            "invoice_payment_term_id": self.payment_term_id.id if self.payment_term_id else False,
            "ref": _("CFDI global %s") % self.name,
            "insurance_policy_id": self.id,
            "is_insurance_cfdi_global": True,
            "invoice_line_ids": [(0, 0, line_vals)],
        }
        apply_cfdi_invoice_values(
            self.env,
            vals,
            usage=self.cfdi_usage or "G03",
            payment_method=self.cfdi_payment_method or "PPD",
            payment_form=self.cfdi_payment_form or "99",
        )
        invoice = self.env["account.move"].with_company(company).create(vals)
        invoice._fill_insurance_complement_from_policy(self)
        self.cfdi_global_move_id = invoice.id
        self.installment_ids.filtered(lambda l: not l.invoice_id).write({
            "invoice_id": invoice.id,
            "state": "invoiced",
        })
        return invoice

    def _sync_installments_from_global_cfdi(self):
        today = fields.Date.context_today(self)
        for policy in self:
            invoice = policy.cfdi_global_move_id
            if not invoice:
                continue
            paid = invoice.amount_total - invoice.amount_residual
            leftover = paid
            for line in policy.installment_ids.sorted("number"):
                if line.state == "cancelled":
                    continue
                if line.state == "paid":
                    leftover -= line.amount
                    continue
                if leftover >= (line.amount or 0.0) - 0.01:
                    line.write({
                        "paid_amount": line.paid_amount or line.amount,
                        "payment_date": line.payment_date or today,
                        "state": "paid",
                    })
                    leftover -= line.amount
                elif (line.extension_end_date or line.due_date) and (line.extension_end_date or line.due_date) < today:
                    line.state = "overdue"
                elif invoice.state != "cancel":
                    line.state = "invoiced"

    def _sync_subscription(self):
        self.ensure_one()
        if "sale.order" not in self.env or "is_subscription" not in self.env["sale.order"]._fields:
            return
        if self.subscription_id or self.installment_count <= 1:
            return
        product = self.scheme_id.product_id or self.env.ref(
            "insurance_management.product_insurance_premium",
            raise_if_not_found=False,
        )
        if not product:
            return
        interval = {
            "monthly": "month",
            "quarterly": "month",
            "semiannual": "month",
            "yearly": "year",
        }.get(self.premium_type)
        if not interval:
            return
        vals = {
            "partner_id": self.partner_id.id,
            "origin": self.name,
            "is_subscription": True,
            "order_line": [(0, 0, {
                "product_id": product.id,
                "name": _("Prima recurrente %s") % self.name,
                "product_uom_qty": 1,
                "price_unit": self.installment_amount or self.policy_amount,
            })],
        }
        if "plan_id" in self.env["sale.order"]._fields:
            Plan = None
            if "sale.subscription.plan" in self.env:
                Plan = self.env["sale.subscription.plan"]
            elif "sale.plan" in self.env:
                Plan = self.env["sale.plan"]
            if Plan:
                plan = Plan.search([], limit=1)
                if plan:
                    vals["plan_id"] = plan.id
        order = self.env["sale.order"].create(vals)
        self.subscription_id = order.id
        self.message_post(body=_("Se vinculó la suscripción %s para la prima recurrente.") % order.name)

    def action_open_cfdi_global(self):
        self.ensure_one()
        invoice = self.cfdi_global_move_id or self._create_cfdi_global_invoice()
        return {
            "type": "ir.actions.act_window",
            "name": _("CFDI 4.0 de prima"),
            "res_model": "account.move",
            "res_id": invoice.id,
            "view_mode": "form",
        }

    def action_open_subscription(self):
        self.ensure_one()
        if not self.subscription_id:
            self._sync_subscription()
        if not self.subscription_id:
            raise UserError(_("No hay módulo de Suscripciones o la póliza es de contado."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Suscripción"),
            "res_model": "sale.order",
            "res_id": self.subscription_id.id,
            "view_mode": "form",
        }

    def action_cancel(self):
        for policy in self:
            invoiced = policy.installment_ids.filtered(lambda l: l.invoice_id and l.invoice_id.state == "posted")
            if invoiced:
                raise UserError(_("No puede cancelar una póliza con facturas publicadas. Cancele o rectifique las facturas primero."))
            policy.installment_ids.filtered(lambda l: not l.invoice_id).write({"state": "cancelled"})
            policy.state = "cancelled"
        return True

    def action_draft(self):
        for policy in self:
            policy.installment_ids.filtered(lambda l: l.state == "cancelled").write({"state": "pending"})
            policy.state = "draft"
        return True

    def action_done(self):
        for policy in self:
            pending = policy.installment_ids.filtered(lambda l: l.state not in ("paid", "cancelled"))
            if pending:
                raise UserError(_("Aún hay cuotas pendientes o vencidas. Regularice la cobranza antes de cerrar."))
            unsigned = policy.document_ids.filtered(
                lambda d: not d.claim_id and d.requires_signature and d.state != "signed"
            )
            if unsigned:
                raise UserError(_("Hay documentos con firma pendiente. Complete el expediente antes de cerrar."))
            policy.state = "done"
        return True

    def _generate_members(self):
        self.ensure_one()
        if self.member_ids:
            return
        if not self.partner_id:
            return
        self.member_ids = [(0, 0, {
            "partner_id": self.partner_id.id,
            "name": self.partner_id.name,
            "insured_role": "insured",
            "relationship": "holder",
            "mx_rfc": self.partner_id.vat,
            "mx_curp": self.partner_id.mx_curp,
            "birth_date": self.partner_id.mx_birth_date,
            "phone": self.partner_id._insurance_phone(),
            "email": self.partner_id.email,
        })]

    def action_renew(self):
        self.ensure_one()
        if self.state not in ("confirmed", "expired", "done"):
            raise UserError(_("Solo se renuevan pólizas activas, vencidas o cerradas."))
        existing = self.renewal_policy_ids[:1]
        if existing:
            return self._open_policy(existing)
        issue = (self.coverage_end_date + relativedelta(days=1)) if self.coverage_end_date else fields.Date.context_today(self)
        percent = self.scheme_id.growth_percent or 0.0
        new_premium = self.policy_amount * (1 + percent / 100.0) if percent else self.policy_amount
        new_insured = self.insured_amount * (1 + percent / 100.0) if percent and self.insured_amount else self.insured_amount
        new_installment = self.installment_amount * (1 + percent / 100.0) if percent else self.installment_amount
        new_policy = self.copy({
            "name": _("Nuevo"),
            "state": "draft",
            "origin_policy_id": self.id,
            "coverage_start_date": issue,
            "emission_date": issue,
            "lead_id": False,
            "policy_amount": new_premium,
            "insured_amount": new_insured,
            "installment_amount": new_installment,
            "next_growth_date": False,
        })
        new_policy.coverage_ids.write({"consumed_amount": 0.0})
        self.message_post(body=_("Se creó la renovación %s para el siguiente periodo.") % new_policy.name)
        return self._open_policy(new_policy)

    def _open_policy(self, policy):
        return {
            "type": "ir.actions.act_window",
            "name": _("Póliza"),
            "res_model": "insurance.policy",
            "res_id": policy.id,
            "view_mode": "form",
        }

    def action_open_claims(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Siniestros / casos"),
            "res_model": "insurance.claim",
            "view_mode": "list,form",
            "domain": [("policy_id", "=", self.id)],
            "context": {
                "default_policy_id": self.id,
            },
        }

    def action_open_renewals(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Renovaciones"),
            "res_model": "insurance.policy",
            "view_mode": "list,form",
            "domain": [("origin_policy_id", "=", self.id)],
        }

    def _generate_features(self):
        self.ensure_one()
        if self.feature_ids:
            return
        self.feature_ids = [
            (0, 0, {"name": feature.name, "included": feature.included})
            for feature in self.scheme_id.feature_ids
        ]

    def _sync_calendar_event(self):
        self.ensure_one()
        if "calendar.event" not in self.env:
            return
        start = self.issue_date or fields.Date.context_today(self)
        stop = self.end_date or start
        vals = {
            "name": _("Vigencia %s") % (self.display_title or self.name),
            "start": fields.Datetime.to_datetime(start),
            "stop": fields.Datetime.to_datetime(stop) + relativedelta(hours=23, minutes=59),
            "allday": True,
            "partner_ids": [(6, 0, self.partner_id.ids)],
            "user_id": self.user_id.id or self.env.user.id,
            "description": self.special_note or self.description or "",
        }
        if self.calendar_event_id:
            self.calendar_event_id.write(vals)
        else:
            self.calendar_event_id = self.env["calendar.event"].create(vals)

    def _sync_project(self):
        self.ensure_one()
        if self.project_id or "project.project" not in self.env:
            return
        self.project_id = self.env["project.project"].create({
            "name": self.display_title or self.name,
            "partner_id": self.partner_id.id,
            "user_id": self.user_id.id or self.env.user.id,
        })

    def action_create_commission_bill(self):
        self.ensure_one()
        if self.commission_move_id:
            return {
                "type": "ir.actions.act_window",
                "name": _("Comisión del agente"),
                "res_model": "account.move",
                "res_id": self.commission_move_id.id,
                "view_mode": "form",
            }
        if not self.agent_id or not self.agent_id.partner_id:
            raise UserError(_("El agente debe tener un contacto para facturar la comisión."))
        if self.commission_amount <= 0:
            raise UserError(_("Defina la prima y el porcentaje de comisión del agente."))
        product = self.scheme_id.product_id
        invoice = self.env["account.move"].create({
            "move_type": "in_invoice",
            "partner_id": self.agent_id.partner_id.id,
            "invoice_date": fields.Date.context_today(self),
            "invoice_origin": self.name,
            "ref": _("Comisión %s") % self.name,
            "insurance_policy_id": self.id,
            "invoice_line_ids": [(0, 0, {
                "name": _("Comisión de agente %s — póliza %s") % (self.agent_id.name, self.name),
                "quantity": 1,
                "price_unit": self.commission_amount,
                "product_id": product.id if product else False,
            })],
        })
        self.commission_move_id = invoice.id
        self.message_post(body=_("Se generó la factura de comisión %s.") % invoice.name)
        return {
            "type": "ir.actions.act_window",
            "name": _("Comisión del agente"),
            "res_model": "account.move",
            "res_id": invoice.id,
            "view_mode": "form",
        }

    def _generate_coverages(self):
        self.ensure_one()
        if self.coverage_ids:
            return
        self.coverage_ids = [
            (0, 0, {
                "name": coverage.name,
                "description": coverage.description,
                "insured_amount": coverage.insured_amount,
                "deductible": coverage.deductible,
                "coinsurance": coverage.coinsurance,
                "waiting_days": coverage.waiting_days,
                "date_from": self.coverage_start_date or self.issue_date,
                "date_to": self.coverage_end_date or self.end_date,
            })
            for coverage in self.scheme_id.coverage_ids
        ]

    def action_generate_installments(self):
        """Botón: genera la lista de cuotas con el importe capturado manualmente."""
        for policy in self:
            if policy.state not in ("draft", "confirmed"):
                raise UserError(_("Solo se generan cuotas en pólizas en borrador o activas."))
            policy._generate_installments()
        return True

    def _generate_installments(self):
        """Crea una cuota por periodo según la forma de pago.

        Cada cuota lleva su calendario: emisión del recibo → días de gracia →
        fecha de cobro → prórroga (con cobro automático) → amparo.
        """
        self.ensure_one()
        if not self.installment_amount or self.installment_amount <= 0:
            raise UserError(_(
                "Capture el importe de la cuota antes de generar la lista de cuotas."
            ))
        active_lines = self.installment_ids.filtered(lambda l: l.state != "cancelled")
        if active_lines.filtered(lambda l: l.state == "paid" or l.paid_amount):
            raise UserError(_(
                "Ya hay cuotas con pagos registrados. No se puede regenerar la lista; "
                "ajuste las cuotas pendientes directamente."
            ))
        global_move = self.cfdi_global_move_id
        own_invoice = active_lines.filtered(lambda l: l.invoice_id and l.invoice_id != global_move)
        if own_invoice:
            raise UserError(_(
                "Hay cuotas con factura propia. Cancele esas facturas antes de regenerar las cuotas."
            ))
        self.installment_ids.filtered(lambda l: l.state != "paid").unlink()

        count = self._get_installment_count()
        months = PREMIUM_PERIODS.get(self.premium_type, 1)
        start = self.coverage_start_date or fields.Date.context_today(self)
        lines = []
        for index in range(count):
            date_from = start + relativedelta(months=months * index)
            date_to = date_from + relativedelta(months=months) - relativedelta(days=1)
            emission = self.emission_date if index == 0 and self.emission_date else date_from
            vals = {
                "number": index + 1,
                "date_from": date_from,
                "date_to": date_to,
                "emission_date": emission,
                "due_date": self._get_collection_date(emission),
                "amount": self.installment_amount,
            }
            if global_move:
                vals.update({"invoice_id": global_move.id, "state": "invoiced"})
            lines.append((0, 0, vals))
        self.installment_ids = lines
        self.message_post(body=_(
            "Se generaron %(count)s cuotas de %(amount)s %(currency)s."
        ) % {
            "count": count,
            "amount": self.installment_amount,
            "currency": self.currency_id.name or "",
        })

    def _generate_required_documents(self):
        self.ensure_one()
        existing_types = self.document_ids.filtered(lambda d: not d.claim_id).mapped("document_type_id")
        contracting_types = self.scheme_id.document_type_ids.filtered(
            lambda t: t.usage in ("contracting", "both")
        )
        missing = contracting_types - existing_types
        self.document_ids = [
            (0, 0, {
                "document_type_id": doc_type.id,
                "partner_id": self.partner_id.id,
                "state": "pending",
            })
            for doc_type in missing
        ]

    def action_create_all_pending_invoices(self):
        lines = self.installment_ids.filtered(lambda l: l.state == "pending" and not l.invoice_id)
        if not lines:
            raise UserError(_("No hay cuotas pendientes por facturar."))
        return lines.action_create_invoice()

    def action_apply_growth(self):
        for policy in self:
            percent = policy.scheme_id.growth_percent
            if not percent:
                raise UserError(_("El esquema no tiene porcentaje de crecimiento configurado."))
            old_premium = policy.policy_amount
            old_insured = policy.insured_amount
            new_premium = old_premium * (1 + percent / 100.0)
            new_insured = old_insured * (1 + percent / 100.0) if old_insured else old_insured
            new_installment = (policy.installment_amount or 0.0) * (1 + percent / 100.0)
            policy.write({
                "policy_amount": new_premium,
                "insured_amount": new_insured,
                "installment_amount": new_installment,
                "next_growth_date": (policy.next_growth_date or fields.Date.context_today(policy)) + relativedelta(years=1),
            })
            self.env["insurance.policy.growth"].create({
                "policy_id": policy.id,
                "date": fields.Date.context_today(policy),
                "percent": percent,
                "old_premium": old_premium,
                "new_premium": new_premium,
                "old_insured": old_insured,
                "new_insured": new_insured,
            })
            pending = policy.installment_ids.filtered(lambda l: l.state == "pending" and not l.invoice_id)
            if pending:
                new_amount = policy.installment_amount
                pending.write({"amount": new_amount})
            policy.message_post(
                body=_("Crecimiento aplicado (%s %%): prima %s → %s.") % (percent, old_premium, new_premium)
            )
        return True

    def action_open_invoices(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Facturas"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("insurance_policy_id", "=", self.id)],
            "context": {"default_insurance_policy_id": self.id, "default_partner_id": self.partner_id.id},
        }

    def action_open_installments(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Cuotas"),
            "res_model": "insurance.installment",
            "view_mode": "list,form",
            "domain": [("policy_id", "=", self.id)],
        }

    def action_open_documents(self):
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_("Asigne el titular para abrir su expediente."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Expediente de %s") % self.partner_id.name,
            "res_model": "res.partner",
            "res_id": self.partner_id.id,
            "view_mode": "form",
        }

    def action_open_partner(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Cliente"),
            "res_model": "res.partner",
            "res_id": self.partner_id.id,
            "view_mode": "form",
        }

    @api.model
    def _cron_growth_followup(self):
        today = fields.Date.context_today(self)
        horizon = today + relativedelta(days=30)
        policies = self.search([
            ("state", "=", "confirmed"),
            ("next_growth_date", "!=", False),
            ("next_growth_date", "<=", horizon),
        ])
        activity_type = self.env.ref(
            "insurance_management.mail_activity_insurance_growth",
            raise_if_not_found=False,
        )
        if not activity_type:
            return
        for policy in policies:
            existing = self.env["mail.activity"].search([
                ("res_model", "=", "insurance.policy"),
                ("res_id", "=", policy.id),
                ("activity_type_id", "=", activity_type.id),
            ], limit=1)
            if existing:
                continue
            policy.activity_schedule(
                "insurance_management.mail_activity_insurance_growth",
                date_deadline=policy.next_growth_date,
                user_id=policy.user_id.id or self.env.user.id,
                summary=_("Fecha de crecimiento — %s") % policy.name,
            )

    @api.model
    def _cron_document_followup(self):
        activity_type = self.env.ref(
            "insurance_management.mail_activity_insurance_document",
            raise_if_not_found=False,
        )
        if not activity_type:
            return
        policies = self.search([("state", "=", "confirmed")])
        for policy in policies.filtered(lambda p: p.document_pending_count):
            existing = self.env["mail.activity"].search([
                ("res_model", "=", "insurance.policy"),
                ("res_id", "=", policy.id),
                ("activity_type_id", "=", activity_type.id),
            ], limit=1)
            if existing:
                continue
            policy.activity_schedule(
                "insurance_management.mail_activity_insurance_document",
                user_id=policy.user_id.id or self.env.user.id,
                summary=_("Expediente incompleto — %s") % policy.name,
                note=_("Hay %s documentos pendientes o sin firma.") % policy.document_pending_count,
            )

    @api.model
    def _cron_renewal_followup(self):
        today = fields.Date.context_today(self)
        horizon = today + relativedelta(days=60)
        policies = self.search([
            ("state", "=", "confirmed"),
            ("auto_renew", "=", True),
            ("end_date", "!=", False),
            ("end_date", "<=", horizon),
            ("renewal_policy_ids", "=", False),
        ])
        activity_type = self.env.ref(
            "insurance_management.mail_activity_insurance_renewal",
            raise_if_not_found=False,
        )
        if not activity_type:
            return
        for policy in policies:
            existing = self.env["mail.activity"].search([
                ("res_model", "=", "insurance.policy"),
                ("res_id", "=", policy.id),
                ("activity_type_id", "=", activity_type.id),
            ], limit=1)
            if existing:
                continue
            policy.activity_schedule(
                "insurance_management.mail_activity_insurance_renewal",
                date_deadline=policy.end_date,
                user_id=policy.user_id.id or self.env.user.id,
                summary=_("Renovación anual — %s") % policy.name,
                note=_("La vigencia vence el %s. Contacte al cliente para renovar.") % policy.end_date,
            )

    @api.model
    def _cron_expire_policies(self):
        today = fields.Date.context_today(self)
        policies = self.search([
            ("state", "=", "confirmed"),
            ("end_date", "!=", False),
            ("end_date", "<", today),
        ])
        for policy in policies:
            policy.state = "expired"
            policy.message_post(body=_("La póliza pasó a Vencido al terminar su vigencia."))


class InsurancePolicyCoverage(models.Model):
    _name = "insurance.policy.coverage"
    _description = "Cobertura contratada"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    policy_id = fields.Many2one("insurance.policy", required=True, ondelete="cascade")
    name = fields.Char(string="Cobertura", required=True)
    description = fields.Text(string="Detalle")
    insured_amount = fields.Monetary(string="Suma asegurada", currency_field="currency_id")
    deductible = fields.Monetary(string="Deducible", currency_field="currency_id")
    coinsurance = fields.Float(string="Coaseguro (%)")
    waiting_days = fields.Integer(string="Periodo de espera (días)")
    date_from = fields.Date(string="Inicio de cobertura")
    date_to = fields.Date(string="Fin de cobertura")
    consumed_amount = fields.Monetary(string="Consumido", currency_field="currency_id")
    remaining_amount = fields.Monetary(
        string="Disponible",
        compute="_compute_remaining_amount",
        store=True,
        currency_field="currency_id",
    )
    consumption_percent = fields.Float(
        string="% consumido",
        compute="_compute_remaining_amount",
        store=True,
    )
    currency_id = fields.Many2one(related="policy_id.currency_id")

    @api.depends("insured_amount", "consumed_amount")
    def _compute_remaining_amount(self):
        for coverage in self:
            coverage.remaining_amount = max((coverage.insured_amount or 0.0) - (coverage.consumed_amount or 0.0), 0.0)
            coverage.consumption_percent = (
                (coverage.consumed_amount / coverage.insured_amount) * 100.0
                if coverage.insured_amount
                else 0.0
            )


class InsurancePolicyGrowth(models.Model):
    _name = "insurance.policy.growth"
    _description = "Histórico de crecimiento de póliza"
    _order = "date desc, id desc"

    policy_id = fields.Many2one("insurance.policy", required=True, ondelete="cascade")
    date = fields.Date(string="Fecha de crecimiento", required=True, default=fields.Date.context_today)
    percent = fields.Float(string="Porcentaje")
    old_premium = fields.Monetary(string="Prima anterior", currency_field="currency_id")
    new_premium = fields.Monetary(string="Prima nueva", currency_field="currency_id")
    old_insured = fields.Monetary(string="Suma anterior", currency_field="currency_id")
    new_insured = fields.Monetary(string="Suma nueva", currency_field="currency_id")
    note = fields.Char(string="Observación")
    currency_id = fields.Many2one(related="policy_id.currency_id")
