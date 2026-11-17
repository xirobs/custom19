# -*- coding: utf-8 -*-
"""Avisos a la administración de la Red: cada recibo emitido, pago con Mercado Pago, comprobante
por validar o pago de prueba ignorado llega por correo y a las notificaciones de Odoo (campana y
app del celular), y queda en el historial del registro."""
from markupsafe import Markup

from odoo import _, api, fields, models


def _money(record, amount):
    return '$ {:,.2f}'.format(amount or 0)


def rss_alert(record, title, details):
    """Nota en el historial del registro que notifica a quienes reciben los avisos de pagos."""
    env = record.env
    company = record.company_id if 'company_id' in record._fields and record.company_id else env.company
    partners = company.sudo().rss_payment_notify_partner_ids
    if not partners:
        group = env.ref('red_seguro_scout.group_rss_manager', raise_if_not_found=False)
        users = group.sudo().user_ids.filtered(lambda u: u.active and not u.share) if group else env['res.users']
        partners = users.partner_id
    base = env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
    url = '%s/odoo/%s/%s' % (base, record._name, record.id)
    items = Markup('').join(Markup('<li>%s</li>') % d for d in details if d)
    body = (Markup('<p><strong>%s</strong></p><ul>%s</ul><p><a href="%s">Abrir en la plataforma</a></p>')
            % (title, items, url))
    record.sudo().message_post(body=body, subject='%s · Red Seguro Scout' % title, partner_ids=partners.ids,
                               subtype_xmlid='mail.mt_note')


class ResCompanyRssAlerts(models.Model):
    _inherit = 'res.company'

    rss_payment_notify_partner_ids = fields.Many2many(
        'res.partner', 'rss_company_payment_notify_rel', 'company_id', 'partner_id',
        string='Avisos de pagos a',
        help='Personas que reciben un aviso (correo y notificación) por cada pago o recibo. '
             'Vacío: los administradores de Red Seguro Scout.')


class ResConfigSettingsRssAlerts(models.TransientModel):
    _inherit = 'res.config.settings'

    rss_payment_notify_partner_ids = fields.Many2many(related='company_id.rss_payment_notify_partner_ids',
                                                      readonly=False)


class RssPaymentAlerts(models.Model):
    _inherit = 'rss.payment'

    def action_confirm(self):
        drafts = self.filtered(lambda p: p.state == 'draft')
        res = super().action_confirm()
        for pay in drafts.filtered(lambda p: p.state == 'confirmed'):
            method = dict(pay._fields['payment_method'].selection).get(pay.payment_method, '')
            origin = (_('Carga del portal %s', pay.import_batch_id.reference or pay.import_batch_id.display_name)
                      if pay.import_batch_id else _('Registrado en la plataforma'))
            rss_alert(pay, _('Recibo %(name)s emitido · %(group)s', name=pay.name, group=pay.group_id.name), [
                _('Importe: %s', _money(pay, pay.amount)),
                _('Personas cubiertas: %s', pay.member_count),
                _('Forma de pago: %s', method) if method else '',
                _('Referencia / transacción: %s', pay.reference) if pay.reference else '',
                _('Comisión de Mercado Pago: %s', _money(pay, pay.mp_commission)) if pay.mp_commission else '',
                _('Origen: %s', origin),
                _('Registró: %s', (pay.received_by or self.env.user).name),
            ])
        return res

    def _group_submit_transfer(self, reference, tracking_key, files):
        res = super()._group_submit_transfer(reference, tracking_key, files)
        rss_alert(self, _('Comprobante de transferencia por validar · %s', self.group_id.name), [
            _('Importe: %s', _money(self, self.amount_expected)),
            _('Referencia: %s', self.request_reference),
            _('Folio / clave de rastreo: %s', reference or tracking_key) if (reference or tracking_key) else '',
            _('Verifica el depósito y presiona «Registrar pago y emitir recibo».'),
        ])
        return res


class RssImportBatchAlerts(models.Model):
    _inherit = 'rss.import.batch'

    def action_submit_transfer(self, reference, transfer_date=None, files=()):
        res = super().action_submit_transfer(reference, transfer_date=transfer_date, files=files)
        rss_alert(self, _('Comprobante de transferencia por validar · %s', self.group_id.name), [
            _('Carga: %s', self.reference),
            _('Importe: %s', _money(self, self.amount_total)),
            _('Personas: %s', self.quantity),
            _('Referencia: %s', reference),
            _('Verifica el depósito y presiona «Validar pago: sí llegó».'),
        ])
        return res


class RssServiceChargeAlerts(models.Model):
    _inherit = 'rss.service.charge'

    def action_register_payment(self):
        opened = self.filtered(lambda c: c.state not in ('paid', 'cancel'))
        res = super().action_register_payment()
        for charge in opened.filtered(lambda c: c.state == 'paid'):
            method = dict(charge._fields['payment_method'].selection).get(charge.payment_method, '')
            rss_alert(charge, _('Pago de servicio %(name)s · %(group)s', name=charge.name, group=charge.group_id.name), [
                _('Concepto: %s', charge.concept),
                _('Importe: %s', _money(charge, charge.amount)),
                _('Forma de pago: %s', method) if method else '',
                _('Referencia / transacción: %s', charge.payment_reference) if charge.payment_reference else '',
                _('Registró: %s', self.env.user.name),
            ])
        return res

    def _group_submit_transfer(self, reference, tracking_key, files):
        res = super()._group_submit_transfer(reference, tracking_key, files)
        rss_alert(self, _('Comprobante de servicio por validar · %s', self.group_id.name), [
            _('Cobro: %s', self.name),
            _('Importe: %s', _money(self, self.amount)),
            _('Verifica el depósito y presiona «Registrar pago».'),
        ])
        return res


class PaymentTransactionRssAlerts(models.Model):
    _inherit = 'payment.transaction'

    def _rss_is_test_payment(self, record=None):
        is_test = super()._rss_is_test_payment(record)
        if is_test and record is not None:
            rss_alert(record, _('Pago de PRUEBA ignorado · %s', record.display_name), [
                _('Transacción: %s', self.reference),
                _('Importe: %s', _money(record, self.amount)),
                _('Mercado Pago está en modo de prueba: no entró dinero real y no se registró el pago.'),
            ])
        return is_test
