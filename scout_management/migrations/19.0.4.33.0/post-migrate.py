# -*- coding: utf-8 -*-
"""Cuotas escalonadas del registro: octubre a diciembre $250, desde enero $300, dirigentes e hijos $200.
Solo se llenan en el ciclo con inscripciones abiertas que aún no tenga cuota anticipada."""
from datetime import date

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    today = date.today()
    # temporada de inscripción: octubre-diciembre del año en curso (o del anterior si ya es enero-septiembre)
    year = today.year if today.month >= 10 else today.year - 1
    for company in env['res.company'].search([]):
        cycle = env['scout.cycle'].with_company(company)._get_registration(company)
        if not cycle or cycle.company_id != company or cycle.early_fee:
            continue
        cycle.write({
            'early_fee': 250.0,
            'early_from': date(year, 10, 1),
            'early_until': date(year, 12, 31),
            'registration_fee': 300.0,
            'leader_fee': 200.0,
        })
