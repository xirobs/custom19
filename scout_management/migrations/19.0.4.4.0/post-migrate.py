# -*- coding: utf-8 -*-
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['scout.section']._scout_route_defaults()
    env['scout.progression.stage']._scout_v44_migrate()
