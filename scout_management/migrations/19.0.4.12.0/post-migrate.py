# -*- coding: utf-8 -*-
"""Recursos sin sección: eran de todo el grupo (ahora se marca explícito "Para todas las secciones")."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['scout.resource'].with_context(active_test=False).search([('section_ids', '=', False)]).write(
        {'all_sections': True})
