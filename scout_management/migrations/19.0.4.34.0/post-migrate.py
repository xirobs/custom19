# -*- coding: utf-8 -*-
"""Una sola ficha por miembro: si alguien quedó con una ficha abierta (sin validar ni pagar) de un ciclo anterior
y otra del ciclo nuevo, se conserva la del ciclo nuevo y la anterior se cancela con nota."""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cancelled = env['scout.registration']._scout_merge_duplicates()
    if cancelled:
        _logger.info("Fichas duplicadas de renovación canceladas: %s", ', '.join(cancelled.mapped('name')))
