# -*- coding: utf-8 -*-
"""Jefe de grupo, secretaría y tesorería: acceso administrativo completo según su cargo."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    partners = env['res.partner'].search([('is_scout', '=', True), ('user_ids', '!=', False),
                                          ('scout_position_id.kind', 'in', ('group_chief', 'secretary', 'treasurer'))])
    partners._scout_apply_admin_access()
