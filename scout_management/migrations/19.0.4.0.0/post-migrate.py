# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['scout.progression.stage']._scout_v4_migrate()
    # Las actividades que ya existían quedan aprobadas (el flujo de visto bueno aplica a las nuevas).
    env['calendar.event'].with_context(active_test=False).search(
        [('is_scout_event', '=', True)]).write({'scout_plan_state': 'approved'})
