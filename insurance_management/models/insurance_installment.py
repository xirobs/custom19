# -*- coding: utf-8 -*-

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class InsuranceInstallment(models.Model):
    _name = "insurance.installment"
    _description = "Cuota / prima periódica"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "policy_id, number"

    name = fields.Char(string="Referencia", compute="_compute_name", store=True)
    policy_id = fields.Many2one(
        "insurance.policy",
        string="Póliza",
        required=True,
        ondelete="cascade",
        index=True,
    )
    partner_id = fields.Many2one(related="policy_id.partner_id", store=True, index=True)
    number = fields.Integer(string="N.º de cuota", required=True)
    date_from = fields.Date(string="Desde", required=True)
    date_to = fields.Date(string="Hasta", required=True)
    due_date = fields.Date(string="Fecha de vencimiento", required=True)
    amount = fields.Monetary(string="Importe", currency_field="currency_id", required=True)
    currency_id = fields.Many2one(related="policy_id.currency_id", store=True)
    invoice_id = fields.Many2one("account.move", string="Factura", copy=False)
    invoice_state = fields.Selection(related="invoice_id.state", string="Estado factura")
    payment_state = fields.Selection(related="invoice_id.payment_state", string="Pago")
    state = fields.Selection(
        [
            ("pending", "Pendiente"),
            ("invoiced", "Facturada"),
            ("paid", "Pagada"),
            ("overdue", "Vencida"),
            ("cancelled", "Cancelada"),
        ],
        string="Estado",
        default="pending",
        tracking=True,
        required=True,
        index=True,
    )
    company_id = fields.Many2one(related="policy_id.company_id", store=True)
    branch_id = fields.Many2one(related="policy_id.branch_id", store=True, string="Sucursal")
    notified_upcoming = fields.Boolean(string="Aviso previo enviado", copy=False)
    notified_overdue = fields.Boolean(string="Aviso de mora enviado", copy=False)

    @api.depends("policy_id.name", "number")
    def _compute_name(self):
        for line in self:
            policy_name = line.policy_id.name or _("Póliza")
            line.name = _("%s — Cuota %s") % (policy_name, line.number)

    def action_create_invoice(self):
        invoices = self.env["account.move"]
        for line in self:
            if line.state == "cancelled":
                raise UserError(_("No se puede facturar una cuota cancelada."))
            if line.policy_id.invoice_mode == "global_ppd":
                invoices |= line.policy_id.cfdi_global_move_id or line.policy_id._create_cfdi_global_invoice()
                continue
            if line.invoice_id:
                raise UserError(_("La cuota %s ya tiene factura.") % line.number)
            invoices |= line._create_invoice()
        if len(invoices) == 1:
            return {
                "type": "ir.actions.act_window",
                "name": _("Factura"),
                "res_model": "account.move",
                "res_id": invoices.id,
                "view_mode": "form",
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("Facturas"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", invoices.ids)],
        }

    def _create_invoice(self):
        self.ensure_one()
        policy = self.policy_id
        product = policy.scheme_id.product_id or self.env.ref(
            "insurance_management.product_insurance_premium",
            raise_if_not_found=False,
        )
        tax_ids = policy.scheme_id.tax_id.ids
        line_vals = {
            "product_id": product.id if product else False,
            "name": _(
                "Prima póliza %(policy)s — cuota %(number)s (%(start)s a %(end)s)"
            ) % {
                "policy": policy.name,
                "number": self.number,
                "start": self.date_from,
                "end": self.date_to,
            },
            "quantity": 1,
            "price_unit": self.amount,
            "tax_ids": [(6, 0, tax_ids)],
        }
        income_account = False
        if product:
            income_account = (
                product.property_account_income_id
                or product.categ_id.property_account_income_categ_id
            )
        if not income_account:
            Account = self.env["account.account"]
            domain = [("account_type", "=", "income")]
            if "deprecated" in Account._fields:
                domain.append(("deprecated", "=", False))
            if "company_ids" in Account._fields:
                domain.append(("company_ids", "in", [policy.company_id.id]))
            elif "company_id" in Account._fields:
                domain.append(("company_id", "in", [False, policy.company_id.id]))
            income_account = Account.search(domain, limit=1)
        if income_account:
            line_vals["account_id"] = income_account.id
        from .cfdi_utils import apply_cfdi_invoice_values, apply_sat_product_code

        apply_sat_product_code(product)
        vals = {
            "move_type": "out_invoice",
            "partner_id": policy.partner_id.id,
            "invoice_date": fields.Date.context_today(self),
            "invoice_date_due": self.due_date,
            "invoice_origin": policy.name,
            "invoice_payment_term_id": policy.payment_term_id.id if policy.payment_term_id else False,
            "ref": self.name,
            "insurance_policy_id": policy.id,
            "insurance_installment_id": self.id,
            "invoice_line_ids": [(0, 0, line_vals)],
        }
        apply_cfdi_invoice_values(
            self.env,
            vals,
            usage=policy.cfdi_usage or "G03",
            payment_method="PUE" if policy.premium_type == "unique" else "PPD",
            payment_form=policy.cfdi_payment_form or "99",
        )
        invoice = self.env["account.move"].create(vals)
        invoice._fill_insurance_complement_from_policy(policy, installment=self)
        self.write({
            "invoice_id": invoice.id,
            "state": "invoiced",
        })
        policy.message_post(
            body=_("Factura %s creada para la cuota %s.") % (invoice.name, self.number)
        )
        return invoice

    def action_register_payment(self):
        self.ensure_one()
        if self.policy_id.invoice_mode == "global_ppd":
            invoice = self.policy_id.cfdi_global_move_id or self.policy_id._create_cfdi_global_invoice()
        else:
            if not self.invoice_id:
                self._create_invoice()
            invoice = self.invoice_id
        if invoice.state == "draft":
            invoice.action_post()
        if invoice.payment_state in ("paid", "in_payment"):
            raise UserError(_("Esta factura ya está pagada."))
        action = invoice.action_register_payment()
        ctx = dict(action.get("context") or {})
        ctx["default_amount"] = self.amount
        action["context"] = ctx
        return action

    def action_send_collection_notice(self):
        template = self.env.ref(
            "insurance_management.email_template_collection_notice",
            raise_if_not_found=False,
        )
        for line in self:
            if template and line.partner_id.email:
                template.send_mail(line.id, force_send=False)
            line.activity_schedule(
                "insurance_management.mail_activity_insurance_collection",
                user_id=line.policy_id.user_id.id or self.env.user.id,
                summary=_("Aviso de cobranza — %s") % line.name,
                note=_("Notificar al titular %s. Vence el %s por %s.") % (
                    line.partner_id.name,
                    line.due_date,
                    line.amount,
                ),
            )
            if line.state == "overdue":
                line.notified_overdue = True
            else:
                line.notified_upcoming = True
            line.policy_id.message_post(
                body=_("Se envió aviso de cobranza de la cuota %s (vence %s).") % (line.number, line.due_date)
            )
        return True

    def action_open_invoice(self):
        self.ensure_one()
        if not self.invoice_id:
            raise UserError(_("Esta cuota aún no tiene factura."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Factura"),
            "res_model": "account.move",
            "res_id": self.invoice_id.id,
            "view_mode": "form",
        }

    def _sync_payment_state(self):
        today = fields.Date.context_today(self)
        for line in self:
            if line.state == "cancelled":
                continue
            if line.invoice_id and line.invoice_id.payment_state in ("paid", "in_payment"):
                line.state = "paid"
            elif line.due_date and line.due_date < today and line.state != "paid":
                line.state = "overdue"
            elif line.invoice_id and line.state != "paid":
                line.state = "invoiced"

    @api.model
    def _cron_update_collection_state(self):
        lines = self.search([("state", "in", ["pending", "invoiced", "overdue"])])
        lines._sync_payment_state()
        overdue = lines.filtered(lambda l: l.state == "overdue")
        activity_type = self.env.ref(
            "insurance_management.mail_activity_insurance_collection",
            raise_if_not_found=False,
        )
        if not activity_type:
            return
        for line in overdue:
            existing = self.env["mail.activity"].search([
                ("res_model", "=", "insurance.installment"),
                ("res_id", "=", line.id),
                ("activity_type_id", "=", activity_type.id),
                ("date_deadline", ">=", fields.Date.context_today(self)),
            ], limit=1)
            if existing:
                continue
            line.activity_schedule(
                "insurance_management.mail_activity_insurance_collection",
                user_id=line.policy_id.user_id.id or self.env.user.id,
                summary=_("Cobranza vencida — %s") % line.name,
                note=_("La cuota %s de la póliza %s venció el %s.") % (
                    line.number,
                    line.policy_id.name,
                    line.due_date,
                ),
            )

    @api.model
    def _cron_invoice_due_installments(self):
        today = fields.Date.context_today(self)
        lines = self.search([
            ("state", "=", "pending"),
            ("invoice_id", "=", False),
            ("due_date", "<=", today),
            ("policy_id.state", "=", "confirmed"),
        ])
        for line in lines:
            try:
                line._create_invoice()
            except Exception:
                continue

    @api.model
    def _cron_collection_notices(self):
        today = fields.Date.context_today(self)
        upcoming = self.search([
            ("state", "in", ["pending", "invoiced"]),
            ("due_date", "!=", False),
            ("due_date", ">", today),
            ("due_date", "<=", today + relativedelta(days=7)),
            ("notified_upcoming", "=", False),
        ])
        overdue = self.search([
            ("state", "=", "overdue"),
            ("notified_overdue", "=", False),
        ])
        (upcoming | overdue).action_send_collection_notice()
