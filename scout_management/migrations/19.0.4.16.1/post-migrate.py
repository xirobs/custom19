# -*- coding: utf-8 -*-
"""Quita de las listas de asistencia (marcados como falta) a miembros que no son de esa sección,
por ejemplo niñas que aparecían en Lobatos aunque su ficha dice Castores o Gacelas."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    events = env['scout.attendance.line'].search([('status', '=', 'absent')]).event_id
    events._scout_prune_attendance(include_closed=True)
