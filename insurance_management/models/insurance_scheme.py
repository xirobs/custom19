# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class InsuranceScheme(models.Model):
    _name = "insurance.scheme"
    _description = "Esquema / producto de seguro"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(string="Nombre de la oferta", required=True, tracking=True)
    policy_type_id = fields.Many2one(
        "insurance.policy.type",
        string="Tipo de seguro",
        required=True,
        tracking=True,
    )
    insurance_company_id = fields.Many2one(
        "insurance.company",
        string="Aseguradora",
        tracking=True,
    )
    description = fields.Html(string="Descripción de la oferta")
    policy_code = fields.Char(string="Código de póliza", required=True, tracking=True)
    duration_months = fields.Integer(
        string="Duración (meses)",
        default=12,
        required=True,
        help="Vigencia habitual de la oferta. Normalmente 12 meses.",
    )
    duration_min = fields.Integer(string="Duración mínima (años)", default=1)
    duration_max = fields.Integer(string="Duración máxima (años)", default=12)
    amount_min = fields.Monetary(string="Monto mínimo", currency_field="currency_id")
    amount_max = fields.Monetary(string="Monto máximo", currency_field="currency_id")
    insured_amount_min = fields.Monetary(string="Suma asegurada mínima", currency_field="currency_id")
    insured_amount_max = fields.Monetary(string="Suma asegurada máxima", currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency",
        string="Moneda",
        default=lambda self: self.env.company.currency_id,
    )
    tax_id = fields.Many2one(
        "account.tax",
        string="Impuesto",
        domain="[('type_tax_use', '=', 'sale')]",
    )
    interest_rate = fields.Float(string="Tasa de interés (%)")
    coverage_mode = fields.Selection(
        [
            ("individual", "Individual"),
            ("family", "Familiar"),
            ("both", "Individual o familiar"),
        ],
        string="Tipo de contratación",
        default="both",
        required=True,
    )
    growth_percent = fields.Float(
        string="Crecimiento anual (%)",
        help="Porcentaje de incremento de prima o suma asegurada en cada renovación o fecha de crecimiento.",
    )
    auto_renew_default = fields.Boolean(
        string="Renovable cada año",
        default=True,
        help="Las pólizas de este producto se pueden contratar de forma recurrente año tras año.",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Producto de facturación",
        help="Producto de servicio usado al facturar las cuotas y la suscripción.",
    )
    sat_product_code = fields.Char(
        string="ClaveProdServ SAT",
        default="84131500",
        help="Catálogo SAT. 84131500 = servicios de seguros.",
    )
    sequence_id = fields.Many2one("ir.sequence", string="Secuencia de numeración", copy=False)
    active = fields.Boolean(default=True)
    note = fields.Text(string="Notas")
    agent_line_ids = fields.One2many(
        "insurance.scheme.agent",
        "scheme_id",
        string="Agentes autorizados",
    )
    coverage_ids = fields.One2many(
        "insurance.scheme.coverage",
        "scheme_id",
        string="Coberturas",
    )
    feature_ids = fields.One2many(
        "insurance.scheme.feature",
        "scheme_id",
        string="Características",
    )
    document_type_ids = fields.Many2many(
        "insurance.document.type",
        "insurance_scheme_document_type_rel",
        "scheme_id",
        "document_type_id",
        string="Documentos requeridos",
    )
    policy_ids = fields.One2many("insurance.policy", "scheme_id", string="Pólizas")
    policy_count = fields.Integer(compute="_compute_policy_count")

    _sql_constraints = [
        ("policy_code_unique", "UNIQUE(policy_code)", "El código de póliza del esquema debe ser único."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if not record.sequence_id:
                sequence = self.env["ir.sequence"].sudo().create({
                    "name": _("Póliza %s") % record.policy_code,
                    "code": "insurance.policy.%s" % record.policy_code.lower(),
                    "prefix": "%s/" % record.policy_code,
                    "padding": 4,
                    "company_id": False,
                })
                record.sequence_id = sequence.id
        return records

    def _compute_policy_count(self):
        for scheme in self:
            scheme.policy_count = len(scheme.policy_ids)

    @api.constrains("duration_min", "duration_max")
    def _check_duration(self):
        for scheme in self:
            if scheme.duration_min and scheme.duration_max and scheme.duration_min > scheme.duration_max:
                raise ValidationError(_("La duración mínima no puede ser mayor que la máxima."))

    @api.constrains("amount_min", "amount_max")
    def _check_amount(self):
        for scheme in self:
            if scheme.amount_min and scheme.amount_max and scheme.amount_min > scheme.amount_max:
                raise ValidationError(_("El monto mínimo no puede ser mayor que el máximo."))

    def action_open_policies(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Pólizas",
            "res_model": "insurance.policy",
            "view_mode": "kanban,list,form,calendar,pivot,graph,activity",
            "domain": [("scheme_id", "=", self.id)],
            "context": {"default_scheme_id": self.id},
        }

    def next_policy_number(self):
        self.ensure_one()
        if self.sequence_id:
            return self.sequence_id.next_by_id()
        return self.env["ir.sequence"].next_by_code("insurance.policy") or _("Nuevo")


class InsuranceSchemeAgent(models.Model):
    _name = "insurance.scheme.agent"
    _description = "Agente autorizado del esquema"

    scheme_id = fields.Many2one("insurance.scheme", required=True, ondelete="cascade")
    agent_id = fields.Many2one("insurance.agent", string="Nombre", required=True)
    branch_id = fields.Many2one(
        "insurance.branch",
        string="Rama",
        related="agent_id.branch_id",
        store=True,
        readonly=False,
    )
    agent_code = fields.Char(related="agent_id.agent_code", string="Código de agente", store=True)
    date = fields.Date(string="Fecha", default=fields.Date.context_today)
    mobile = fields.Char(related="agent_id.mobile", string="Móvil", readonly=False)
    email = fields.Char(related="agent_id.email", string="Correo electrónico", readonly=False)


class InsuranceSchemeCoverage(models.Model):
    _name = "insurance.scheme.coverage"
    _description = "Cobertura del esquema"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    scheme_id = fields.Many2one("insurance.scheme", required=True, ondelete="cascade")
    name = fields.Char(string="Cobertura", required=True)
    description = fields.Text(string="Detalle")
    insured_amount = fields.Monetary(string="Suma asegurada", currency_field="currency_id")
    deductible = fields.Monetary(string="Deducible", currency_field="currency_id")
    coinsurance = fields.Float(string="Coaseguro (%)")
    waiting_days = fields.Integer(string="Periodo de espera (días)")
    currency_id = fields.Many2one(related="scheme_id.currency_id")
