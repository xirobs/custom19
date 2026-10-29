# -*- coding: utf-8 -*-
"""Cobro del seguro desde el portal: tarifas por periodo, cuenta para transferencias y
comisión de Mercado Pago (que se traslada al grupo)."""
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class RssPolicyTariff(models.Model):
    _name = 'rss.policy.tariff'
    _description = 'Tarifa del seguro por periodo de alta'
    _order = 'policy_id, date_from'

    policy_id = fields.Many2one('rss.policy', required=True, ondelete='cascade', index=True)
    name = fields.Char('Periodo', required=True, help='P. ej. «Primer trimestre».')
    date_from = fields.Date('Altas desde', required=True)
    date_to = fields.Date('Altas hasta', required=True)
    currency_id = fields.Many2one(related='policy_id.currency_id')
    amount = fields.Monetary('Importe por persona', required=True)

    @api.constrains('date_from', 'date_to', 'policy_id')
    def _check_dates(self):
        for tariff in self:
            if tariff.date_to < tariff.date_from:
                raise ValidationError(_('En %s la fecha final es anterior a la inicial.', tariff.name))
            overlap = self.search_count([
                ('policy_id', '=', tariff.policy_id.id), ('id', '!=', tariff.id),
                ('date_from', '<=', tariff.date_to), ('date_to', '>=', tariff.date_from)])
            if overlap:
                raise ValidationError(_('Los periodos de tarifa de la póliza no deben encimarse (%s).',
                                        tariff.name))


class RssPolicyBilling(models.Model):
    _inherit = 'rss.policy'

    tariff_ids = fields.One2many('rss.policy.tariff', 'policy_id', string='Tarifas por periodo')
    # Transferencia
    bank_beneficiary = fields.Char('Beneficiario')
    bank_name = fields.Char('Banco')
    bank_clabe = fields.Char('CLABE')
    bank_account = fields.Char('Cuenta')
    bank_card = fields.Char('Tarjeta (para depósito)')
    bank_notes = fields.Text('Indicaciones para la transferencia')
    # Mercado Pago
    mp_pass_fee = fields.Boolean('Cobrar la comisión de Mercado Pago al grupo', default=True)
    mp_fee_percent = fields.Float('Comisión Mercado Pago (%)', default=3.49, digits=(5, 2))
    mp_fee_fixed = fields.Monetary('Cargo fijo por cobro', default=4.0)
    mp_fee_vat = fields.Float('IVA sobre la comisión (%)', default=16.0, digits=(5, 2))

    def _tariff_for(self, day):
        """Importe por persona según la fecha de alta (o la cuota anual si no hay tabla)."""
        self.ensure_one()
        tariff = self.tariff_ids.filtered(lambda t: t.date_from <= day <= t.date_to)[:1]
        return tariff, (tariff.amount if tariff else self.premium_per_member)

    def _mercado_pago_total(self, net):
        """Total a cobrar con Mercado Pago para que, descontada la comisión (con IVA),
        llegue completo ``net``: total = (net + fijo·(1+IVA)) / (1 − %·(1+IVA))."""
        self.ensure_one()
        if not self.mp_pass_fee or not net:
            return net, 0.0
        vat = 1 + (self.mp_fee_vat or 0.0) / 100.0
        pct = (self.mp_fee_percent or 0.0) / 100.0 * vat
        fixed = (self.mp_fee_fixed or 0.0) * vat
        total = self.currency_id.round((net + fixed) / (1 - pct)) if pct < 1 else net
        return total, self.currency_id.round(total - net)
