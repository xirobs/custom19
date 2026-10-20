# -*- coding: utf-8 -*-
"""Activa la junta semanal automática (sábado 15:45 a 18:00) en las secciones y colorea el calendario por sección."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    sections = env['scout.section'].search([('is_leadership', '=', False)])
    sections.filtered(lambda s: not s.session_weekday).write({'session_weekday': '5'})
    sections.write({'session_auto': True})
    sections.filtered(lambda s: not s.session_hour_end).write({'session_hour_start': 15.75, 'session_hour_end': 18.0})
    sections.filtered(lambda s: not s.session_name).write({'session_name': 'Junta'})
    # Colores distintos por sección (si había repetidos o sin color)
    used = set()
    palette = [1, 2, 3, 4, 6, 8, 9, 10, 11, 5, 7]
    for section in env['scout.section'].search([], order='sequence, id'):
        if not section.color or section.color in used:
            free = [c for c in palette if c not in used]
            if free:
                section.color = free[0]
        used.add(section.color)
    events = env['calendar.event'].search([('is_scout_event', '=', True)])
    env.add_to_compute(env['calendar.event']._fields['scout_section_color'], events)
    events.flush_recordset(['scout_section_color'])
