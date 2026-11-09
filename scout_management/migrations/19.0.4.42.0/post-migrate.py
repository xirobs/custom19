# -*- coding: utf-8 -*-
"""El "saldo inicial" capturado como sobrante de arqueo pasa al concepto "Saldo inicial", que no cuenta como
ingreso del mes (se suma al saldo con el que arranca la cuenta)."""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    opening = env.ref('scout_management.category_opening_balance', raise_if_not_found=False)
    adjust = env.ref('scout_management.category_adjust_in', raise_if_not_found=False)
    if not opening or not adjust:
        return
    moves = env['scout.treasury.move'].search([('category_id', '=', adjust.id), ('move_type', '=', 'income'),
                                               ('description', 'ilike', 'saldo inicial')])
    moves.write({'category_id': opening.id})
