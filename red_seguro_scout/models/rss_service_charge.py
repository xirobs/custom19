# -*- coding: utf-8 -*-
"""Cobro por servicios de Red Seguro Scout a cada grupo (cuota fija, $500 por omisión).

Va aparte de los pagos del seguro: no lleva asegurados ni se remite a la aseguradora.
Al grupo le llega una liga para pagar con Mercado Pago o subir su comprobante de transferencia;
con Mercado Pago queda pagado y con recibo al momento."""
import logging
import re
import secrets
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

SERVICE_FEE_PARAM = 'red_seguro_scout.service_fee'
DEFAULT_SERVICE_FEE = 500.0
SERVICE_REFERENCE_RE = re.compile(r'^RSV-G\d+-(\d+)')


class RssServiceCharge(models.Model):
    _name = 'rss.service.charge'
    _description = 'Cobro por servicios de Red Seguro Scout'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'rss.branding.mixin']
    _order = 'date desc, id desc'

    name = fields.Char('Folio', readonly=True, copy=False, default=lambda self: _('Nuevo'))
    group_id = fields.Many2one('rss.scout.group', string='Grupo', required=True, index=True, tracking=True)
    company_id = fields.Many2one('res.company', string='Organización', required=True,
                                 default=lambda self: self.env.company)
    currency_id = fields.Many2one('res.currency', compute='_compute_currency', string='Moneda')
    concept = fields.Char('Concepto', required=True, tracking=True,
                          default=lambda self: _('Servicios de Red Seguro Scout'))
    amount = fields.Monetary('Importe', required=True, tracking=True,
                             default=lambda self: self._default_amount())
    date = fields.Date('Fecha', required=True, default=fields.Date.context_today, tracking=True)
    state = fields.Selection([
        ('draft', 'Por enviar'),
        ('sent', 'Enviado al grupo'),
        ('review', 'Comprobante por verificar'),
        ('paid', 'Pagado'),
        ('cancel', 'Cancelado'),
    ], string='Estatus', default='draft', required=True, tracking=True, index=True, copy=False)
    payment_method = fields.Selection([
        ('mercadopago', 'Mercado Pago'),
        ('transfer', 'Transferencia'),
        ('deposit', 'Depósito bancario'),
        ('cash', 'Efectivo'),
        ('other', 'Otro'),
    ], string='Forma de pago', tracking=True, copy=False)
    paid_date = fields.Date('Pagado el', readonly=True, copy=False, tracking=True)
    payment_reference = fields.Char('Folio de la operación', copy=False, tracking=True)
    tracking_key = fields.Char('Clave de rastreo SPEI', copy=False)
    mp_commission = fields.Monetary('Comisión Mercado Pago', readonly=True, copy=False)
    attachment_ids = fields.Many2many('ir.attachment', 'rss_service_charge_attachment_rel', 'charge_id',
                                      'attachment_id', string='Comprobante(s)', copy=False)
    notes = fields.Text('Notas')
    access_token = fields.Char('Código de la liga', copy=False, readonly=True, index=True)
    sent_date = fields.Datetime('Enviado el', readonly=True, copy=False)
    reference = fields.Char('Referencia para pagar', compute='_compute_reference', store=True)
    payment_url = fields.Char('Liga de pago', compute='_compute_payment_url')
    amount_mp_total = fields.Monetary('Total con Mercado Pago', compute='_compute_mp_total')
    representative_ids = fields.Many2many('res.partner', string='Representantes',
                                          compute='_compute_representatives')

    @api.model
    def _default_amount(self):
        value = self.env['ir.config_parameter'].sudo().get_param(SERVICE_FEE_PARAM)
        try:
            return float(value) if value else DEFAULT_SERVICE_FEE
        except ValueError:
            return DEFAULT_SERVICE_FEE

    @api.depends('company_id')
    def _compute_currency(self):
        fallback = self.env.ref('base.MXN', raise_if_not_found=False) or self.env.company.currency_id
        for rec in self:
            rec.currency_id = rec.company_id.currency_id or fallback

    @api.depends('group_id.number')
    def _compute_reference(self):
        for rec in self:
            rec.reference = 'RSV-G%s-%s' % (rec.group_id.number or rec.group_id.id or 0, rec.id) if rec.id else False

    def _compute_payment_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        for rec in self:
            rec.payment_url = '%s/seguro/servicio/%s/%s' % (base, rec.id, rec.access_token) \
                if rec.id and rec.access_token else False

    def _billing_policy(self):
        """Póliza de donde salen la cuenta bancaria y la regla de comisión de Mercado Pago."""
        self.ensure_one()
        policy = self.group_id.sudo().policy_id
        if policy and (policy.bank_clabe or policy.bank_account or policy.bank_card):
            return policy
        return self.env['rss.policy'].sudo().search(
            ['|', '|', ('bank_clabe', '!=', False), ('bank_account', '!=', False), ('bank_card', '!=', False)],
            limit=1) or policy

    @api.depends('amount', 'group_id')
    def _compute_mp_total(self):
        for rec in self:
            policy = rec._billing_policy() if rec.group_id else False
            rec.amount_mp_total = policy._mercado_pago_total(rec.amount)[0] if policy and rec.amount else rec.amount

    @api.depends('group_id')
    def _compute_representatives(self):
        for rec in self:
            members = rec.group_id.sudo().portal_member_ids.filtered('active') \
                if 'portal_member_ids' in rec.group_id._fields else self.env['rss.group.member']
            rec.representative_ids = members.mapped('partner_id').filtered('email')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('rss.service.charge') or _('Nuevo')
        records = super().create(vals_list)
        records._ensure_access_token()
        return records

    def unlink(self):
        if any(rec.state == 'paid' for rec in self):
            raise UserError(_('No se puede eliminar un cobro pagado; cancélalo.'))
        return super().unlink()

    def _ensure_access_token(self):
        for rec in self.sudo().filtered(lambda r: not r.access_token):
            rec.access_token = secrets.token_urlsafe(18)

    def _mercadopago_available(self):
        self.ensure_one()
        providers = self.env['payment.provider'].sudo()._get_compatible_providers(
            self.company_id.id, (self.representative_ids[:1] or self.env.user.partner_id).id,
            self.amount_mp_total or 1.0, currency_id=self.currency_id.id, rss_mercado_pago_only=True)
        return bool(providers)

    def _rss_brand(self):
        self.ensure_one()
        return self._rss_brand_from(company=self.company_id)

    def _amount_in_words(self):
        self.ensure_one()
        try:
            from num2words import num2words
        except ImportError:
            return ''
        integer = int(self.amount)
        cents = int(round((self.amount - integer) * 100))
        return '(%s PESOS %02d/100 M.N.)' % (num2words(integer, lang='es').upper(), cents)

    # ------------------------------------------------------------------ envío al grupo
    def _check_open(self):
        for rec in self:
            if rec.state in ('paid', 'cancel'):
                raise UserError(_('El cobro %s ya está %s.', rec.name,
                                  dict(rec._fields['state'].selection)[rec.state].lower()))
            if rec.amount <= 0:
                raise UserError(_('Captura el importe del cobro %s.', rec.name))

    def _text_message(self):
        """Mensaje para WhatsApp."""
        self.ensure_one()
        money = '$ {:,.2f}'.format
        policy = self._billing_policy()
        lines = [_('*Red Seguro Scout · %s*', self.concept), _('Grupo: %s', self.group_id.name),
                 _('Importe: %s', money(self.amount)), _('Referencia: %s', self.reference), '']
        if self._mercadopago_available():
            lines += [_('*Pagar con Mercado Pago* (%s):', money(self.amount_mp_total)), self.payment_url, '']
        if policy and (policy.bank_clabe or policy.bank_account or policy.bank_card):
            lines.append(_('*Transferencia bancaria* (%s):', money(self.amount)))
            for label, value in ((_('Beneficiario'), policy.bank_beneficiary), (_('Banco'), policy.bank_name),
                                 (_('CLABE'), policy.bank_clabe), (_('Cuenta'), policy.bank_account),
                                 (_('Tarjeta'), policy.bank_card)):
                if value:
                    lines.append('%s: %s' % (label, value))
            lines += [_('Concepto: %s', self.reference), '',
                      _('Después de transferir, sube tu comprobante aquí:'), self.payment_url]
        return '\n'.join(lines)

    def _render_mail(self):
        self.ensure_one()
        template = self.env.ref('red_seguro_scout.mail_template_rss_service_charge')
        return (template._render_field('subject', self.ids)[self.id],
                template._render_field('body_html', self.ids)[self.id])

    def action_send(self):
        """Abre el correo con la liga de pago, dirigido a los representantes del grupo."""
        self.ensure_one()
        self._check_open()
        self._ensure_access_token()
        subject, body = self._render_mail()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'mail.compose.message', 'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_model': self._name, 'default_res_ids': self.ids,
                'default_composition_mode': 'comment', 'default_subject': subject, 'default_body': body,
                'default_partner_ids': self.representative_ids.ids,
                'default_email_layout_xmlid': 'red_seguro_scout.mail_notification_rss',
                'mark_rss_service_sent': True,
            },
        }

    def _send_auto(self):
        """Envío directo (cobro a varios grupos a la vez)."""
        sent = self.browse()
        for rec in self:
            rec._check_open()
            rec._ensure_access_token()
            if not rec.representative_ids:
                rec.message_post(body=_('No se envió: el grupo no tiene representantes con correo. '
                                        'Usa «Enviar por WhatsApp».'))
                continue
            subject, body = rec._render_mail()
            rec.message_post(subject=subject, body=body, partner_ids=rec.representative_ids.ids,
                             subtype_xmlid='mail.mt_comment', email_layout_xmlid='red_seguro_scout.mail_notification_rss')
            rec._mark_sent()
            sent |= rec
        return sent

    def _mark_sent(self):
        for rec in self:
            vals = {'sent_date': rec.sent_date or fields.Datetime.now()}
            if rec.state == 'draft':
                vals['state'] = 'sent'
            rec.write(vals)

    def action_send_whatsapp(self):
        self.ensure_one()
        self._check_open()
        self._ensure_access_token()
        phone = re.sub(r'\D', '', self.group_id.leader_phone or '')
        if len(phone) == 10:
            phone = '52' + phone
        self._mark_sent()
        self.message_post(body=_('Liga de pago compartida por WhatsApp.'))
        url = 'https://wa.me/%s?text=%s' % (phone if len(phone) >= 12 else '', quote(self._text_message()))
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}

    # ------------------------------------------------------------------ pago
    def action_register_payment(self):
        """Registro manual (transferencia verificada, efectivo, depósito)."""
        for rec in self:
            if rec.state in ('paid', 'cancel'):
                raise UserError(_('El cobro %s ya está cerrado.', rec.name))
            rec.write({'state': 'paid', 'paid_date': rec.paid_date or fields.Date.context_today(rec),
                       'payment_method': rec.payment_method or 'transfer'})
            rec.activity_ids.action_feedback(feedback=_('Pago registrado.'))
            rec.message_post(body=_('Pago registrado. Recibo: %s', '%s/recibo' % rec.payment_url),
                             partner_ids=rec.representative_ids.ids, subtype_xmlid='mail.mt_comment')
        return True

    def action_cancel(self):
        self.filtered(lambda r: r.state != 'paid').write({'state': 'cancel'})
        return True

    def action_reset(self):
        self.filtered(lambda r: r.state == 'cancel').write({'state': 'draft'})
        return True

    def _group_submit_transfer(self, reference, tracking_key, files):
        self.ensure_one()
        if self.state in ('paid', 'cancel'):
            raise UserError(_('Este cobro ya no está pendiente.'))
        if not files:
            raise UserError(_('Adjunta la foto o el PDF del comprobante.'))
        attachments = self.env['ir.attachment'].sudo().create([{
            'name': name, 'datas': data, 'mimetype': mimetype, 'res_model': self._name, 'res_id': self.id,
        } for name, data, mimetype in files])
        vals = {'attachment_ids': [(4, a.id) for a in attachments], 'state': 'review', 'payment_method': 'transfer'}
        if reference:
            vals['payment_reference'] = reference.strip()[:64]
        if tracking_key:
            vals['tracking_key'] = tracking_key.strip()[:64]
        self.sudo().write(vals)
        self.sudo().message_post(body=_('El grupo subió su comprobante. Verifica el depósito y presiona '
                                        '«Registrar pago».'), attachment_ids=attachments.ids)
        self.sudo().activity_schedule('mail.mail_activity_data_todo', user_id=self.create_uid.id,
                                      summary=_('Verificar pago de servicio del grupo'))

    def _mercadopago_paid(self, tx):
        self.ensure_one()
        if self.state in ('paid', 'cancel'):
            return
        self.sudo().write({
            'payment_method': 'mercadopago',
            'mp_commission': max(tx.amount - self.amount, 0.0),
            'payment_reference': (tx.provider_reference or tx.reference or '')[:64],
        })
        self.sudo().action_register_payment()

    # ------------------------------------------------------------------ cobro a varios grupos
    @api.model
    def create_for_groups(self, groups, amount=None, concept=None, send=True):
        vals = [{'group_id': g.id, 'company_id': (g.company_id or self.env.company).id,
                 'amount': amount if amount is not None else self._default_amount(),
                 **({'concept': concept} if concept else {})} for g in groups]
        charges = self.create(vals)
        if send:
            charges._send_auto()
        return charges


class MailComposeRssService(models.TransientModel):
    _inherit = 'mail.compose.message'

    def _action_send_mail(self, auto_commit=False):
        res = super()._action_send_mail(auto_commit=auto_commit)
        if self.env.context.get('mark_rss_service_sent') and self.model == 'rss.service.charge':
            self.env['rss.service.charge'].browse(self._evaluate_res_ids())._mark_sent()
        return res


class PaymentTransactionRssService(models.Model):
    _inherit = 'payment.transaction'

    def _post_process(self):
        res = super()._post_process()
        self._rss_close_service_charges()
        return res

    def _rss_close_service_charges(self):
        for tx in self.filtered(lambda t: t.state == 'done' and t.reference):
            match = SERVICE_REFERENCE_RE.match(tx.reference)
            if not match:
                continue
            charge = self.env['rss.service.charge'].sudo().browse(int(match.group(1))).exists()
            if not charge:
                continue
            if tx.currency_id.compare_amounts(tx.amount, charge.amount) < 0:
                charge.message_post(body=_('Pago de Mercado Pago %(ref)s por un importe menor (%(amt)s); '
                                           'revísalo.', ref=tx.reference, amt=tx.amount))
                continue
            try:
                charge._mercadopago_paid(tx)
            except Exception:  # noqa: BLE001
                _logger.exception('Red Seguro Scout: no se pudo cerrar el cobro de servicio %s', charge.id)


class RssServiceChargeWizard(models.TransientModel):
    _name = 'rss.service.charge.wizard'
    _description = 'Cobrar servicios a grupos'

    group_ids = fields.Many2many('rss.scout.group', string='Grupos', required=True,
                                 default=lambda self: self._default_groups())
    amount = fields.Float('Importe por grupo', required=True,
                          default=lambda self: self.env['rss.service.charge']._default_amount())
    concept = fields.Char('Concepto', required=True, default=lambda self: _('Servicios de Red Seguro Scout'))
    send = fields.Boolean('Enviar la liga por correo a los representantes', default=True)
    remember = fields.Boolean('Usar este importe como cuota por omisión', default=True)

    @api.model
    def _default_groups(self):
        ctx = self.env.context
        if ctx.get('active_model') == 'rss.scout.group' and ctx.get('active_ids'):
            return self.env['rss.scout.group'].browse(ctx['active_ids'])
        return self.env['rss.scout.group'].search([])

    def action_create(self):
        self.ensure_one()
        if self.amount <= 0:
            raise UserError(_('Captura el importe por grupo.'))
        if self.remember:
            self.env['ir.config_parameter'].sudo().set_param(SERVICE_FEE_PARAM, self.amount)
        charges = self.env['rss.service.charge'].create_for_groups(
            self.group_ids, amount=self.amount, concept=self.concept, send=self.send)
        return {
            'type': 'ir.actions.act_window', 'name': _('Cobros de servicio'),
            'res_model': 'rss.service.charge', 'view_mode': 'list,form',
            'domain': [('id', 'in', charges.ids)],
        }
