# -*- coding: utf-8 -*-
"""Los pagos de Red Seguro Scout (referencia "RSV-") entran por la misma cuenta de Mercado Pago pero no son
dinero del grupo: se anula la comisión que la tesorería del grupo les había registrado. El pago en sí se
excluye de saldos y reportes por su referencia."""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['ir.config_parameter'].sudo().set_param(
        'scout_management.treasury_foreign_prefixes',
        env['ir.config_parameter'].sudo().get_param('scout_management.treasury_foreign_prefixes') or 'RSV-')
    txs = env['scout.treasury.ledger']._foreign_transactions()
    done = []
    for tx in txs.filtered('scout_fee_move_id'):
        fee = tx.scout_fee_move_id
        try:
            with cr.savepoint():
                if fee.account_move_id and fee.account_move_id.state == 'posted':
                    fee.account_move_id.button_draft()
                    fee.account_move_id.button_cancel()
                fee.write({'state': 'cancel'})
                fee.message_post(body='Anulado: comisión de un pago de Red Seguro Scout, no es del grupo.')
                done.append(fee.name)
        except Exception:
            _logger.warning('No se pudo anular la comisión %s', fee.name, exc_info=True)
    _logger.info('scout_management 19.0.4.48.0: comisiones de pagos ajenos anuladas: %s', done)
