# -*- coding: utf-8 -*-
"""El "saldo inicial" capturado como sobrante de arqueo pasa al concepto "Saldo inicial", que no cuenta como
ingreso del mes (se suma al saldo con el que arranca la cuenta).

Se hace con SQL porque los movimientos registrados no permiten cambiar su concepto desde el ORM; el asiento
contable no cambia (solo cómo se agrupa en los reportes)."""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    opening = env.ref('scout_management.category_opening_balance', raise_if_not_found=False)
    adjust = env.ref('scout_management.category_adjust_in', raise_if_not_found=False)
    if not opening or not adjust:
        return
    cr.execute("""
        UPDATE scout_treasury_move SET category_id = %s
         WHERE category_id = %s AND move_type = 'income' AND description ILIKE %s
     RETURNING id""", (opening.id, adjust.id, '%saldo inicial%'))
    ids = [r[0] for r in cr.fetchall()]
    env['scout.treasury.move'].invalidate_model(['category_id'])
    _logger.info('scout_management 19.0.4.42.0 (migración): %s movimientos pasan a "Saldo inicial": %s', len(ids), ids)
