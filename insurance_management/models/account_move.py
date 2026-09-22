# -*- coding: utf-8 -*-

from odoo import fields, models

from .cfdi_utils import CFDI_USAGE_GMM, CFDI_USAGE_GENERAL


class AccountMove(models.Model):
    _inherit = "account.move"

    insurance_policy_id = fields.Many2one(
        "insurance.policy",
        string="Póliza de seguro",
        copy=False,
        index=True,
    )
    insurance_installment_id = fields.Many2one(
        "insurance.installment",
        string="Cuota de seguro",
        copy=False,
        index=True,
    )
    is_insurance_cfdi_global = fields.Boolean(
        string="CFDI global de prima",
        copy=False,
        help="Factura tipo I CFDI 4.0 por la prima total (PPD). Los pagos generan complemento de pagos 2.0.",
    )
    insurance_cfdi_usage = fields.Selection(
        [
            ("G03", "G03 — Gastos en general"),
            ("D07", "D07 — Primas de seguros de gastos médicos"),
            ("S01", "S01 — Sin efectos fiscales"),
            ("CP01", "CP01 — Pagos"),
        ],
        string="Uso CFDI",
        default="G03",
    )
    insurance_cfdi_payment_method = fields.Selection(
        [
            ("PUE", "PUE — Pago en una sola exhibición"),
            ("PPD", "PPD — Pago en parcialidades o diferido"),
        ],
        string="Método de pago SAT",
        default="PPD",
    )
    insurance_cfdi_payment_form = fields.Selection(
        [
            ("01", "01 — Efectivo"),
            ("03", "03 — Transferencia electrónica"),
            ("04", "04 — Tarjeta de crédito"),
            ("28", "28 — Tarjeta de débito"),
            ("99", "99 — Por definir"),
        ],
        string="Forma de pago SAT",
        default="99",
    )
    insurance_sat_product_code = fields.Char(
        string="ClaveProdServ SAT",
        default="84131500",
    )
    insurance_complement_policy = fields.Char(string="Núm. de póliza (complemento)")
    insurance_complement_ramo = fields.Char(string="Ramo / tipo de seguro")
    insurance_complement_insured = fields.Char(string="Asegurado")
    insurance_complement_rfc = fields.Char(string="RFC del asegurado")
    insurance_complement_date_from = fields.Date(string="Inicio de vigencia")
    insurance_complement_date_to = fields.Date(string="Fin de vigencia")
    insurance_complement_coverage = fields.Monetary(
        string="Suma asegurada",
        currency_field="currency_id",
    )
    insurance_complement_coverage_used = fields.Monetary(
        string="Cobertura consumida",
        currency_field="currency_id",
    )
    insurance_complement_coverage_left = fields.Monetary(
        string="Cobertura disponible",
        currency_field="currency_id",
    )
    insurance_complement_prima_neta = fields.Monetary(string="Prima neta", currency_field="currency_id")
    insurance_complement_derechos = fields.Monetary(string="Derechos de póliza", currency_field="currency_id")
    insurance_complement_recargos = fields.Monetary(string="Recargos", currency_field="currency_id")
    insurance_complement_installment = fields.Integer(string="Parcialidad")

    def write(self, vals):
        result = super().write(vals)
        if "payment_state" in vals or "state" in vals or "amount_residual" in vals:
            self._sync_insurance_payments()
        return result

    def _sync_insurance_payments(self):
        installments = self.mapped("insurance_installment_id").filtered(lambda l: l)
        extra = self.env["insurance.installment"].search([("invoice_id", "in", self.ids)])
        (installments | extra)._sync_payment_state()
        globals_moves = self.filtered("is_insurance_cfdi_global")
        if globals_moves:
            globals_moves.mapped("insurance_policy_id")._sync_installments_from_global_cfdi()

    def _fill_insurance_complement_from_policy(self, policy, installment=None):
        self.ensure_one()
        usage = CFDI_USAGE_GMM if policy.policy_type_id and policy.policy_type_id.code == "SEGGMM" else CFDI_USAGE_GENERAL
        usage = policy.cfdi_usage or usage
        holder = policy.member_ids.filtered("is_holder")[:1]
        self.write({
            "insurance_cfdi_usage": usage,
            "insurance_cfdi_payment_method": policy.cfdi_payment_method or "PPD",
            "insurance_cfdi_payment_form": policy.cfdi_payment_form or "99",
            "insurance_sat_product_code": policy.scheme_id.sat_product_code or "84131500",
            "insurance_complement_policy": policy.name,
            "insurance_complement_ramo": policy.scheme_id.display_name,
            "insurance_complement_insured": (holder.name if holder else policy.partner_id.name),
            "insurance_complement_rfc": (holder.mx_rfc if holder else policy.partner_id.vat),
            "insurance_complement_date_from": policy.coverage_start_date or policy.issue_date,
            "insurance_complement_date_to": policy.coverage_end_date or policy.end_date,
            "insurance_complement_coverage": policy.insured_amount or sum(policy.coverage_ids.mapped("insured_amount")),
            "insurance_complement_coverage_used": policy.coverage_consumed,
            "insurance_complement_coverage_left": policy.coverage_remaining,
            "insurance_complement_prima_neta": policy.prima_neta or policy.policy_amount,
            "insurance_complement_derechos": policy.derechos,
            "insurance_complement_recargos": policy.recargos,
            "insurance_complement_installment": installment.number if installment else 0,
        })
