# -*- coding: utf-8 -*-

from odoo import api, fields, models


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
    name = fields.Char(string="Nombre", required=True)
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
    premium_share = fields.Float(
        string="Participación de prima (%)",
        help="Porcentaje de la prima que corresponde a esta parte asegurada.",
    )
    note = fields.Char(string="Observaciones")

    @api.depends("relationship")
    def _compute_is_holder(self):
        for member in self:
            member.is_holder = member.relationship == "holder"

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if not self.partner_id:
            return
        self.name = self.partner_id.name
        self.phone = self.partner_id.mobile or self.partner_id.phone
        self.email = self.partner_id.email
        self.mx_rfc = self.partner_id.vat
        self.mx_curp = self.partner_id.mx_curp
        self.birth_date = self.partner_id.mx_birth_date
