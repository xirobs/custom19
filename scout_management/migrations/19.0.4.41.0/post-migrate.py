# -*- coding: utf-8 -*-
"""Cancela los cargos de registro que quedaron pendientes de fichas eliminadas o canceladas
(registros de prueba), para que no salgan en cuentas por cobrar ni en el informe de tesorería.
Solo toca cargos SIN ningún pago."""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Move = env['account.move'].with_context(active_test=False)
    charges = Move.search([('move_type', '=', 'out_invoice'), ('state', '=', 'posted'),
                           ('scout_charge_origin', '=', 'registration'),
                           ('payment_state', 'in', ('not_paid', False))])
    done = []
    for inv in charges:
        if not inv.currency_id.is_zero(inv.amount_total - inv.amount_residual):
            continue
        if not inv._scout_dead_charge():
            continue
        try:
            with cr.savepoint():
                inv.button_draft()
                inv.button_cancel()
                inv.message_post(body='Cargo cancelado automáticamente: su ficha de registro fue eliminada o '
                                      'cancelada (registro de prueba).')
                done.append(inv.name)
        except Exception:
            _logger.warning('No se pudo cancelar el cargo %s', inv.name, exc_info=True)
    _logger.info('scout_management 19.0.4.41.0: %s cargos de registro huérfanos cancelados: %s',
                 len(done), ', '.join(done))
