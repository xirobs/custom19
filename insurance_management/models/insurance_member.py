# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class InsurancePolicyMember(models.Model):
    _name = "insurance.policy.member"
    _description = "Asegurado de la póliza"
    _order = "is_holder desc, id"

    policy_id = fields.Many2one(
        "insurance.policy",
        string="Póliza",
        required=True,
        ondelete="cascade",
        index=True,
    )
    partner_id = fields.Many2one("res.partner", string="Contacto")
    name = fields.Char(
        string="Nombre",
        compute="_compute_name",
        store=True,
        readonly=False,
    )
    insured_role = fields.Selection(
        [
            ("insured", "Asegurado"),
            ("beneficiary", "Beneficiario"),
        ],
        string="Tipo de asegurado",
        required=True,
        default="insured",
        index=True,
    )
    relationship = fields.Selection(
        [
            ("holder", "Titular"),
            ("spouse", "Cónyuge / pareja"),
            ("child", "Hijo(a)"),
            ("parent", "Padre / madre"),
            ("other", "Otro dependiente"),
        ],
        string="Parentesco",
        required=True,
        default="holder",
    )
    is_holder = fields.Boolean(string="Es titular", compute="_compute_is_holder", store=True)
    birth_date = fields.Date(string="Fecha de nacimiento")
    gender = fields.Selection(
        [("male", "Hombre"), ("female", "Mujer"), ("other", "Otro")],
        string="Sexo",
    )
    mx_rfc = fields.Char(string="RFC")
    mx_curp = fields.Char(string="CURP")
    phone = fields.Char(string="Teléfono")
    email = fields.Char(string="Correo")
    seniority_date = fields.Date(
        string="Reconocimiento de antigüedad",
        help="Fecha desde la que se reconoce la antigüedad del asegurado en la póliza.",
    )
    premium_share = fields.Float()
    note = fields.Char(string="Observaciones")

    @api.depends("relationship")
    def _compute_is_holder(self):
        for member in self:
            member.is_holder = member.relationship == "holder"

    @api.depends("partner_id", "partner_id.name")
    def _compute_name(self):
        for member in self:
            if member.partner_id:
                member.name = member.partner_id.name

    @api.constrains("partner_id", "name")
    def _check_member_identity(self):
        for member in self:
            if not member.partner_id and not member.name:
                raise ValidationError(_("Indique el contacto o el nombre de la parte asegurada."))

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if not self.partner_id:
            return
        self.name = self.partner_id.name
        self.phone = self.partner_id._insurance_phone()
        self.email = self.partner_id.email
        self.mx_rfc = self.partner_id.vat
        self.mx_curp = self.partner_id.mx_curp
        self.birth_date = self.partner_id.mx_birth_date
