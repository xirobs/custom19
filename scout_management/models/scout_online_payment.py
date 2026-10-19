# -*- coding: utf-8 -*-
"""Cobro en línea (Mercado Pago u otra pasarela de Odoo) para cargos de miembros y ventas de la tienda.

Cada cargo pendiente (factura de cliente) tiene una liga de pago estándar de Odoo; aquí se arma la liga,
su QR y los mensajes para WhatsApp o correo. Al pagar, Odoo registra el pago en el diario de la pasarela
y el cargo queda pagado sin captura manual.
"""
import base64
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import UserError


def scout_qr_png(value, box_size=8):
    """PNG (bytes) de un código QR. Usa la librería qrcode que ya trae Odoo (sin depender de reportlab)."""
    import io
    import qrcode
    qr = qrcode.QRCode(box_size=box_size, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(value)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color='#0f3049', back_color='white').save(buf, format='PNG')
    return buf.getvalue()


def scout_qr_data_uri(value):
    return 'data:image/png;base64,' + base64.b64encode(scout_qr_png(value)).decode()


def scout_whatsapp_phone(raw):
    phone = ''.join(ch for ch in (raw or '') if ch.isdigit())
    if len(phone) == 10:
        phone = '52' + phone  # México
    return phone


class AccountMove(models.Model):
    _inherit = 'account.move'

    scout_can_pay_online = fields.Boolean(compute='_compute_scout_can_pay_online', string='Se puede pagar en línea')

    @api.depends('state', 'move_type', 'payment_state', 'amount_residual')
    def _compute_scout_can_pay_online(self):
        for move in self:
            move.scout_can_pay_online = bool(
                move.state == 'posted' and move.move_type == 'out_invoice'
                and move.payment_state in ('not_paid', 'partial') and move.amount_residual > 0)

    def _scout_payment_link(self):
        """Liga pública para pagar el saldo del cargo con la pasarela activa (Mercado Pago)."""
        self.ensure_one()
        if not self.scout_can_pay_online:
            raise UserError(_('El cargo %s no tiene saldo pendiente.', self.name))
        wizard = self.env['payment.link.wizard'].sudo().with_context(
            active_model='account.move', active_id=self.id).create({'amount': self.amount_residual})
        base = self.get_base_url().rstrip('/')
        return wizard.link.replace(base + '//', base + '/', 1)

    def _scout_payment_message(self, link=None):
        self.ensure_one()
        member = self.scout_member_id
        return _('Hola %(name)s, este es el enlace para pagar en línea %(concept)s%(member)s por %(amount)s '
                 '(tarjeta, OXXO o Mercado Pago): %(link)s\n%(company)s',
                 name=self.partner_id.name or '', concept=self.invoice_line_ids[:1].name or self.name,
                 member=_(' de %s', member.name) if member else '',
                 amount=f'${self.amount_residual:,.2f}', link=link or self._scout_payment_link(),
                 company=self.company_id.name)

    def action_scout_payment_link(self):
        self.ensure_one()
        wizard = self.env['scout.payment.link.wizard'].create({'move_id': self.id})
        return wizard._open()


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def action_scout_payment_link(self):
        self.ensure_one()
        return self.move_id.action_scout_payment_link()


class ScoutPaymentLinkWizard(models.TransientModel):
    _name = 'scout.payment.link.wizard'
    _description = 'Cobrar en línea (liga y QR)'

    move_id = fields.Many2one('account.move', string='Cargo', required=True, readonly=True)
    partner_id = fields.Many2one(related='move_id.partner_id', string='Quién paga')
    member_id = fields.Many2one(related='move_id.scout_member_id', string='Miembro')
    amount = fields.Monetary(related='move_id.amount_residual', string='Saldo a pagar')
    currency_id = fields.Many2one(related='move_id.currency_id')
    phone = fields.Char(string='WhatsApp', compute='_compute_contact', store=True, readonly=False)
    email = fields.Char(string='Correo', compute='_compute_contact', store=True, readonly=False)
    link = fields.Char(string='Liga de pago', readonly=True)
    qr_code = fields.Binary(string='QR para pagar', readonly=True)
    provider_ready = fields.Boolean(compute='_compute_provider_ready')

    @api.depends('move_id')
    def _compute_contact(self):
        for wiz in self:
            wiz.phone = wiz.move_id.partner_id.phone
            wiz.email = wiz.move_id.partner_id.email

    def _compute_provider_ready(self):
        ready = bool(self.env['payment.provider'].sudo().search_count(
            [('state', 'in', ('enabled', 'test')), ('company_id', '=', self.env.company.id)]))
        self.provider_ready = ready

    @api.model_create_multi
    def create(self, vals_list):
        wizards = super().create(vals_list)
        for wiz in wizards:
            link = wiz.move_id._scout_payment_link()
            wiz.write({'link': link, 'qr_code': base64.b64encode(scout_qr_png(link))})
        return wizards

    def _open(self):
        return {
            'type': 'ir.actions.act_window', 'name': _('Cobrar en línea'), 'res_model': self._name,
            'res_id': self.id, 'view_mode': 'form', 'target': 'new',
        }

    def action_whatsapp(self):
        self.ensure_one()
        phone = scout_whatsapp_phone(self.phone)
        if not phone:
            raise UserError(_('Escribe el número de WhatsApp.'))
        self.move_id.message_post(body=_('Liga de pago enviada por WhatsApp a %s.', phone))
        text = self.move_id._scout_payment_message(self.link)
        return {'type': 'ir.actions.act_url', 'url': f'https://wa.me/{phone}?text={quote(text)}', 'target': 'new'}

    def action_email(self):
        self.ensure_one()
        if not self.email:
            raise UserError(_('Escribe el correo.'))
        body = self.move_id._scout_payment_message(self.link).replace('\n', '<br/>')
        self.env['mail.mail'].sudo().create({
            'subject': _('Pago en línea - %s', self.move_id.company_id.name),
            'email_to': self.email,
            'email_from': self.move_id.company_id.email_formatted or self.env.user.email_formatted,
            'body_html': f'<p>{body}</p><p><a href="{self.link}" style="background:#f36f45;color:#fff;'
                         f'padding:10px 18px;border-radius:6px;text-decoration:none;font-weight:bold;">'
                         f'{_("Pagar en línea")}</a></p>',
            'model': 'account.move', 'res_id': self.move_id.id,
        }).send()
        self.move_id.message_post(body=_('Liga de pago enviada por correo a %s.', self.email))
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'type': 'success', 'message': _('Liga enviada a %s', self.email),
                           'next': {'type': 'ir.actions.act_window_close'}}}


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    def write(self, vals):
        res = super().write(vals)
        if 'journal_id' in vals or 'state' in vals:
            # Tesorería simple: el cobro en línea suma directo al saldo del diario de la pasarela.
            self.journal_id._scout_setup_simple_treasury()
        return res


class ResCompanyOnlinePayment(models.Model):
    _inherit = 'res.company'

    @api.model
    def _scout_setup_online_payments(self):
        """Crea el diario "Mercado Pago" (banco) y lo asigna a la pasarela para ver ese saldo en tesorería."""
        provider = self.env.ref('payment.payment_provider_mercado_pago', raise_if_not_found=False)
        for company in self.env['res.company'].search([]):
            Journal = self.env['account.journal'].with_company(company)
            if not company.chart_template and not Journal.search_count([('company_id', '=', company.id)]):
                continue  # Sin plan contable todavía.
            journal = Journal.search([('code', '=', 'MPAGO'), ('company_id', '=', company.id)], limit=1)
            if not journal:
                try:
                    journal = Journal.create({'name': 'Mercado Pago', 'type': 'bank', 'code': 'MPAGO',
                                              'company_id': company.id})
                except Exception:  # noqa: BLE001 - sin cuentas configuradas, se hace a mano
                    continue
            if provider and provider.company_id == company and not provider.journal_id:
                provider.journal_id = journal
            journal._scout_setup_simple_treasury()
