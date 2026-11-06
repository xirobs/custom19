# -*- coding: utf-8 -*-
"""Solicitud de pago al grupo: se envían a los representantes los datos para pagar
(liga de Mercado Pago o transferencia bancaria) y el grupo paga o sube su comprobante
desde una página con liga segura, sin necesidad de iniciar sesión."""
import logging
import re
import secrets
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PAYMENT_REQUEST_RE = re.compile(r'^RSP-G\d+-(\d+)')


class RssPaymentRequest(models.Model):
    _inherit = 'rss.payment'

    access_token = fields.Char('Código de la liga de pago', copy=False, readonly=True, index=True)
    request_reference = fields.Char('Referencia para pagar', compute='_compute_request_reference', store=True,
                                    help='Concepto que el grupo escribe en su transferencia.')
    request_sent_date = fields.Datetime('Datos de pago enviados el', readonly=True, copy=False, tracking=True)
    amount_mp_total = fields.Monetary('Total con Mercado Pago', compute='_compute_mp_total',
                                      help='Importe a cobrar en Mercado Pago (incluye su comisión si la póliza '
                                           'la traslada al grupo).')
    proof_submitted = fields.Boolean('Comprobante enviado por el grupo', readonly=True, copy=False, tracking=True)
    payment_url = fields.Char('Liga de pago', compute='_compute_payment_url')
    representative_ids = fields.Many2many('res.partner', string='Representantes del grupo',
                                          compute='_compute_representatives')

    @api.depends('group_id.number', 'group_id')
    def _compute_request_reference(self):
        for pay in self:
            pay.request_reference = 'RSP-G%s-%s' % (pay.group_id.number or pay.group_id.id or 0, pay.id or 0) \
                if pay.id else False

    @api.depends('amount_expected', 'policy_id')
    def _compute_mp_total(self):
        for pay in self:
            net = pay.amount_expected
            if pay.policy_id and net:
                pay.amount_mp_total = pay.policy_id._mercado_pago_total(net)[0]
            else:
                pay.amount_mp_total = net

    def _compute_payment_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        for pay in self:
            pay.payment_url = '%s/seguro/pago/%s/%s' % (base, pay.id, pay.access_token) \
                if pay.id and pay.access_token else False

    @api.depends('group_id')
    def _compute_representatives(self):
        for pay in self:
            members = pay.group_id.sudo().portal_member_ids.filtered('active') \
                if 'portal_member_ids' in pay.group_id._fields else self.env['rss.group.member']
            pay.representative_ids = members.mapped('partner_id').filtered('email')

    # ------------------------------------------------------------------
    def _ensure_access_token(self):
        for pay in self.sudo().filtered(lambda p: not p.access_token):
            pay.access_token = secrets.token_urlsafe(18)

    def _check_requestable(self):
        for pay in self:
            if pay.state != 'draft':
                raise UserError(_('Solo se envían datos de pago de un pago en borrador.'))
            if not pay.member_ids:
                pay.member_ids = pay._members_to_pay()
            if not pay.member_ids:
                raise UserError(_('El grupo %s no tiene asegurados para cobrar.', pay.group_id.name))
            if pay.amount_expected <= 0:
                raise UserError(_('Captura la cuota por miembro para calcular el importe a pagar.'))

    def _mercadopago_available(self):
        self.ensure_one()
        providers = self.env['payment.provider'].sudo()._get_compatible_providers(
            self.company_id.id, (self.representative_ids[:1] or self.env.user.partner_id).id,
            self.amount_mp_total or 1.0, currency_id=self.currency_id.id, rss_mercado_pago_only=True)
        return bool(providers)

    def _request_text(self):
        """Mensaje en texto plano (WhatsApp)."""
        self.ensure_one()
        pol = self.policy_id
        money = lambda v: '$ {:,.2f}'.format(v or 0)  # noqa: E731
        lines = [
            _('*Red Seguro Scout · Pago del seguro*'),
            _('Grupo: %s', self.group_id.name),
            _('Asegurados: %s', self.member_count),
            _('Importe: %s', money(self.amount_expected)),
            _('Referencia: %s', self.request_reference),
            '',
        ]
        if self._mercadopago_available():
            lines += [_('*Pagar con Mercado Pago* (%s):', money(self.amount_mp_total)), self.payment_url, '']
        if pol and (pol.bank_clabe or pol.bank_account or pol.bank_card):
            lines.append(_('*Transferencia bancaria* (%s):', money(self.amount_expected)))
            for label, value in ((_('Beneficiario'), pol.bank_beneficiary), (_('Banco'), pol.bank_name),
                                 (_('CLABE'), pol.bank_clabe), (_('Cuenta'), pol.bank_account),
                                 (_('Tarjeta'), pol.bank_card)):
                if value:
                    lines.append('%s: %s' % (label, value))
            lines.append(_('Concepto: %s', self.request_reference))
            if pol.bank_notes:
                lines.append(pol.bank_notes)
            lines += ['', _('Después de transferir, sube tu comprobante aquí:'), self.payment_url]
        return '\n'.join(lines)

    def action_send_payment_request(self):
        """Abre el correo con los datos de pago dirigido a los representantes del grupo."""
        self.ensure_one()
        self._check_requestable()
        self._ensure_access_token()
        template = self.env.ref('red_seguro_scout.mail_template_rss_payment_request', raise_if_not_found=False)
        subject = body = False
        if template:
            subject = template._render_field('subject', self.ids)[self.id]
            body = template._render_field('body_html', self.ids)[self.id]
        if not self.representative_ids:
            self.message_post(body=_('El grupo no tiene representantes con correo en «Representantes del '
                                     'portal»; agrega el destinatario a mano o envía por WhatsApp.'))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_model': self._name,
                'default_res_ids': self.ids,
                'default_composition_mode': 'comment',
                'default_subject': subject,
                'default_body': body,
                'default_partner_ids': self.representative_ids.ids,
                'default_email_layout_xmlid': 'mail.mail_notification_light',
                'mark_rss_payment_request_sent': True,
            },
        }

    def action_send_payment_whatsapp(self):
        """WhatsApp con el mensaje ya escrito; se elige el chat del grupo o del representante."""
        self.ensure_one()
        self._check_requestable()
        self._ensure_access_token()
        phone = re.sub(r'\D', '', self.group_id.leader_phone or '')
        if len(phone) == 10:
            phone = '52' + phone
        if not self.request_sent_date:
            self.request_sent_date = fields.Datetime.now()
        self.message_post(body=_('Datos de pago compartidos por WhatsApp.'))
        url = 'https://wa.me/%s?text=%s' % (phone if len(phone) >= 12 else '', quote(self._request_text()))
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}

    # ------------------------------------------------------------------ desde la liga de pago
    def _group_submit_transfer(self, reference, tracking_key, files):
        """El grupo sube su comprobante de transferencia; queda para que la Red lo confirme."""
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_('Este pago ya no está pendiente.'))
        if not files:
            raise UserError(_('Adjunta la foto o el PDF del comprobante.'))
        attachments = self.env['ir.attachment'].sudo().create([{
            'name': name, 'datas': data, 'mimetype': mimetype,
            'res_model': self._name, 'res_id': self.id,
        } for name, data, mimetype in files])
        vals = {'attachment_ids': [(4, a.id) for a in attachments], 'proof_submitted': True,
                'payment_method': 'transfer'}
        if reference:
            vals['reference'] = reference.strip()[:64]
        if tracking_key:
            vals['tracking_key'] = tracking_key.strip()[:64]
        if not self.amount:
            vals['amount'] = self.amount_expected
        self.sudo().write(vals)
        self.sudo().message_post(body=_('El grupo subió su comprobante de transferencia. Verifica el depósito '
                                        'y presiona «Registrar pago y emitir recibo».'),
                                 attachment_ids=attachments.ids)
        user = self.received_by or self.create_uid
        self.sudo().activity_schedule('mail.mail_activity_data_todo', user_id=user.id,
                                      summary=_('Verificar transferencia del grupo'))

    def _mercadopago_paid(self, tx):
        """Pago con Mercado Pago confirmado: se registra y se emite el recibo."""
        self.ensure_one()
        if self.state != 'draft':
            return
        net = self.amount_expected or tx.amount
        self.sudo().write({
            'payment_method': 'mercadopago',
            'amount': net,
            'mp_commission': max(tx.amount - net, 0.0),
            'reference': (tx.provider_reference or tx.reference or '')[:64],
            'date': fields.Date.context_today(self),
            'payer_name': tx.partner_id.name or self.payer_name,
        })
        self.sudo().action_confirm()
        self.sudo().message_post(
            body=_('Pago recibido por Mercado Pago (%s). Recibo emitido; descárgalo en %s',
                   tx.reference, '%s/recibo' % self.payment_url),
            partner_ids=self.representative_ids.ids, subtype_xmlid='mail.mt_comment')


class MailComposeRssPayment(models.TransientModel):
    _inherit = 'mail.compose.message'

    def _action_send_mail(self, auto_commit=False):
        res = super()._action_send_mail(auto_commit=auto_commit)
        if self.env.context.get('mark_rss_payment_request_sent') and self.model == 'rss.payment':
            payments = self.env['rss.payment'].browse(self._evaluate_res_ids())
            payments.filtered(lambda p: not p.request_sent_date).write(
                {'request_sent_date': fields.Datetime.now()})
        return res


class PaymentTransactionRssPayment(models.Model):
    _inherit = 'payment.transaction'

    def _post_process(self):
        res = super()._post_process()
        self._rss_close_payment_requests()
        return res

    def _rss_close_payment_requests(self):
        for tx in self.filtered(lambda t: t.state == 'done' and t.reference):
            match = PAYMENT_REQUEST_RE.match(tx.reference)
            if not match:
                continue
            pay = self.env['rss.payment'].sudo().browse(int(match.group(1))).exists()
            if not pay:
                continue
            if tx.currency_id.compare_amounts(tx.amount, pay.amount_expected) < 0:
                pay.message_post(body=_('Pago de Mercado Pago %(ref)s por un importe menor al esperado (%(amt)s); '
                                        'revísalo manualmente.', ref=tx.reference, amt=tx.amount))
                continue
            try:
                pay._mercadopago_paid(tx)
            except Exception:  # noqa: BLE001 - el cobro ya se hizo; se revisa a mano
                _logger.exception('Red Seguro Scout: no se pudo cerrar el pago %s', pay.id)
                pay.message_post(body=_('El pago %s se recibió pero no se pudo emitir el recibo '
                                        'automáticamente; revísalo.', tx.reference))


class RssPaymentRequestAuto(models.Model):
    _inherit = 'rss.payment'

    def _send_payment_request_auto(self):
        """Envía sin intervención los datos de pago a los representantes (cargas de la plataforma/asesor)."""
        template = self.env.ref('red_seguro_scout.mail_template_rss_payment_request', raise_if_not_found=False)
        for pay in self:
            pay._check_requestable()
            pay._ensure_access_token()
            if not pay.representative_ids:
                pay.message_post(body=_('No se enviaron los datos de pago: el grupo no tiene representantes con '
                                        'correo. Usa «Enviar por WhatsApp» o agrega sus correos.'))
                continue
            if not template:
                continue
            pay.message_post(
                subject=template._render_field('subject', pay.ids)[pay.id],
                body=template._render_field('body_html', pay.ids)[pay.id],
                partner_ids=pay.representative_ids.ids, subtype_xmlid='mail.mt_comment',
                email_layout_xmlid='mail.mail_notification_light')
            pay.request_sent_date = fields.Datetime.now()


class RssImportBatchPaymentInfo(models.Model):
    _inherit = 'rss.import.batch'

    rss_request_payment_id = fields.Many2one('rss.payment', string='Solicitud de pago enviada', readonly=True,
                                             copy=False)

    def _rss_representatives(self):
        self.ensure_one()
        members = self.group_id.sudo().portal_member_ids.filtered('active') \
            if 'portal_member_ids' in self.group_id._fields else self.env['rss.group.member']
        return members.mapped('partner_id').filtered('email')

    def _rss_mp_available(self):
        self.ensure_one()
        currency = self.currency_id or self.env.company.currency_id
        partner = self._rss_representatives()[:1] or self.env.user.partner_id
        providers = self.env['payment.provider'].sudo()._get_compatible_providers(
            (self.company_id or self.env.company).id, partner.id, self.amount_mp_total or 1.0,
            currency_id=currency.id, rss_mercado_pago_only=True)
        return bool(providers)

    def _rss_portal_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        return '%s/mi-grupo/importar/%s' % (base, self.id)

    def _rss_notify_payment_info(self):
        """Lista del grupo lista para pagar: les llega el importe, la liga de Mercado Pago y la cuenta."""
        template = self.env.ref('red_seguro_scout.mail_template_rss_batch_payment', raise_if_not_found=False)
        for batch in self.filtered(lambda b: b.state == 'payment'):
            partners = batch._rss_representatives()
            if not template or not partners:
                continue
            try:
                batch.sudo().message_post(
                    subject=template._render_field('subject', batch.ids)[batch.id],
                    body=template._render_field('body_html', batch.ids)[batch.id],
                    partner_ids=partners.ids, subtype_xmlid='mail.mt_comment',
                    email_layout_xmlid='mail.mail_notification_light')
            except Exception:  # noqa: BLE001 - el aviso no debe impedir el pago
                _logger.exception('Red Seguro Scout: no se pudo avisar el pago de la carga %s', batch.id)

    def action_request_payment(self):
        res = super().action_request_payment()
        self._rss_notify_payment_info()
        return res

    def action_save_draft(self):
        """Cargas de la plataforma o del asesor: los preinscritos quedan en un pago pendiente y al grupo
        le llega la liga para pagarlo."""
        pending = self.filtered(lambda b: b.office_load and b.state == 'review')
        res = super().action_save_draft()
        if self.env.context.get('rss_no_payment_request'):
            return res
        for batch in pending.filtered(lambda b: b.state == 'done'):
            insured = batch.line_ids.mapped('insured_id').filtered(lambda i: i.state == 'draft')
            if not insured or not batch.group_id.policy_id:
                continue
            try:
                pay = self.env['rss.payment'].sudo().create({
                    'group_id': batch.group_id.id, 'amount': 0,
                    'member_ids': [(6, 0, insured.ids)],
                    'notes': _('Generado al cargar la lista %s.', batch.filename or batch.display_name),
                })
                if pay.amount_expected > 0:
                    pay._send_payment_request_auto()
                batch.rss_request_payment_id = pay
                batch.message_post(body=_('Pago pendiente %(ref)s por %(amt)s creado%(sent)s.',
                                          ref=pay.request_reference, amt='$ {:,.2f}'.format(pay.amount_expected),
                                          sent=_(' y enviado a los representantes del grupo')
                                          if pay.request_sent_date else ''))
            except Exception:  # noqa: BLE001 - los preinscritos ya se guardaron
                _logger.exception('Red Seguro Scout: no se pudo crear el pago de la carga %s', batch.id)
        return res
