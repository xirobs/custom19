# -*- coding: utf-8 -*-
"""Origen de los cargos existentes y datos de identificación de los pagos en línea ya hechos."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['account.move']._scout_backfill_origin()
    txs = env['payment.transaction'].search([])
    if txs:
        for fname in ('scout_member_id', 'scout_section_id', 'scout_charge_origin', 'scout_label'):
            env.add_to_compute(txs._fields[fname], txs)
        env.flush_all()
