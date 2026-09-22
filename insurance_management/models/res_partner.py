# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ResPartner(models.Model):
    _inherit = "res.partner"

    mx_curp = fields.Char(string="CURP")
    mx_birth_date = fields.Date(string="Fecha de nacimiento")
    insurance_cfdi_usage = fields.Selection(
        [
            ("G03", "G03 — Gastos en general"),
            ("D07", "D07 — Primas de seguros de gastos médicos"),
            ("S01", "S01 — Sin efectos fiscales"),
        ],
        string="Uso CFDI predeterminado",
        default="G03",
    )
    insurance_fiscal_regime = fields.Selection(
        [
            ("601", "601 — General de Ley Personas Morales"),
            ("603", "603 — Personas Morales con Fines no Lucrativos"),
            ("605", "605 — Sueldos y Salarios e Ingresos Asimilados"),
            ("606", "606 — Arrendamiento"),
            ("612", "612 — Personas Físicas con Actividades Empresariales"),
            ("616", "616 — Sin obligaciones fiscales"),
            ("626", "626 — Régimen Simplificado de Confianza"),
        ],
        string="Régimen fiscal SAT",
    )
    insurance_policy_ids = fields.One2many(
        "insurance.policy",
        "partner_id",
        string="Pólizas",
    )
    insurance_claim_ids = fields.One2many(
        "insurance.claim",
        "partner_id",
        string="Siniestros",
    )
    insurance_claim_count = fields.Integer(compute="_compute_insurance_claim_count")
    insurance_policy_count = fields.Integer(
        string="Pólizas contratadas",
        compute="_compute_insurance_policy_count",
    )
    is_insurance_customer = fields.Boolean(
        string="Cliente de seguros",
        compute="_compute_is_insurance_customer",
        store=True,
    )
    insurance_document_ids = fields.One2many(
        "insurance.document",
        "partner_id",
        string="Expediente documental",
    )
    insurance_document_count = fields.Integer(compute="_compute_insurance_document_count")
    insurance_document_pending = fields.Integer(compute="_compute_insurance_document_count")
    has_sign_module = fields.Boolean(compute="_compute_insurance_integration")
    has_documents_module = fields.Boolean(compute="_compute_insurance_integration")

    @api.depends("insurance_policy_ids")
    def _compute_insurance_policy_count(self):
        for partner in self:
            partner.insurance_policy_count = len(partner.insurance_policy_ids)

    @api.depends("insurance_policy_ids")
    def _compute_is_insurance_customer(self):
        for partner in self:
            partner.is_insurance_customer = bool(partner.insurance_policy_ids)

    @api.depends("insurance_claim_ids")
    def _compute_insurance_claim_count(self):
        for partner in self:
            partner.insurance_claim_count = len(partner.insurance_claim_ids)

    def action_open_insurance_claims(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Siniestros / casos"),
            "res_model": "insurance.claim",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }

    def _compute_insurance_document_count(self):
        for partner in self:
            docs = partner.insurance_document_ids
            partner.insurance_document_count = len(docs)
            partner.insurance_document_pending = len(docs.filtered(
                lambda d: d.state in ("pending", "uploaded", "rejected")
            ))

    def _compute_insurance_integration(self):
        has_sign = "sign.request" in self.env
        has_documents = "documents.document" in self.env
        for partner in self:
            partner.has_sign_module = has_sign
            partner.has_documents_module = has_documents

    def action_open_insurance_documents(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Expediente"),
            "res_model": "insurance.document",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }

    def action_open_documents_workspace(self):
        self.ensure_one()
        if "documents.document" not in self.env:
            raise UserError(_("Active el módulo Documentos para abrir el workspace del expediente."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Documentos"),
            "res_model": "documents.document",
            "view_mode": "kanban,list,form",
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }

    def action_open_insurance_policies(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Pólizas"),
            "res_model": "insurance.policy",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.id)],
            "context": {"default_partner_id": self.id},
        }
