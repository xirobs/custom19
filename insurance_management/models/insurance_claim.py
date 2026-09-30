# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class InsuranceClaim(models.Model):
    _name = "insurance.claim"
    _description = "Siniestro / caso médico"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "occurrence_date desc, id desc"

    name = fields.Char(
        string="Folio",
        required=True,
        copy=False,
        default=lambda self: _("Nuevo"),
        tracking=True,
        index=True,
    )
    policy_id = fields.Many2one(
        "insurance.policy",
        string="Póliza",
        required=True,
        tracking=True,
        index=True,
        domain="[('state', 'in', ['confirmed', 'expired', 'done'])]",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Cliente",
        compute="_compute_partner_from_policy",
        store=True,
        readonly=False,
        index=True,
        tracking=True,
    )
    member_id = fields.Many2one(
        "insurance.policy.member",
        string="Asegurado afectado",
        domain="[('policy_id', '=', policy_id)]",
        tracking=True,
    )
    coverage_id = fields.Many2one(
        "insurance.policy.coverage",
        string="Cobertura a aplicar",
        domain="[('policy_id', '=', policy_id)]",
        tracking=True,
    )
    insurance_company_id = fields.Many2one(related="policy_id.insurance_company_id", store=True)
    scheme_id = fields.Many2one(related="policy_id.scheme_id", store=True)
    claim_kind = fields.Selection(
        [
            ("gmm", "Gastos médicos / caso clínico"),
            ("accident", "Accidente personal"),
            ("auto", "Siniestro automotriz"),
            ("life", "Vida / fallecimiento"),
            ("other", "Otro"),
        ],
        string="Tipo de caso",
        required=True,
        default="gmm",
        tracking=True,
    )
    occurrence_date = fields.Date(
        string="Fecha del evento",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    report_date = fields.Date(string="Fecha de aviso", default=fields.Date.context_today)
    diagnosis = fields.Char(string="Diagnóstico / descripción corta")
    description = fields.Text(string="Relato del caso")
    provider_name = fields.Char(string="Hospital / taller / prestador")
    state = fields.Selection(
        [
            ("draft", "Aviso"),
            ("documents", "Documentos"),
            ("review", "En evaluación"),
            ("authorized", "Autorizado"),
            ("paid", "Pagado / reembolsado"),
            ("rejected", "Rechazado"),
            ("closed", "Cerrado"),
        ],
        string="Estado",
        default="draft",
        required=True,
        tracking=True,
        copy=False,
        index=True,
    )
    consumption_type = fields.Selection(
        [
            ("partial", "Consumo parcial de la cobertura"),
            ("full", "Consumo del 100% de la cobertura"),
        ],
        string="Uso de cobertura",
        default="partial",
        required=True,
        tracking=True,
    )
    currency_id = fields.Many2one(related="policy_id.currency_id")
    claimed_amount = fields.Monetary(string="Monto reclamado", currency_field="currency_id", tracking=True)
    deductible_amount = fields.Monetary(string="Deducible", currency_field="currency_id")
    authorized_amount = fields.Monetary(string="Monto autorizado", currency_field="currency_id", tracking=True)
    paid_amount = fields.Monetary(string="Monto pagado", currency_field="currency_id")
    coverage_limit = fields.Monetary(related="coverage_id.insured_amount", string="Límite de cobertura")
    coverage_remaining = fields.Monetary(related="coverage_id.remaining_amount", string="Saldo de cobertura")
    company_id = fields.Many2one(related="policy_id.company_id", store=True)
    user_id = fields.Many2one(
        "res.users",
        string="Ajustador / responsable",
        default=lambda self: self.env.user,
        tracking=True,
    )
    document_ids = fields.One2many("insurance.document", "claim_id", string="Documentos del caso")
    document_pending_count = fields.Integer(compute="_compute_document_pending")
    rejection_reason = fields.Text(string="Motivo de rechazo")
    note = fields.Text(string="Notas internas")
    consumed = fields.Boolean(string="Cobertura descontada", copy=False)

    @api.depends("document_ids.state")
    def _compute_document_pending(self):
        for claim in self:
            claim.document_pending_count = len(
                claim.document_ids.filtered(lambda d: d.state in ("pending", "uploaded", "rejected"))
            )

    @api.onchange("coverage_id")
    def _onchange_coverage_id(self):
        if self.coverage_id and not self.deductible_amount:
            self.deductible_amount = self.coverage_id.deductible

    @api.onchange("policy_id")
    def _onchange_policy_id(self):
        if self.policy_id and not self.member_id:
            holder = self.policy_id.member_ids.filtered("is_holder")[:1]
            self.member_id = holder

    @api.constrains("authorized_amount", "coverage_id", "consumption_type")
    def _check_authorized_against_coverage(self):
        for claim in self:
            if not claim.coverage_id or not claim.authorized_amount:
                continue
            available = claim.coverage_id.remaining_amount
            if claim.consumed:
                available += claim.authorized_amount
            if claim.consumption_type != "full" and claim.authorized_amount > available + 0.01:
                raise ValidationError(
                    _("El monto autorizado (%s) supera el saldo de la cobertura (%s).")
                    % (claim.authorized_amount, available)
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("Nuevo")) in (False, "/", _("Nuevo")):
                vals["name"] = self.env["ir.sequence"].next_by_code("insurance.claim") or _("Nuevo")
        claims = super().create(vals_list)
        claims._generate_required_documents()
        return claims

    def action_request_documents(self):
        for claim in self:
            claim._generate_required_documents()
            claim.state = "documents"
            claim.message_post(body=_("Se generó el expediente de documentos del caso."))
        return True

    def action_submit_review(self):
        for claim in self:
            pending = claim.document_ids.filtered(
                lambda d: d.requires_signature and d.state != "signed" or d.state == "pending"
            )
            if pending:
                raise UserError(
                    _("Hay documentos pendientes en el caso. Complételos antes de enviarlo a evaluación.")
                )
            claim.state = "review"
        return True

    def action_authorize(self):
        for claim in self:
            if not claim.coverage_id:
                raise UserError(_("Indique la cobertura a aplicar."))
            if claim.consumption_type == "full":
                claim.authorized_amount = claim.coverage_id.remaining_amount
            elif not claim.authorized_amount:
                raise UserError(_("Capture el monto autorizado o marque consumo del 100%."))
            claim._apply_coverage_consumption()
            claim.state = "authorized"
            claim.message_post(body=_("Caso autorizado. Se descontó el consumo de la cobertura."))
        return True

    def action_mark_paid(self):
        for claim in self:
            if claim.state != "authorized":
                raise UserError(_("Solo se puede pagar un caso autorizado."))
            claim.paid_amount = claim.paid_amount or claim.authorized_amount
            claim.state = "paid"
        return True

    def action_reject(self):
        for claim in self:
            if not claim.rejection_reason:
                raise UserError(_("Indique el motivo de rechazo."))
            if claim.consumed:
                claim._reverse_coverage_consumption()
            claim.state = "rejected"
        return True

    def action_close(self):
        for claim in self:
            if claim.state not in ("paid", "rejected", "authorized"):
                raise UserError(_("Cierre el caso cuando ya esté autorizado, pagado o rechazado."))
            claim.state = "closed"
        return True

    def action_draft(self):
        for claim in self:
            if claim.consumed:
                claim._reverse_coverage_consumption()
            claim.state = "draft"
        return True

    def _generate_required_documents(self):
        DocumentType = self.env["insurance.document.type"]
        for claim in self:
            types = DocumentType.search([
                ("usage", "in", ["claim", "both"]),
                ("claim_kind", "in", [claim.claim_kind, "all"]),
            ])
            existing = claim.document_ids.mapped("document_type_id")
            missing = types - existing
            claim.document_ids = [
                (0, 0, {
                    "partner_id": claim.partner_id.id or claim.policy_id.partner_id.id,
                    "policy_id": claim.policy_id.id,
                    "claim_id": claim.id,
                    "document_type_id": doc_type.id,
                    "state": "pending",
                })
                for doc_type in missing
            ]

    def _apply_coverage_consumption(self):
        self.ensure_one()
        if self.consumed or not self.coverage_id:
            return
        amount = (
            self.coverage_id.remaining_amount
            if self.consumption_type == "full"
            else self.authorized_amount
        )
        self.coverage_id.consumed_amount += amount
        self.consumed = True

    def _reverse_coverage_consumption(self):
        self.ensure_one()
        if not self.consumed or not self.coverage_id:
            return
        amount = self.authorized_amount
        self.coverage_id.consumed_amount = max(self.coverage_id.consumed_amount - amount, 0.0)
        self.consumed = False

    def action_open_documents(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Documentos del caso"),
            "res_model": "insurance.document",
            "view_mode": "list,form",
            "domain": [("claim_id", "=", self.id)],
            "context": {"default_claim_id": self.id, "default_policy_id": self.policy_id.id},
        }
