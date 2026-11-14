# -*- coding: utf-8 -*-
"""Limpia "Secciones a cargo" de scouts que no son dirigentes (salían en listas de asistencia de otra sección)."""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    youth = env['res.partner'].with_context(active_test=False).search([
        ('is_scout', '=', True), ('scout_type', '!=', 'leader'), ('scout_leading_section_ids', '!=', False)])
    youth.write({'scout_leading_section_ids': [(5, 0, 0)]})
