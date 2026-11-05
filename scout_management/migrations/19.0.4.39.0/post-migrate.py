# -*- coding: utf-8 -*-
"""Nuevos ingresos (en prueba) que se dieron de alta como scouts en una sección juvenil pero ya pasaron la edad de
todas las secciones (adultos): pasan a Aspirante a jefe en la jefatura, apoyando en la sección donde se registraron."""
import logging

from odoo import SUPERUSER_ID, Command, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Section = env['scout.section']
    members = env['res.partner'].search([('is_scout', '=', True), ('scout_type', '=', 'youth'),
                                         ('scout_state', '=', 'trial'), ('scout_birthdate', '!=', False)])
    for member in members:
        if Section._scout_suggest(member.scout_birthdate, member.scout_gender) or \
                not Section._scout_over_age(member.scout_birthdate, member.scout_gender):
            continue
        section = member.scout_section_id
        leadership, position = Section._scout_aspirant(member.company_id)
        vals = {'scout_type': 'leader', 'scout_position_id': position.id or False, 'scout_patrol_id': False}
        if leadership:
            vals['scout_section_id'] = leadership.id
        if section and not section.is_leadership:
            vals['scout_leading_section_ids'] = [Command.link(section.id)]
        member.with_context(scout_change_approved=True).write(vals)
        member.message_post(body='Por su edad pasó a Aspirante a jefe (apoyando en %s).' % (section.name or '-'))
        _logger.info('Aspirante a jefe: %s', member.name)
