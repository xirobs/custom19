# -*- coding: utf-8 -*-
"""Asegurados con fecha de nacimiento pero sin sección: se les asigna la sección por edad."""
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.red_seguro_scout.models.rss_common import section_for_age

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    insured = env['rss.insured'].with_context(active_test=False).search(
        [('section', '=', False), ('birthdate', '!=', False)])
    for rec in insured:
        rec.section = section_for_age(rec.age)
    _logger.info('Red Seguro Scout: sección asignada por edad a %s asegurados', len(insured))
