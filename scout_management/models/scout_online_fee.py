# -*- coding: utf-8 -*-
"""Comisión del pago en línea (Mercado Pago) a cargo del cliente.

Cuando la familia elige pagar en línea un cargo mayor al mínimo configurado, se agrega al cargo una línea
"Comisión por pago en línea" calculada para que al grupo le llegue completo lo que cobra
(monto + comisión fija con IVA, dividido entre 1 - porcentaje con IVA). Si al final paga en efectivo o por
transferencia, la línea se quita antes de registrar el pago.
"""
import logging

from odoo import _, api, fields, models
from odoo.tools import float_compare, float_round

_logger = logging.getLogger(__name__)


class ResCompanyOnlineFee(models.Model):
    _inherit = 'res.company'

    scout_online_fee_active = fields.Boolean(
        string='Cobrar al cliente la comisión del pago en línea', default=True,
        help='Se suma al cargo solo cuando la familia paga en línea (Mercado Pago).')
    scout_online_fee_min = fields.Float(string='A partir de (monto)', default=100.0,
                                        help='Cargos mayores a este monto llevan la comisión.')
    scout_online_fee_pct = fields.Float(string='Comisión (%)', default=3.49, digits=(5, 2))
    scout_online_fee_fixed = fields.Float(string='Cargo fijo por cobro', default=4.0, digits=(10, 2))
    scout_online_fee_vat = fields.Float(string='IVA sobre la comisión (%)', default=16.0, digits=(5, 2))

    def _scout_online_fee(self, amount):
        """Comisión a sumar para que, después de lo que cobra Mercado Pago, el grupo reciba `amount` completo."""
        company = self[:1].sudo()
        if not company or not company.scout_online_fee_active or amount <= (company.scout_online_fee_min or 0.0):
            return 0.0
        tax = 1.0 + (company.scout_online_fee_vat or 0.0) / 100.0
        rate = (company.scout_online_fee_pct or 0.0) / 100.0 * tax
        if rate >= 1.0:
            return 0.0
        total = (amount + (company.scout_online_fee_fixed or 0.0) * tax) / (1.0 - rate)
        return float_round(total - amount, precision_rounding=company.currency_id.rounding or 0.01, rounding_method='UP')


class AccountMoveOnlineFee(models.Model):
    _inherit = 'account.move'

    scout_online_fee = fields.Monetary(string='Comisión por pago en línea', compute='_compute_scout_online_fee',
                                       currency_field='currency_id')

    def _scout_fee_product(self):
        return self.env.ref('scout_management.product_online_fee', raise_if_not_found=False)

    def _scout_fee_lines(self):
        product = self._scout_fee_product()
        return self.invoice_line_ids.filtered(lambda l: product and l.product_id == product)

    @api.depends('invoice_line_ids.price_total')
    def _compute_scout_online_fee(self):
        for move in self:
            move.scout_online_fee = sum(move._scout_fee_lines().mapped('price_total'))

    def _scout_fee_base(self):
        """Saldo sin la comisión (lo que se cobra en efectivo o transferencia)."""
        self.ensure_one()
        return self.amount_residual - self.scout_online_fee

    def _scout_fee_preview(self):
        """Comisión que tendría el cargo si se paga en línea (sin modificarlo)."""
        self.ensure_one()
        return self.company_id._scout_online_fee(self._scout_fee_base())

    def _scout_fee_editable(self):
        self.ensure_one()
        return self.move_type == 'out_invoice' and self.state == 'posted' and self.payment_state == 'not_paid' \
            and not self.sudo().transaction_ids.filtered(lambda t: t.state in ('pending', 'authorized', 'done'))

    def _scout_set_online_fee(self, fee):
        """Deja el cargo con esa comisión (0 = la quita). Lo regresa a borrador y lo vuelve a publicar."""
        product = self._scout_fee_product()
        for move in self.sudo():
            current = sum(move._scout_fee_lines().mapped('price_unit'))
            if not product or not move._scout_fee_editable() \
                    or float_compare(current, fee, precision_rounding=move.currency_id.rounding or 0.01) == 0:
                continue
            try:
                with self.env.cr.savepoint():
                    move.with_context(scout_approved=True).button_draft()
                    lines = move._scout_fee_lines()
                    if fee:
                        if lines:
                            lines[0].write({'price_unit': fee, 'quantity': 1})
                            lines[1:].unlink()
                        else:
                            move.write({'invoice_line_ids': [fields.Command.create({
                                'product_id': product.id, 'name': _('Comisión por pago en línea (Mercado Pago)'),
                                'quantity': 1, 'price_unit': fee, 'tax_ids': [fields.Command.clear()],
                            })]})
                    else:
                        lines.unlink()
                    move.with_context(scout_approved=True).action_post()
            except Exception:  # noqa: BLE001 - nunca impedir el cobro por la comisión
                _logger.exception('No se pudo ajustar la comisión en línea de %s', move.name)

    def _scout_apply_online_fee(self):
        for move in self:
            if move._scout_fee_editable():
                move._scout_set_online_fee(move.company_id._scout_online_fee(move._scout_fee_base()))

    def _scout_remove_online_fee(self):
        self.filtered(lambda m: m.scout_online_fee)._scout_set_online_fee(0.0)


class AccountPaymentRegisterOnlineFee(models.TransientModel):
    _inherit = 'account.payment.register'

    @api.model
    def default_get(self, fields_list):
        """Pago registrado a mano (efectivo, transferencia): se quita la comisión del pago en línea."""
        ctx = self.env.context
        ids = ctx.get('active_ids') or ([ctx['active_id']] if ctx.get('active_id') else [])
        if ids and ctx.get('active_model') in ('account.move', 'account.move.line'):
            records = self.env[ctx['active_model']].browse(ids).exists()
            moves = records if ctx['active_model'] == 'account.move' else records.move_id
            moves.filtered(lambda m: m.move_type == 'out_invoice')._scout_remove_online_fee()
        return super().default_get(fields_list)
