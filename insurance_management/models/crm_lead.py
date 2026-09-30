# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class CrmLead(models.Model):
    _inherit = "crm.lead"

    is_insurance_opportunity = fields.Boolean(
        string="Oportunidad de seguros",
        default=False,
        index=True,
        copy=True,
    )
    insurance_scheme_id = fields.Many2one(
        "insurance.scheme",
        string="Esquema de seguro",
    )
    insurance_policy_type_id = fields.Many2one(
        "insurance.policy.type",
        string="Ramo",
        index=True,
    )
    insurance_offer_id = fields.Many2one(
        "insurance.offer",
        string="Oferta / sub-ramo",
        domain="[('ramo_id', '=', insurance_policy_type_id), ('parent_id', '=', False)]",
    )
    insurance_origin = fields.Selection(
        [
            ("new", "Cliente nuevo"),
            ("cross_sell", "Venta cruzada"),
            ("referral", "Referido"),
            ("renewal", "Renovación"),
        ],
        string="Origen del prospecto",
        default="new",
    )
    insurance_company_id = fields.Many2one(
        "insurance.company",
        string="Compañía de seguros",
    )
    insurance_policy_id = fields.Many2one(
        "insurance.policy",
        string="Póliza generada",
        copy=False,
    )
    estimated_premium = fields.Monetary(
        string="Prima estimada",
        currency_field="company_currency",
    )
    estimated_insured_amount = fields.Monetary(
        string="Suma asegurada estimada",
        currency_field="company_currency",
    )
    insurance_duration_years = fields.Integer(string="Duración estimada (años)", default=1)
    insurance_duration_months = fields.Integer(string="Duración estimada (meses)", default=12)
    insurance_coverage_mode = fields.Selection(
        [
            ("individual", "Individual"),
            ("family", "Familiar"),
        ],
        string="Contratación",
        default="individual",
    )

    def _auto_init(self):
        res = super()._auto_init()
        self.env.cr.execute(
            """
            UPDATE crm_lead
               SET is_insurance_opportunity = true
             WHERE COALESCE(is_insurance_opportunity, false) = false
               AND (insurance_scheme_id IS NOT NULL OR insurance_policy_id IS NOT NULL)
            """
        )
        self.env.cr.execute(
            """
            UPDATE crm_lead
               SET is_insurance_opportunity = false
             WHERE is_insurance_opportunity IS NULL
            """
        )
        return res

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if (
                self.env.context.get("default_is_insurance_opportunity")
                or vals.get("is_insurance_opportunity")
                or vals.get("insurance_scheme_id")
                or vals.get("insurance_policy_id")
            ):
                vals["is_insurance_opportunity"] = True
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("insurance_scheme_id") or vals.get("insurance_policy_id"):
            vals = dict(vals, is_insurance_opportunity=True)
        return super().write(vals)

    def action_send_insurance_message(self):
        return self.env["insurance.message.wizard"].action_open(self)

    @api.onchange("insurance_policy_type_id")
    def _onchange_insurance_ramo(self):
        if self.insurance_policy_type_id:
            self.is_insurance_opportunity = True
            if self.insurance_offer_id.ramo_id != self.insurance_policy_type_id:
                self.insurance_offer_id = False
            if self.insurance_scheme_id.policy_type_id != self.insurance_policy_type_id:
                self.insurance_scheme_id = self.env["insurance.scheme"].search(
                    [("policy_type_id", "=", self.insurance_policy_type_id.id)], limit=1
                )

    @api.onchange("insurance_scheme_id")
    def _onchange_insurance_scheme_id(self):
        if self.insurance_scheme_id:
            if not self.insurance_policy_type_id:
                self.insurance_policy_type_id = self.insurance_scheme_id.policy_type_id
            self.is_insurance_opportunity = True
            if self.insurance_scheme_id.insurance_company_id and not self.insurance_company_id:
                self.insurance_company_id = self.insurance_scheme_id.insurance_company_id

    def action_create_insurance_policy(self):
        self.ensure_one()
        if self.insurance_policy_id:
            return self.action_open_insurance_policy()
        if not self.partner_id:
            raise UserError(_("Asigne un cliente a la oportunidad antes de crear la póliza."))
        if not self.insurance_scheme_id and not self.insurance_policy_type_id:
            raise UserError(_("Seleccione el ramo del seguro."))
        policy = self.env["insurance.policy"].create({
            "partner_id": self.partner_id.id,
            "scheme_id": self.insurance_scheme_id.id or False,
            "policy_type_id": self.insurance_policy_type_id.id or self.insurance_scheme_id.policy_type_id.id,
            "offer_id": self.insurance_offer_id.id or False,
            "insurance_company_id": self.insurance_company_id.id,
            "policy_amount": self.estimated_premium or self.insurance_scheme_id.amount_min or 0.0,
            "insured_amount": self.estimated_insured_amount or self.insurance_scheme_id.insured_amount_min or 0.0,
            "duration_months": self.insurance_duration_months or (self.insurance_duration_years or 1) * 12,
            "coverage_mode": self.insurance_coverage_mode or "individual",
            "lead_id": self.id,
            "user_id": self.user_id.id or self.env.user.id,
        })
        self.insurance_policy_id = policy.id
        self.message_post(body=_("Se creó la póliza %s desde esta oportunidad.") % policy.name)
        return self.action_open_insurance_policy()

    def action_open_insurance_policy(self):
        self.ensure_one()
        if not self.insurance_policy_id:
            raise UserError(_("Esta oportunidad aún no tiene póliza."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Póliza"),
            "res_model": "insurance.policy",
            "res_id": self.insurance_policy_id.id,
            "view_mode": "form",
        }
