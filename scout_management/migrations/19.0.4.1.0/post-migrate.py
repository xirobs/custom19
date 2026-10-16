# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['scout.section']._scout_route_defaults()
    # Crea el registro de permanencia de cada beneficiario y calcula sus alertas (sin avisos masivos el primer día).
    env['res.partner'].search([('is_scout', '=', True), ('scout_type', '=', 'youth'),
                               ('scout_state', 'in', ('trial', 'active'))])._scout_ensure_route()
    routes = env['scout.route'].search([('is_open', '=', True)])
    routes._scout_refresh_status(notify=False)
    for route in routes:
        route.with_context(scout_route_no_refresh=True, scout_route_system=True).write(
                {'notified_state': f'{route.state}:{route.alert}'})
