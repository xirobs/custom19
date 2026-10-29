# -*- coding: utf-8 -*-
"""De dónde viene y de quién es cada cobro y cada pago en línea.

* Cada cargo (factura de cliente) guarda su origen: registro, tienda, campamento/evento, cuota, colaboración u
  otro, con la referencia (folio de la ficha o de la venta) y el miembro.
* Al pagar con Mercado Pago se manda a Mercado Pago un título legible ("Registro · Juan Pérez (Lobatos) · …") y
  los datos en `metadata`, para que en el panel de Mercado Pago también se sepa qué es cada cobro.
* Cuando Mercado Pago confirma, se guardan quién pagó (nombre y correo de la cuenta), el medio (tarjeta, OXXO…),
  la comisión y lo que llega neto; el pago contable queda ligado al miembro con un concepto claro.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

CHARGE_ORIGINS = [
    ('registration', 'Registro / reinscripción'),
    ('store', 'Tienda scout'),
    ('food', 'Alimentos'),
    ('event', 'Campamento / evento'),
    ('fee', 'Cuota'),
    ('donation', 'Colaboración / donativo'),
    ('other', 'Otro ingreso'),
]
ORIGIN_LABELS = dict(CHARGE_ORIGINS)
SALE_TYPE_ORIGIN = {'store': 'store', 'food': 'food', 'event': 'event', 'donation': 'donation'}


class AccountMovePaymentOrigin(models.Model):
    _inherit = 'account.move'

    scout_charge_origin = fields.Selection(CHARGE_ORIGINS, string='Origen del cobro', index=True, copy=False,
                                           tracking=True)
    scout_charge_ref = fields.Char(string='Referencia de origen', copy=False,
                                   help='Folio de la ficha de registro, de la venta o del evento que generó el cargo.')
    scout_payment_label = fields.Char(string='Concepto del cobro', compute='_compute_scout_payment_label')

    @api.depends('scout_charge_origin', 'scout_charge_ref', 'scout_member_id', 'partner_id', 'invoice_line_ids.name')
    def _compute_scout_payment_label(self):
        for move in self:
            move.scout_payment_label = move._scout_label()

    def _scout_label(self, with_ref=True):
        """"Registro / reinscripción · Juan Pérez (Lobatos) · REG/2026/0004"."""
        self.ensure_one()
        move = self.sudo()
        parts = [ORIGIN_LABELS.get(move.scout_charge_origin or move._scout_guess_origin()[0], _('Cobro'))]
        concept = (move.invoice_line_ids[:1].name or '').split('\n')[0].strip()
        member = move.scout_member_id
        if member:
            parts.append('%s%s' % (member.name, ' (%s)' % member.scout_section_id.name if member.scout_section_id
                                   else ''))
        elif move.partner_id:
            parts.append(move.partner_id.name)
        if concept and move.scout_charge_origin in ('event', 'fee', 'other', 'donation', False):
            parts.append(concept[:60])
        if with_ref:
            parts.append(move.scout_charge_ref or move.name or '')
        return ' · '.join(p for p in parts if p)

    def _scout_guess_origin(self):
        """(origen, referencia) para cargos creados antes de guardar el origen."""
        self.ensure_one()
        move = self.sudo()
        reg = self.env['scout.registration'].sudo().search([('invoice_id', '=', move.id)], limit=1)
        if reg:
            return 'registration', reg.name
        sale = self.env['scout.store.sale'].sudo().search([('invoice_id', '=', move.id)], limit=1)
        if sale:
            return SALE_TYPE_ORIGIN.get(sale.sale_type, 'store'), sale.name
        product = self.env.ref('scout_management.product_registration', raise_if_not_found=False)
        if product and product in move.invoice_line_ids.product_id:
            return 'registration', move.invoice_origin or ''
        return 'other', move.invoice_origin or ''

    @api.model
    def _scout_backfill_origin(self):
        moves = self.sudo().search([('move_type', '=', 'out_invoice'), ('scout_charge_origin', '=', False)])
        for move in moves:
            origin, ref = move._scout_guess_origin()
            move.write({'scout_charge_origin': origin, 'scout_charge_ref': move.scout_charge_ref or ref or False})
        return len(moves)

    @api.model
    def _scout_new_charge(self, payer, amount, concept, origin='other', member=None, category=None,
                          company=None, ref=None):
        """Cargo (factura de cliente) por cualquier ingreso para cobrarlo en línea con Mercado Pago."""
        company = company or self.env.company
        if amount <= 0:
            raise UserError(_('El importe debe ser mayor a cero.'))
        if not payer:
            raise UserError(_('Indica quién paga.'))
        self.env['scout.registration']._scout_check_sale_journal(company)
        line = {'name': concept or ORIGIN_LABELS.get(origin, _('Cobro')), 'quantity': 1, 'price_unit': amount,
                'tax_ids': [fields.Command.clear()]}
        if origin == 'registration':
            product = self.env.ref('scout_management.product_registration', raise_if_not_found=False)
            if product:
                line['product_id'] = product.id
        account = category._get_account(company) if category else False
        if account:
            line['account_id'] = account.id
        invoice = self.sudo().with_company(company).create({
            'move_type': 'out_invoice', 'partner_id': payer.id, 'scout_member_id': member.id if member else False,
            'invoice_date': fields.Date.context_today(self), 'invoice_origin': ref or False,
            'scout_charge_origin': origin, 'scout_charge_ref': ref or False,
            'invoice_line_ids': [fields.Command.create(line)],
        })
        invoice.action_post()
        invoice.message_post(body=_('Cobro creado por %s para pagar en línea.', self.env.user.name))
        return invoice


class PaymentTransactionScout(models.Model):
    _inherit = 'payment.transaction'

    scout_member_id = fields.Many2one('res.partner', string='Miembro', compute='_compute_scout_origin', store=True,
                                      index='btree_not_null')
    scout_section_id = fields.Many2one('scout.section', string='Sección', compute='_compute_scout_origin',
                                       store=True)
    scout_charge_origin = fields.Selection(CHARGE_ORIGINS, string='Origen', compute='_compute_scout_origin',
                                           store=True)
    scout_label = fields.Char(string='Concepto', compute='_compute_scout_origin', store=True)
    scout_payer_name = fields.Char(string='Pagó (cuenta de Mercado Pago)', readonly=True)
    scout_payer_email = fields.Char(string='Correo de quien pagó', readonly=True)
    scout_method_detail = fields.Char(string='Medio de pago', readonly=True)
    scout_fee_amount = fields.Monetary(string='Comisión Mercado Pago', currency_field='currency_id', readonly=True)
    scout_net_amount = fields.Monetary(string='Neto recibido', currency_field='currency_id', readonly=True)

    @api.depends('invoice_ids', 'invoice_ids.scout_member_id', 'invoice_ids.scout_charge_origin')
    def _compute_scout_origin(self):
        for tx in self:
            invoices = tx.invoice_ids if 'invoice_ids' in tx._fields else self.env['account.move']
            invoice = invoices[:1]
            if invoice:
                member = invoice.scout_member_id
                tx.scout_member_id = member
                tx.scout_section_id = member.scout_section_id
                tx.scout_charge_origin = invoice.scout_charge_origin or invoice._scout_guess_origin()[0]
                tx.scout_label = ', '.join(i._scout_label() for i in invoices)[:250]
            else:
                tx.scout_member_id = False
                tx.scout_section_id = False
                tx.scout_charge_origin = False
                tx.scout_label = False

    # ------------------------------------------------------------------ lo que ve Mercado Pago
    def _scout_mp_title(self):
        self.ensure_one()
        label = self.scout_label or ''
        return ('%s · %s' % (label, self.reference) if label and self.reference not in label else
                label or self.reference)[:250]

    def _scout_mp_metadata(self):
        self.ensure_one()
        member = self.scout_member_id
        return {k: v for k, v in {
            'grupo': self.company_id.name, 'origen': ORIGIN_LABELS.get(self.scout_charge_origin or '', ''),
            'miembro': member.name or '', 'seccion': self.scout_section_id.name or '',
            'cargos': ', '.join(self.invoice_ids.mapped('name')) if 'invoice_ids' in self._fields else '',
            'referencia_odoo': self.reference,
        }.items() if v}

    def _mercado_pago_prepare_preference_request_payload(self):
        payload = super()._mercado_pago_prepare_preference_request_payload()
        try:
            title = self._scout_mp_title()
            for item in payload.get('items') or []:
                item['title'] = title
                item['description'] = title
            payload['metadata'] = self._scout_mp_metadata()
        except Exception:  # noqa: BLE001 - nunca impedir el cobro por la descripción
            _logger.exception('No se pudo poner la descripción scout en Mercado Pago')
        return payload

    def _mercado_pago_prepare_payment_request_payload(self):
        payload = super()._mercado_pago_prepare_payment_request_payload()
        try:
            title = self._scout_mp_title()
            payload['description'] = title
            for item in (payload.get('additional_info') or {}).get('items') or []:
                item['title'] = title
            payload['metadata'] = self._scout_mp_metadata()
        except Exception:  # noqa: BLE001
            _logger.exception('No se pudo poner la descripción scout en Mercado Pago')
        return payload

    # ------------------------------------------------------------------ lo que regresa Mercado Pago
    def _apply_updates(self, payment_data):
        res = super()._apply_updates(payment_data)
        if self.provider_code == 'mercado_pago' and isinstance(payment_data, dict):
            try:
                self._scout_store_payer(payment_data)
            except Exception:  # noqa: BLE001
                _logger.exception('No se pudieron guardar los datos de quien pagó')
        return res

    def _scout_store_payer(self, data):
        payer = data.get('payer') or {}
        name = ' '.join(x for x in (payer.get('first_name'), payer.get('last_name')) if x).strip()
        card = data.get('card') or {}
        holder = (card.get('cardholder') or {}).get('name') or ''
        method = data.get('payment_method_id') or ''
        kind = {'credit_card': _('Tarjeta de crédito'), 'debit_card': _('Tarjeta de débito'),
                'ticket': _('Efectivo (OXXO / tienda)'), 'bank_transfer': _('Transferencia / SPEI'),
                'account_money': _('Saldo Mercado Pago'), 'atm': _('Cajero')}.get(data.get('payment_type_id'), '')
        last4 = card.get('last_four_digits')
        detail = ' '.join(x for x in (kind, method.upper() if method else '', ('•••• %s' % last4) if last4 else '')
                          if x)
        details = data.get('transaction_details') or {}
        fees = sum(float(f.get('amount') or 0) for f in data.get('fee_details') or [])
        vals = {'scout_payer_name': (name or holder or '')[:120] or False,
                'scout_payer_email': (payer.get('email') or '')[:120] or False,
                'scout_method_detail': detail[:120] or False}
        if fees:
            vals['scout_fee_amount'] = fees
        if details.get('net_received_amount'):
            vals['scout_net_amount'] = float(details['net_received_amount'])
        self.write({k: v for k, v in vals.items() if v or k.startswith('scout_payer')})

    def _create_payment(self, **extra_create_values):
        """El pago contable queda ligado al miembro y con un concepto que dice de qué y de quién es."""
        try:
            if self.scout_member_id and 'scout_member_id' not in extra_create_values:
                extra_create_values['scout_member_id'] = self.scout_member_id.id
            if self.scout_label and 'memo' not in extra_create_values:
                who = self.scout_payer_name or self.partner_name or ''
                extra_create_values['memo'] = ('%s - %s · %s%s' % (
                    self.reference, self.provider_reference or '', self.scout_label,
                    (' · pagó %s' % who) if who else ''))[:250]
        except Exception:  # noqa: BLE001
            _logger.exception('No se pudo identificar el pago en línea')
        return super()._create_payment(**extra_create_values)
