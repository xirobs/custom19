# -*- coding: utf-8 -*-
"""Funciones del equipo (asistentes del agente): emisiones, trámites, siniestros, cobranza y CRM.

Cada asunto puede asignarse a una persona del equipo (``team_user_id``); esa persona
lo trabaja desde el portal del equipo (/equipo) aunque sea usuario de portal.
"""

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

EMISSION_STAGES = [
    ("request", "Solicitud"),
    ("documents", "Requisitos"),
    ("submitted", "En aseguradora"),
    ("issued", "Emitida"),
    ("delivered", "Entregada"),
]
DELIVERY_METHODS = [
    ("hand", "En persona"),
    ("email", "Correo"),
    ("whatsapp", "WhatsApp"),
    ("courier", "Mensajería"),
]
PROCEDURE_STATES = [
    ("new", "Nuevo"),
    ("in_progress", "En proceso"),
    ("insurer", "En aseguradora"),
    ("done", "Terminado"),
    ("cancelled", "Cancelado"),
]
PORTAL_BLOCKS = ("emission", "procedure", "claim", "collection", "crm")


class InsuranceTeamAssignMixin(models.AbstractModel):
    """Al asignar un asunto a alguien del equipo, esa persona lo sigue y recibe avisos."""

    _name = "insurance.team.assign.mixin"
    _description = "Asignación al equipo de seguros"

    team_user_id = fields.Many2one(
        "res.users",
        string="Asignado a (equipo)",
        tracking=True,
        index=True,
        help="Persona del equipo que trabaja este asunto desde el portal del equipo.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._team_subscribe()
        return records

    def write(self, vals):
        res = super().write(vals)
        if vals.get("team_user_id"):
            self._team_subscribe()
        return res

    def _team_subscribe(self):
        for record in self.filtered("team_user_id"):
            if hasattr(record, "message_subscribe"):
                record.sudo().message_subscribe(partner_ids=record.team_user_id.partner_id.ids)


class ResUsers(models.Model):
    _inherit = "res.users"

    ins_portal_emission = fields.Boolean(string="Portal: emisiones")
    ins_portal_procedure = fields.Boolean(string="Portal: trámites")
    ins_portal_claim = fields.Boolean(string="Portal: siniestros")
    ins_portal_collection = fields.Boolean(string="Portal: cobranza")
    ins_portal_crm = fields.Boolean(string="Portal: CRM y prospectos")
    ins_portal_all = fields.Boolean(
        string="Ve los asuntos de todo el equipo",
        help="Supervisión: además de lo asignado a esta persona, ve y puede trabajar lo de los demás.",
    )
    ins_portal_access = fields.Boolean(
        string="Portal del equipo de seguros",
        compute="_compute_ins_portal_access",
    )

    @api.depends(*["ins_portal_%s" % b for b in PORTAL_BLOCKS])
    def _compute_ins_portal_access(self):
        for user in self:
            user.ins_portal_access = any(user["ins_portal_%s" % b] for b in PORTAL_BLOCKS)

    def _ins_portal_can(self, block):
        self.ensure_one()
        return bool(self.sudo()["ins_portal_%s" % block])

    def action_insurance_make_portal(self):
        """Convierte un usuario interno en usuario de portal (Odoo 19 ya no muestra el tipo de usuario).

        Conserva sus bloques del portal del equipo y deja de ocupar licencia.
        """
        if not self.env.user.has_group("insurance_management.group_insurance_manager"):
            raise UserError(_("Solo un administrador de Seguros puede cambiar el tipo de usuario."))
        portal = self.env.ref("base.group_portal")
        for user in self:
            if user == self.env.user:
                raise UserError(_("No puedes convertir tu propio usuario en usuario de portal."))
            if user.has_group("base.group_system"):
                raise UserError(_("%s es administrador del sistema: quítale ese permiso antes de pasarlo a portal.") % user.name)
            if user.share:
                continue
            user.sudo().write({"group_ids": [(6, 0, [portal.id])]})
            user.partner_id.sudo().message_post(
                body=_("%s pasó a usuario de portal (portal del equipo de seguros).") % user.name,
                subtype_xmlid="mail.mt_note",
            )
        return True


class InsuranceProcedureType(models.Model):
    _name = "insurance.procedure.type"
    _description = "Tipo de trámite"
    _order = "sequence, name"

    name = fields.Char(string="Trámite", required=True, translate=True)
    code = fields.Char(string="Código")
    sequence = fields.Integer(default=10)
    days = fields.Integer(string="Plazo (días)", default=5, help="Días para resolverlo; propone la fecha límite.")
    icon = fields.Char(string="Icono", default="fa-file-text-o", help="Icono de Font Awesome para el portal.")
    requirements = fields.Text(string="Requisitos", help="Qué hay que pedir al cliente; se muestra en el portal.")
    active = fields.Boolean(default=True)


class InsuranceProcedure(models.Model):
    _name = "insurance.procedure"
    _description = "Trámite de póliza"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_deadline, id desc"

    name = fields.Char(string="Folio", default=lambda self: _("Nuevo"), copy=False, readonly=True, index=True)
    type_id = fields.Many2one("insurance.procedure.type", string="Trámite", required=True, tracking=True)
    policy_id = fields.Many2one("insurance.policy", string="Póliza", tracking=True, index=True)
    partner_id = fields.Many2one(
        "res.partner", string="Cliente", compute="_compute_partner_id", store=True, readonly=False,
        tracking=True, index=True,
    )
    user_id = fields.Many2one(
        "res.users", string="Asignado a", default=lambda self: self.env.user, tracking=True, index=True,
    )
    state = fields.Selection(
        PROCEDURE_STATES, string="Estado", default="new", required=True, tracking=True, index=True,
        group_expand="_expand_states",
    )
    date_deadline = fields.Date(string="Fecha límite", tracking=True, index=True)
    date_done = fields.Date(string="Terminado el", copy=False)
    insurer_folio = fields.Char(string="Folio de la aseguradora", tracking=True)
    description = fields.Text(string="Qué solicita el cliente")
    result = fields.Text(string="Resultado")
    requirements = fields.Text(related="type_id.requirements")
    is_late = fields.Boolean(string="Atrasado", compute="_compute_is_late", search="_search_is_late")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)

    @api.depends("policy_id")
    def _compute_partner_id(self):
        for proc in self:
            if proc.policy_id:
                proc.partner_id = proc.policy_id.partner_id

    def _compute_is_late(self):
        today = fields.Date.context_today(self)
        for proc in self:
            proc.is_late = bool(
                proc.state not in ("done", "cancelled") and proc.date_deadline and proc.date_deadline < today
            )

    def _search_is_late(self, operator, value):
        domain = [("state", "not in", ["done", "cancelled"]), ("date_deadline", "<", fields.Date.context_today(self))]
        positive = (operator == "=") == bool(value)
        return domain if positive else ["!", "&"] + domain

    def _expand_states(self, states, domain):
        return [key for key, _label in PROCEDURE_STATES if key != "cancelled"]

    @api.model_create_multi
    def create(self, vals_list):
        Type = self.env["insurance.procedure.type"]
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == _("Nuevo"):
                vals["name"] = self.env["ir.sequence"].next_by_code("insurance.procedure") or _("Nuevo")
            if not vals.get("date_deadline") and vals.get("type_id"):
                days = Type.browse(vals["type_id"]).days or 5
                vals["date_deadline"] = fields.Date.context_today(self) + relativedelta(days=days)
        records = super().create(vals_list)
        for proc in records.filtered("user_id"):
            proc.message_subscribe(partner_ids=proc.user_id.partner_id.ids)
        return records

    def write(self, vals):
        if vals.get("state") == "done":
            vals.setdefault("date_done", fields.Date.context_today(self))
        elif vals.get("state") in ("new", "in_progress", "insurer"):
            vals.setdefault("date_done", False)
        res = super().write(vals)
        if vals.get("user_id"):
            for proc in self:
                proc.message_subscribe(partner_ids=proc.user_id.partner_id.ids)
        return res

    def action_start(self):
        self.write({"state": "in_progress"})

    def action_to_insurer(self):
        self.write({"state": "insurer"})

    def action_done(self):
        self.write({"state": "done"})

    def action_cancel(self):
        self.write({"state": "cancelled"})

    def action_reopen(self):
        self.write({"state": "in_progress"})


class InsurancePolicy(models.Model):
    _name = "insurance.policy"
    _inherit = ["insurance.policy", "insurance.team.assign.mixin"]

    emission_stage = fields.Selection(
        EMISSION_STAGES,
        string="Etapa de emisión",
        default="request",
        tracking=True,
        index=True,
        group_expand="_expand_emission_stage",
        help="Seguimiento de la emisión hasta entregar la póliza al cliente.",
    )
    delivery_date = fields.Date(string="Fecha de entrega", tracking=True, copy=False)
    delivery_method = fields.Selection(DELIVERY_METHODS, string="Entregada por", copy=False)
    procedure_ids = fields.One2many("insurance.procedure", "policy_id", string="Portal: trámites")
    procedure_count = fields.Integer(compute="_compute_procedure_count")

    def _expand_emission_stage(self, stages, domain):
        return [key for key, _label in EMISSION_STAGES]

    def _compute_procedure_count(self):
        data = self.env["insurance.procedure"]._read_group(
            [("policy_id", "in", self.ids)], ["policy_id"], ["__count"]
        )
        counts = {policy.id: count for policy, count in data}
        for policy in self:
            policy.procedure_count = counts.get(policy.id, 0)

    def write(self, vals):
        if vals.get("state") == "confirmed" and "emission_stage" not in vals:
            early = self.filtered(lambda p: p.emission_stage in ("request", "documents", "submitted"))
            if early:
                super(InsurancePolicy, early).write({"emission_stage": "issued"})
        if vals.get("emission_stage") == "delivered":
            vals.setdefault("delivery_date", fields.Date.context_today(self))
        return super().write(vals)

    def action_emission_next(self):
        keys = [key for key, _label in EMISSION_STAGES]
        for policy in self:
            index = keys.index(policy.emission_stage or "request")
            if index < len(keys) - 1:
                policy.emission_stage = keys[index + 1]

    def action_open_procedures(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Trámites"),
            "res_model": "insurance.procedure",
            "view_mode": "list,kanban,form",
            "domain": [("policy_id", "=", self.id)],
            "context": {"default_policy_id": self.id},
        }


class InsuranceClaim(models.Model):
    _name = "insurance.claim"
    _inherit = ["insurance.claim", "insurance.team.assign.mixin"]


class CrmLead(models.Model):
    _name = "crm.lead"
    _inherit = ["crm.lead", "insurance.team.assign.mixin"]

    insurance_lost_note = fields.Char(string="Motivo (perdido / publicidad)")

    def action_insurance_to_emission(self):
        """Prospecto ganado → solicitud de emisión (póliza en borrador) para el equipo."""
        self.ensure_one()
        if self.insurance_policy_id:
            return self.insurance_policy_id
        partner = self.partner_id
        if not partner:
            if not (self.contact_name or self.partner_name or self.name):
                raise UserError(_("Indique el nombre del cliente."))
            partner = self.env["res.partner"].create({
                "name": self.contact_name or self.partner_name or self.name,
                "email": self.email_from or False,
                "phone": self.phone or False,
            })
            self.partner_id = partner
        vals = {
            "partner_id": partner.id,
            "lead_id": self.id,
            "policy_type_id": self.insurance_policy_type_id.id or False,
            "offer_id": self.insurance_offer_id.id or False,
            "insurance_company_id": self.insurance_company_id.id or False,
            "policy_amount": self.expected_revenue or 0.0,
            "team_user_id": (self.team_user_id or self.env.user).id,
            "emission_stage": "request",
        }
        policy = self.env["insurance.policy"].create({k: v for k, v in vals.items() if v is not False or k == "policy_amount"})
        self.insurance_policy_id = policy
        self.message_post(body=_("Se envió a emisión: %s") % policy.display_name)
        return policy
