# -*- coding: utf-8 -*-
from odoo import api, models


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    def _scout_setup_simple_treasury(self):
        """Tesorería simple: los cobros y pagos se registran directo en la cuenta de la caja o banco,
        sin cuentas puente ni conciliación bancaria, para que el saldo de cada caja/banco sea inmediato."""
        for journal in self.filtered(lambda j: j.type in ('cash', 'bank') and j.default_account_id):
            lines = journal.inbound_payment_method_line_ids | journal.outbound_payment_method_line_ids
            lines.filtered(lambda l: not l.payment_account_id).write(
                {'payment_account_id': journal.default_account_id.id})
            # Pasarelas (Mercado Pago): el cobro en línea suma directo al saldo de su diario.
            provider_lines = journal.inbound_payment_method_line_ids.filtered(
                lambda l: 'payment_provider_id' in l._fields and l.payment_provider_id
                and l.payment_account_id != journal.default_account_id)
            provider_lines.write({'payment_account_id': journal.default_account_id.id})

    @api.model_create_multi
    def create(self, vals_list):
        journals = super().create(vals_list)
        journals._scout_setup_simple_treasury()
        return journals

    @api.model
    def action_scout_setup_simple_treasury(self):
        self.search([('type', 'in', ('cash', 'bank'))])._scout_setup_simple_treasury()
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': 'Tesorería simple', 'type': 'success',
                           'message': 'Cajas y bancos listos: cobros y pagos afectan directo su saldo.'}}
