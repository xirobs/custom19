# -*- coding: utf-8 -*-
"""Trae de Mercado Pago las comisiones de los pagos que ya se recibieron (si hay conexión)."""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for company in env['res.company'].search([]):
        try:
            res = env['scout.treasury.ledger'].with_company(company)._scout_mp_backfill(company)
            _logger.info('Comisiones de Mercado Pago (%s): %s', company.name, res)
        except Exception:  # noqa: BLE001 - se puede hacer después con el botón
            _logger.info('No se pudieron traer comisiones de Mercado Pago', exc_info=True)
