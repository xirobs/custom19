# -*- coding: utf-8 -*-
"""Recalcula "¿Pagó?" de las ventas: en 19.0.4.46.0 dejó de actualizarse y las ventas pagadas
quedaban como "Por cobrar"."""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    sales = env['scout.store.sale'].with_context(active_test=False).search([])
    env.add_to_compute(sales._fields['paid_state'], sales)
    sales._recompute_recordset(['paid_state'])
