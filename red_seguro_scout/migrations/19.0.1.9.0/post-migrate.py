# -*- coding: utf-8 -*-
"""Vuelve a leer los certificados ya anexados con el lector corregido (número de póliza
pegado a «CERTIFICADO», vigencias en otra columna, domicilio con doble coma)."""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    insured = env['rss.insured'].with_context(active_test=False).search([('certificate_attachment_id', '!=', False)])
    for rec in insured:
        try:
            with cr.savepoint():
                rec.action_reread_certificate()
        except Exception:  # noqa: BLE001
            _logger.warning('No se pudo releer el certificado de %s', rec.display_name, exc_info=True)
    _logger.info('Red Seguro Scout: certificados releídos en %s asegurados', len(insured))
