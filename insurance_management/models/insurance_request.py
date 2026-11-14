# -*- coding: utf-8 -*-
"""Expediente de solicitud: lo que el asesor captura cuando el prospecto se gana y que
Emisiones necesita ver en la etapa «Solicitud» para iniciar el trámite con la aseguradora."""

import re

from odoo import api, fields, models, _

RFC_RE = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")
CURP_RE = re.compile(r"^[A-Z][AEIOUX][A-Z]{2}\d{6}[HMX][A-Z]{5}[A-Z0-9]\d$")

DISEASE = [
    ("no", "No"),
    ("current", "Sí, la tiene actualmente"),
    ("past", "Sí, la tuvo"),
]

REQUEST_FIELDS = [
    "req_full_name", "req_height", "req_weight", "req_employer", "req_job_years",
    "req_job_position", "req_rfc", "req_curp", "req_address_proof_ids", "req_is_gmm",
    "req_prev_policy", "req_prev_policy_ids", "req_payment_proof_ids",
    "req_has_disease", "req_disease_detail", "req_missing", "req_complete",
]


class CrmLeadRequest(models.Model):
    _inherit = "crm.lead"

    req_full_name = fields.Char(string="Nombre completo")
    req_height = fields.Float(string="Estatura (m)", digits=(3, 2))
    req_weight = fields.Float(string="Peso (kg)", digits=(5, 1))
    req_employer = fields.Char(string="¿Dónde trabaja?")
    req_job_years = fields.Char(string="Tiempo trabajando ahí", help="Ej. 3 años, 8 meses")
    req_job_position = fields.Char(string="Puesto")
    req_rfc = fields.Char(string="RFC")
    req_curp = fields.Char(string="CURP")
    req_address_proof_ids = fields.Many2many(
        "ir.attachment", "crm_lead_req_address_rel", "lead_id", "attachment_id",
        string="Comprobante de domicilio")
    req_is_gmm = fields.Boolean(
        string="Es Gastos Médicos", compute="_compute_req_is_gmm", store=True,
        help="Se marca solo cuando el ramo es de Gastos Médicos (se renueva cada año).")
    req_prev_policy = fields.Char(string="Póliza anterior", help="Aseguradora y número de la póliza anterior")
    req_prev_policy_ids = fields.Many2many(
        "ir.attachment", "crm_lead_req_prev_policy_rel", "lead_id", "attachment_id",
        string="Pólizas anteriores (archivos)")
    req_payment_proof_ids = fields.Many2many(
        "ir.attachment", "crm_lead_req_payment_rel", "lead_id", "attachment_id",
        string="Comprobantes de pago de la póliza anterior")
    req_has_disease = fields.Selection(DISEASE, string="¿Tiene o tuvo alguna enfermedad?")
    req_disease_detail = fields.Text(string="¿Cuál y desde cuándo?")
    req_missing = fields.Char(string="Falta en el expediente", compute="_compute_req_missing")
    req_complete = fields.Boolean(string="Expediente completo", compute="_compute_req_missing")
    req_active = fields.Boolean(compute="_compute_req_active",
                                help="El expediente se activa cuando el prospecto cae en una etapa ganada.")

    @api.depends("insurance_policy_type_id", "insurance_policy_type_id.renewal_kind", "insurance_policy_type_id.name")
    def _compute_req_is_gmm(self):
        for lead in self:
            ramo = lead.insurance_policy_type_id
            name = (ramo.name or "").lower()
            lead.req_is_gmm = bool(ramo) and (ramo.renewal_kind == "renewal" or "médic" in name or "medic" in name)

    @api.depends("stage_id", "stage_id.is_won", "insurance_policy_id")
    def _compute_req_active(self):
        for lead in self:
            lead.req_active = bool(lead.stage_id.is_won or lead.insurance_policy_id)

    @api.depends(*[f for f in REQUEST_FIELDS if f not in ("req_missing", "req_complete")])
    def _compute_req_missing(self):
        for lead in self:
            missing = lead._req_missing_list()
            lead.req_missing = ", ".join(missing)
            lead.req_complete = not missing

    def _req_missing_list(self):
        self.ensure_one()
        missing = []
        checks = [
            ("req_full_name", _("nombre completo")), ("req_height", _("estatura")), ("req_weight", _("peso")),
            ("req_employer", _("dónde trabaja")), ("req_job_years", _("antigüedad")),
            ("req_job_position", _("puesto")), ("req_rfc", _("RFC")), ("req_curp", _("CURP")),
            ("req_address_proof_ids", _("comprobante de domicilio")), ("req_has_disease", _("enfermedades")),
        ]
        for field, label in checks:
            if not self[field]:
                missing.append(label)
        if self.req_rfc and not RFC_RE.match(self.req_rfc):
            missing.append(_("RFC con formato inválido"))
        if self.req_curp and not CURP_RE.match(self.req_curp):
            missing.append(_("CURP con formato inválido"))
        if self.req_has_disease in ("current", "past") and not (self.req_disease_detail or "").strip():
            missing.append(_("detalle de la enfermedad"))
        if self.req_is_gmm:
            if not self.req_prev_policy and not self.req_prev_policy_ids:
                missing.append(_("póliza anterior (o «ninguna»)"))
            if self.req_prev_policy_ids and not self.req_payment_proof_ids:
                missing.append(_("comprobantes de pago de la póliza anterior"))
        return missing

    @api.onchange("req_rfc", "req_curp")
    def _onchange_req_upper(self):
        for lead in self:
            if lead.req_rfc:
                lead.req_rfc = lead.req_rfc.strip().upper().replace(" ", "")
            if lead.req_curp:
                lead.req_curp = lead.req_curp.strip().upper().replace(" ", "")

    def _req_prefill(self):
        """Al ganarse, el nombre completo arranca con el del contacto para no volver a escribirlo."""
        for lead in self:
            if not lead.req_full_name:
                name = lead.contact_name or lead.partner_id.name or lead.partner_name
                if name:
                    lead.req_full_name = name

    @staticmethod
    def _req_norm_vals(vals):
        for key in ("req_rfc", "req_curp"):
            if vals.get(key):
                vals[key] = vals[key].strip().upper().replace(" ", "")
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._req_norm_vals(v) for v in vals_list])

    def write(self, vals):
        res = super().write(self._req_norm_vals(vals))
        if vals.get("stage_id"):
            self.filtered(lambda l: l.stage_id.is_won and l.is_insurance_opportunity)._req_prefill()
        # los archivos quedan ligados al prospecto (y se ven también desde la póliza)
        for key in ("req_address_proof_ids", "req_prev_policy_ids", "req_payment_proof_ids"):
            if key in vals:
                for lead in self:
                    lead[key].filtered(lambda a: not a.res_id).sudo().write(
                        {"res_model": "crm.lead", "res_id": lead.id})
        return res


class InsurancePolicyRequest(models.Model):
    _inherit = "insurance.policy"

    req_full_name = fields.Char(related="lead_id.req_full_name", readonly=False)
    req_height = fields.Float(related="lead_id.req_height", readonly=False)
    req_weight = fields.Float(related="lead_id.req_weight", readonly=False)
    req_employer = fields.Char(related="lead_id.req_employer", readonly=False)
    req_job_years = fields.Char(related="lead_id.req_job_years", readonly=False)
    req_job_position = fields.Char(related="lead_id.req_job_position", readonly=False)
    req_rfc = fields.Char(related="lead_id.req_rfc", readonly=False)
    req_curp = fields.Char(related="lead_id.req_curp", readonly=False)
    req_address_proof_ids = fields.Many2many(related="lead_id.req_address_proof_ids", readonly=False)
    req_is_gmm = fields.Boolean(related="lead_id.req_is_gmm")
    req_prev_policy = fields.Char(related="lead_id.req_prev_policy", readonly=False)
    req_prev_policy_ids = fields.Many2many(related="lead_id.req_prev_policy_ids", readonly=False)
    req_payment_proof_ids = fields.Many2many(related="lead_id.req_payment_proof_ids", readonly=False)
    req_has_disease = fields.Selection(related="lead_id.req_has_disease", readonly=False)
    req_disease_detail = fields.Text(related="lead_id.req_disease_detail", readonly=False)
    req_missing = fields.Char(related="lead_id.req_missing")
    req_complete = fields.Boolean(related="lead_id.req_complete")
