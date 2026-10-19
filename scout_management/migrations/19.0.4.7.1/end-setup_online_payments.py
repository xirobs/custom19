# -*- coding: utf-8 -*-
"""Crea/asigna el diario "Mercado Pago" al final de la actualización, con todos los módulos ya cargados."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['res.company']._scout_setup_online_payments()
