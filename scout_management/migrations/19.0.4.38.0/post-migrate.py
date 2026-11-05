# -*- coding: utf-8 -*-
"""Ubica en su nivel de la ruta el material que solo tenía la categoría con el nombre del nivel."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['scout.resource'].with_context(active_test=False).search([('stage_id', '=', False)])._scout_autoplace()
