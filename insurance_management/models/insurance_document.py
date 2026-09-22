# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class InsuranceDocumentType(models.Model):
    _name = "insurance.document.type"
    _description = "Tipo de documento de seguro"
    _order = "sequence, name"

    name = fields.Char(string="Documento", required=True)
    code = fields.Char(string="Código")
    sequence = fields.Integer(default=10)
    requires_signature = fields.Boolean(string="Requiere firma", default=True)
    usage = fields.Selection(
        [
            ("contracting", "Contratación de póliza"),
            ("claim", "Siniestro / caso médico"),
            ("both", "Ambos"),
        ],
        string="Uso",
        default="contracting",
        required=True,
    )
    claim_kind = fields.Selection(
        [
            ("all", "Todos los casos"),
            ("gmm", "Gastos médicos"),
            ("accident", "Accidente"),
            ("auto", "Automotriz"),
            ("life", "Vida"),
            ("other", "Otro"),
        ],
        string="Tipo de caso",
        default="all",
    )
    description = fields.Text(string="Instrucciones")
    active = fields.Boolean(default=True)


class InsuranceDocument(models.Model):
    _name = "insurance.document"
    _description = "Documento de póliza"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "partner_id, sequence, id"

    name = fields.Char(string="Nombre", compute="_compute_name", store=True)
    sequence = fields.Integer(default=10)
    partner_id = fields.Many2one(
        "res.partner",
        string="Contacto",
        required=True,
        ondelete="cascade",
        index=True,
    )
    policy_id = fields.Many2one(
        "insurance.policy",
        string="Póliza",
        ondelete="set null",
        index=True,
    )
    claim_id = fields.Many2one(
        "insurance.claim",
        string="Siniestro / caso",
        ondelete="cascade",
        index=True,
    )
    document_type_id = fields.Many2one(
        "insurance.document.type",
        string="Tipo de documento",
        required=True,
    )
    requires_signature = fields.Boolean(
        related="document_type_id.requires_signature",
        store=True,
    )
    attachment_ids = fields.Many2many(
        "ir.attachment",
        "insurance_document_attachment_rel",
        "document_id",
        "attachment_id",
        string="Archivos",
    )
    state = fields.Selection(
        [
            ("pending", "Pendiente"),
            ("uploaded", "Cargado"),
            ("signed", "Firmado"),
            ("rejected", "Rechazado"),
        ],
        string="Estado",
        default="pending",
        tracking=True,
        required=True,
    )
    signed = fields.Boolean(string="Firmado", tracking=True)
    signed_date = fields.Datetime(string="Fecha de firma")
    signed_by = fields.Many2one("res.users", string="Firmado por")
    rejected_reason = fields.Char(string="Motivo de rechazo")
    note = fields.Text(string="Observaciones")
    has_sign_module = fields.Boolean(compute="_compute_integration_flags")
    has_documents_module = fields.Boolean(compute="_compute_integration_flags")

    @api.depends("document_type_id", "policy_id", "claim_id", "partner_id")
    def _compute_name(self):
        for document in self:
            origin = (
                document.claim_id.name
                or document.policy_id.name
                or document.partner_id.name
                or _("Expediente")
            )
            type_name = document.document_type_id.name or _("Documento")
            document.name = "%s — %s" % (origin, type_name)

    def _compute_integration_flags(self):
        has_sign = "sign.request" in self.env
        has_documents = "documents.document" in self.env
        for document in self:
            document.has_sign_module = has_sign
            document.has_documents_module = has_documents

    @api.onchange("policy_id")
    def _onchange_policy_id(self):
        if self.policy_id and not self.partner_id:
            self.partner_id = self.policy_id.partner_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("partner_id") and vals.get("policy_id"):
                policy = self.env["insurance.policy"].browse(vals["policy_id"])
                vals["partner_id"] = policy.partner_id.id
        records = super().create(vals_list)
        records._sync_documents_workspace()
        return records

    def action_mark_uploaded(self):
        for document in self:
            document.state = "uploaded"

    def action_mark_signed(self):
        for document in self:
            document.write({
                "state": "signed",
                "signed": True,
                "signed_date": fields.Datetime.now(),
                "signed_by": self.env.user.id,
            })

    def action_reject(self):
        for document in self:
            document.write({
                "state": "rejected",
                "signed": False,
            })

    def action_reset_pending(self):
        self.write({
            "state": "pending",
            "signed": False,
            "signed_date": False,
            "signed_by": False,
        })

    def _sync_documents_workspace(self):
        if "documents.document" not in self.env:
            return
        Document = self.env["documents.document"]
        for document in self:
            for attachment in document.attachment_ids:
                existing = Document.search([("attachment_id", "=", attachment.id)], limit=1)
                if existing:
                    continue
                vals = {
                    "name": document.name or attachment.name,
                    "attachment_id": attachment.id,
                    "partner_id": document.partner_id.id,
                }
                if "owner_id" in Document._fields:
                    vals["owner_id"] = self.env.user.id
                Document.create(vals)

    def action_request_sign(self):
        self.ensure_one()
        if "sign.request" not in self.env:
            raise UserError(_("Active el módulo Firma electrónica (Sign) para enviar este documento a firmar."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Firma electrónica"),
            "res_model": "sign.request",
            "view_mode": "list,form",
            "context": {
                "default_reference": self.name,
                "default_res_model": "insurance.document",
                "default_res_id": self.id,
            },
        }

    def action_open_documents_workspace(self):
        self.ensure_one()
        self._sync_documents_workspace()
        if "documents.document" not in self.env:
            raise UserError(_("Active el módulo Documentos para ver el expediente en el workspace."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Documentos"),
            "res_model": "documents.document",
            "view_mode": "kanban,list,form",
            "domain": [("partner_id", "=", self.partner_id.id)],
            "context": {"default_partner_id": self.partner_id.id},
        }
