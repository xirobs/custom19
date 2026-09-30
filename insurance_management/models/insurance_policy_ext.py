# -*- coding: utf-8 -*-
"""Extensiones de la póliza: cartera (clave de agente), retiros, semáforo de atención,
estado de renovación, avisos automáticos y comunicación con el cliente."""

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

ATTENTION_LEVELS = [
    ("ok", "Al día"),
    ("warning", "Requiere seguimiento"),
    ("danger", "Atención urgente"),
]
RENEWAL_STATES = [
    ("not_due", "Vigente"),
    ("pending", "Por renovar"),
    ("in_progress", "Renovación en trámite"),
    ("renewed", "Renovada"),
    ("lost", "No renovada"),
]


class InsurancePolicy(models.Model):
    _inherit = "insurance.policy"

    agent_key_id = fields.Many2one(
        "insurance.agent.key",
        string="Clave / cartera",
        tracking=True,
        domain="[('agent_id', '=?', agent_id), ('insurance_company_id', '=?', insurance_company_id)]",
        help="Clave de agente con la que se emitió la póliza (cartera de clientes).",
    )
    withdrawal_ids = fields.One2many("insurance.policy.withdrawal", "policy_id", string="Retiros")
    commission_ids = fields.One2many("insurance.commission", "policy_id", string="Comisiones")
    commission_earned = fields.Monetary(
        string="Comisión generada",
        compute="_compute_commission_earned",
        currency_field="currency_id",
    )
    # --- Semáforo de atención ------------------------------------------------
    attention_level = fields.Selection(
        ATTENTION_LEVELS,
        string="Atención",
        compute="_compute_attention",
        store=True,
        index=True,
    )
    attention_reason = fields.Char(string="Motivo de atención", compute="_compute_attention", store=True)
    # --- Renovación -----------------------------------------------------------
    renewal_state = fields.Selection(
        RENEWAL_STATES,
        string="Renovación",
        compute="_compute_renewal_state",
        store=True,
        index=True,
    )
    renewal_notified = fields.Boolean(string="Aviso de renovación enviado", copy=False)
    # --- Últimos pagos --------------------------------------------------------
    pending_installment_count = fields.Integer(
        string="Cuotas pendientes",
        compute="_compute_pending_installment_count",
        store=True,
    )
    last_payments_notified = fields.Boolean(string="Aviso de últimos pagos enviado", copy=False)
    welcome_sent = fields.Boolean(string="Bienvenida enviada", copy=False)

    # ------------------------------------------------------------------
    def _compute_commission_earned(self):
        for policy in self:
            policy.commission_earned = sum(
                policy.commission_ids.filtered(lambda c: c.state != "cancelled").mapped("amount")
            )

    @api.depends("installment_ids.state")
    def _compute_pending_installment_count(self):
        for policy in self:
            policy.pending_installment_count = len(
                policy.installment_ids.filtered(lambda l: l.state in ("pending", "invoiced", "overdue"))
            )

    @api.depends(
        "state",
        "end_date",
        "renewal_policy_ids",
        "renewal_policy_ids.state",
    )
    def _compute_renewal_state(self):
        today = fields.Date.context_today(self)
        horizon = today + relativedelta(days=60)
        for policy in self:
            renewals = policy.renewal_policy_ids.filtered(lambda p: p.state != "cancelled")
            if renewals.filtered(lambda p: p.state in ("confirmed", "expired", "done")):
                policy.renewal_state = "renewed"
            elif renewals:
                policy.renewal_state = "in_progress"
            elif policy.state not in ("confirmed", "expired") or not policy.end_date:
                policy.renewal_state = "not_due"
            elif policy.end_date < today - relativedelta(days=30):
                policy.renewal_state = "lost"
            elif policy.end_date <= horizon:
                policy.renewal_state = "pending"
            else:
                policy.renewal_state = "not_due"

    @api.depends(
        "state",
        "overdue_count",
        "installment_ids.collection_status",
        "renewal_state",
        "end_date",
        "document_pending_count",
        "claim_open_count",
    )
    def _compute_attention(self):
        today = fields.Date.context_today(self)
        for policy in self:
            danger, warning = [], []
            if policy.state in ("confirmed", "expired"):
                lines = policy.installment_ids
                if lines.filtered(lambda l: l.collection_status == "overdue"):
                    danger.append(_("Pago vencido"))
                elif lines.filtered(lambda l: l.collection_status == "due_soon"):
                    warning.append(_("Pago por vencer"))
                if policy.renewal_state == "lost":
                    danger.append(_("No renovada"))
                elif policy.renewal_state == "pending":
                    if policy.end_date and policy.end_date <= today + relativedelta(days=15):
                        danger.append(_("Renovación urgente"))
                    else:
                        warning.append(_("Por renovar"))
                if policy.claim_open_count:
                    warning.append(_("Siniestro en proceso"))
                if policy.document_pending_count:
                    warning.append(_("Documentos / firma pendiente"))
            elif policy.state == "draft" and policy.document_pending_count:
                warning.append(_("Documentos / firma pendiente"))
            if danger:
                policy.attention_level = "danger"
            elif warning:
                policy.attention_level = "warning"
            else:
                policy.attention_level = "ok"
            policy.attention_reason = ", ".join(danger + warning) or False

    # ------------------------------------------------------------------
    @api.onchange("agent_id", "insurance_company_id", "policy_type_id")
    def _onchange_agent_key(self):
        if self.agent_id and (not self.agent_key_id or self.agent_key_id.agent_id != self.agent_id):
            keys = self.agent_id.key_ids.filtered(
                lambda k: not self.insurance_company_id or k.insurance_company_id == self.insurance_company_id
            )
            self.agent_key_id = keys[:1]
        if self.agent_id:
            rule = self.agent_id._get_commission_rule(self.insurance_company_id, self.policy_type_id)
            if rule:
                self.commission_rate = rule.rate_first_year

    @api.onchange("agent_key_id")
    def _onchange_agent_key_id(self):
        if self.agent_key_id:
            self.agent_id = self.agent_key_id.agent_id
            if not self.insurance_company_id:
                self.insurance_company_id = self.agent_key_id.insurance_company_id

    # ------------------------------------------------------------------
    # Comunicación con el cliente
    # ------------------------------------------------------------------
    def action_send_message(self):
        return self.env["insurance.message.wizard"].action_open(self)

    def action_register_customer_payment(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Registrar pago"),
            "res_model": "insurance.payment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_policy_id": self.id, "default_partner_id": self.partner_id.id},
        }

    def action_create_task(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Nueva tarea"),
            "res_model": "insurance.task",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_policy_id": self.id,
                "default_partner_id": self.partner_id.id,
                "default_name": _("Seguimiento póliza %s") % self.name,
            },
        }

    def action_send_renewal_notice(self):
        template = self.env.ref("insurance_management.mail_template_policy_renewal", raise_if_not_found=False)
        for policy in self:
            if template and policy.partner_id.email:
                policy.message_post_with_source(template, message_type="comment", subtype_xmlid="mail.mt_comment")
            policy.renewal_notified = True
        return True

    def action_open_withdrawals(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Retiros"),
            "res_model": "insurance.policy.withdrawal",
            "view_mode": "list,form",
            "domain": [("policy_id", "=", self.id)],
            "context": {"default_policy_id": self.id},
        }

    def action_confirm(self):
        res = super().action_confirm()
        template = self.env.ref("insurance_management.mail_template_policy_welcome", raise_if_not_found=False)
        auto = self.env["ir.config_parameter"].sudo().get_param("insurance_management.auto_welcome")
        if template and auto:
            for policy in self.filtered(lambda p: p.partner_id.email and not p.welcome_sent):
                policy.message_post_with_source(template, message_type="comment", subtype_xmlid="mail.mt_comment")
                policy.welcome_sent = True
        return res

    # ------------------------------------------------------------------
    # Tareas programadas
    # ------------------------------------------------------------------
    @api.model
    def _cron_refresh_status(self):
        """Recalcula los campos que dependen de la fecha de hoy (renovación y semáforo)."""
        policies = self.search([("state", "in", ["draft", "confirmed", "expired"])])
        policies._compute_renewal_state()
        policies._compute_lifecycle_stage()
        policies._compute_attention()

    @api.model
    def _cron_policy_notifications(self):
        params = self.env["ir.config_parameter"].sudo()
        today = fields.Date.context_today(self)
        # Renovación: aviso N días antes del fin de vigencia
        if params.get_param("insurance_management.auto_renewal"):
            days = int(params.get_param("insurance_management.renewal_notice_days", 30))
            policies = self.search([
                ("state", "=", "confirmed"),
                ("renewal_notified", "=", False),
                ("renewal_state", "=", "pending"),
                ("end_date", "<=", today + relativedelta(days=days)),
            ])
            policies.action_send_renewal_notice()
        # Últimos pagos: cuando quedan 2 o menos cuotas por pagar
        if params.get_param("insurance_management.auto_last_payments"):
            template = self.env.ref("insurance_management.mail_template_policy_last_payments", raise_if_not_found=False)
            policies = self.search([
                ("state", "=", "confirmed"),
                ("last_payments_notified", "=", False),
                ("pending_installment_count", ">", 0),
                ("pending_installment_count", "<=", 2),
                ("installment_count", ">", 2),
            ])
            for policy in policies:
                if template and policy.partner_id.email:
                    policy.message_post_with_source(template, message_type="comment", subtype_xmlid="mail.mt_comment")
                policy.last_payments_notified = True
        # Firma de contrato pendiente: recordatorio semanal
        if params.get_param("insurance_management.auto_signature"):
            template = self.env.ref("insurance_management.mail_template_signature_reminder", raise_if_not_found=False)
            documents = self.env["insurance.document"].search([
                ("requires_signature", "=", True),
                ("state", "in", ["pending", "uploaded"]),
                ("policy_id.state", "in", ["draft", "confirmed"]),
                ("claim_id", "=", False),
            ])
            for policy in documents.mapped("policy_id"):
                last = policy.signature_reminder_date
                if last and last > today - relativedelta(days=7):
                    continue
                if template and policy.partner_id.email:
                    policy.message_post_with_source(template, message_type="comment", subtype_xmlid="mail.mt_comment")
                policy.signature_reminder_date = today

    signature_reminder_date = fields.Date(string="Último recordatorio de firma", copy=False)

    unsigned_document_names = fields.Char(compute="_compute_unsigned_document_names")

    def _compute_unsigned_document_names(self):
        for policy in self:
            docs = policy.document_ids.filtered(
                lambda d: not d.claim_id and d.requires_signature and d.state != "signed"
            )
            policy.unsigned_document_names = ", ".join(docs.mapped("document_type_id.name"))

    def _get_benefits_summary(self):
        """Texto de beneficios para las plantillas."""
        self.ensure_one()
        parts = []
        for benefit in self.benefit_ids:
            label = benefit.benefit_id.display_name
            if benefit.insured_amount:
                label += " — %s %s" % ("{:,.2f}".format(benefit.insured_amount), self.currency_id.name or "")
            parts.append(label)
        return parts
