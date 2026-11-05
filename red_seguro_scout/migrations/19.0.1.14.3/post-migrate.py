# -*- coding: utf-8 -*-
"""Pagos guardados sin organización (grupo sin compañía): se les asigna una para que el recibo tenga moneda."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    company = env['res.company'].search([], limit=1, order='id')
    for pay in env['rss.payment'].with_context(active_test=False).search([('company_id', '=', False)]):
        pay.company_id = pay.group_id.company_id or pay.policy_id.company_id or company
