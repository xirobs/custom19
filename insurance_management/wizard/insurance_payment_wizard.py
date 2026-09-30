# -*- coding: utf-8 -*-
"""Realizar pago desde la cuenta del cliente, la póliza o la cuota."""

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class InsurancePaymentWizard(models.TransientModel):
    _name = "insurance.payment.wizard"
    _description = "Registrar pago de primas"

    partner_id = fields.Many2one("res.partner", string="Cliente", required=True)
    policy_id = fields.Many2one(
        "insurance.policy",
        string="Póliza",
        domain="[('partner_id', '=', partner_id), ('state', 'in', ['draft', 'confirmed', 'expired'])]",
    )
    installment_ids = fields.Many2many(
        "insurance.installment",
        string="Cuotas a pagar",
        domain="[('partner_id', '=', partner_id), ('state', 'in', ['pending', 'invoiced', 'overdue'])]",
    )
    currency_id = fields.Many2one("res.currency", compute="_compute_totals", string="Moneda")
    amount_due = fields.Monetary(string="Saldo seleccionado", compute="_compute_totals", currency_field="currency_id")
    amount = fields.Monetary(string="Importe pagado", currency_field="currency_id")
    payment_date = fields.Date(string="Fecha de pago", required=True, default=fields.Date.context_today)
    payment_method = fields.Selection(
        [
            ("link", "Liga de pago"),
            ("bank", "Banco / ventanilla"),
            ("transfer", "Transferencia"),
            ("portal", "Portal de la aseguradora"),
            ("domiciled", "Cargo domiciliado"),
            ("cash", "Efectivo"),
        ],
        string="Forma de pago",
        default="transfer",
        required=True,
    )
    reference = fields.Char(string="Referencia / folio")
    send_receipt = fields.Boolean(string="Enviar comprobante por correo", default=True)

    @api.onchange("partner_id", "policy_id")
    def _onchange_scope(self):
        if not self.partner_id:
            return
        domain = [("partner_id", "=", self.partner_id.id), ("state", "in", ["pending", "invoiced", "overdue"])]
        if self.policy_id:
            domain.append(("policy_id", "=", self.policy_id.id))
        lines = self.env["insurance.installment"].search(domain, order="due_date, number")
        if self.installment_ids.filtered(lambda l: self.policy_id and l.policy_id != self.policy_id) or not self.installment_ids:
            # por defecto: la cuota más antigua pendiente
            self.installment_ids = lines[:1]
        self.amount = sum(self.installment_ids.mapped("balance"))

    @api.onchange("installment_ids")
    def _onchange_installments(self):
        self.amount = sum(self.installment_ids.mapped("balance"))

    @api.depends("installment_ids")
    def _compute_totals(self):
        for wizard in self:
            lines = wizard.installment_ids
            wizard.currency_id = lines[:1].currency_id or wizard.policy_id.currency_id or self.env.company.currency_id
            wizard.amount_due = sum(lines.mapped("balance"))

    def action_apply(self):
        self.ensure_one()
        lines = self.installment_ids.sorted(lambda l: (l.due_date or fields.Date.today(), l.number))
        if not lines:
            raise UserError(_("Seleccione las cuotas que se pagan."))
        if len(lines.mapped("currency_id")) > 1:
            raise UserError(_("Registre por separado los pagos de cuotas en monedas distintas."))
        if self.amount <= 0:
            raise UserError(_("Indique el importe pagado."))
        remaining = self.amount
        paid_lines = self.env["insurance.installment"]
        applied_by_line = {}
        for line in lines:
            if remaining <= 0:
                break
            applied = min(remaining, line.balance)
            if applied <= 0:
                continue
            line.write({
                "paid_amount": (line.paid_amount or 0.0) + applied,
                "payment_date": self.payment_date,
                "payment_reference": " / ".join(filter(None, [line.payment_reference, self.reference])) or False,
            })
            remaining -= applied
            paid_lines |= line
            applied_by_line[line.id] = applied
        methods = dict(self._fields["payment_method"]._description_selection(self.env))
        for policy in paid_lines.mapped("policy_id"):
            policy_lines = paid_lines.filtered(lambda l: l.policy_id == policy)
            policy.message_post(body=_(
                "Pago registrado: %(amount)s %(currency)s por %(method)s (ref. %(ref)s) — cuotas %(numbers)s."
            ) % {
                "amount": "{:,.2f}".format(sum(applied_by_line[l.id] for l in policy_lines)),
                "currency": self.currency_id.name or "",
                "method": methods.get(self.payment_method),
                "ref": self.reference or "-",
                "numbers": ", ".join(str(n) for n in policy_lines.mapped("number")),
            })
        if self.send_receipt:
            template = self.env.ref("insurance_management.mail_template_payment_receipt", raise_if_not_found=False)
            if template:
                for line in paid_lines.filtered(lambda l: l.partner_id.email):
                    line.message_post_with_source(template, message_type="comment", subtype_xmlid="mail.mt_comment")
        message = _("Pago aplicado a %s cuota(s).") % len(paid_lines)
        if remaining > 0.01:
            message += " " + _("Sobrante sin aplicar: %s.") % "{:,.2f}".format(remaining)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Pago registrado"),
                "message": message,
                "type": "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
