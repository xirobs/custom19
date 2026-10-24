# -*- coding: utf-8 -*-
"""Castores: ruta de enlace a los 6 años y pase a los 6 años 6 meses; las niñas pasan a Gacelas.
Recalcula el destino de las rutas abiertas según el sexo de cada beneficiario."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    castores = env.ref('scout_management.section_castores', raise_if_not_found=False)
    gacelas = env.ref('scout_management.section_gacelas', raise_if_not_found=False)
    lobatos = env.ref('scout_management.section_lobatos', raise_if_not_found=False)
    sections = env['scout.section'].search([])
    for section in sections:
        is_castores = section == castores or (section.name or '').strip().lower().startswith('castor')
        if not is_castores:
            continue
        vals = {}
        route, pass_, _stay = section._route_offsets()
        if (route, pass_) in ((66, 72), (0, 0)):
            vals.update(route_age_years=6, route_age_months=0, pass_age_years=6, pass_age_months=6,
                        transition_age=6)
        if gacelas and not section.next_section_female_id:
            vals['next_section_female_id'] = gacelas.id
        if lobatos and not section.next_section_id:
            vals['next_section_id'] = lobatos.id
        if vals:
            section.write(vals)
    members = env['res.partner'].search([('is_scout', '=', True), ('scout_type', '=', 'youth'),
                                         ('scout_state', 'in', ('trial', 'active')), ('scout_section_id', '!=', False)])
    members._scout_ensure_route()
    env['scout.route'].search([('is_open', '=', True)])._scout_refresh_status(notify=False)
