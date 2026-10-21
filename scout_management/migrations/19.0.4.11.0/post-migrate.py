# -*- coding: utf-8 -*-
"""Datos de transferencia del Grupo 7 Ethnos (se pueden cambiar en Configuración → Datos del grupo)."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    companies = env['res.company'].search([('name', 'ilike', 'ethnos'), ('scout_bank_clabe', '=', False)])
    companies.write({
        'scout_bank_name': 'Banorte',
        'scout_bank_clabe': '072650012261397106',
        'scout_bank_holder': 'Jhuliana Delgado',
        'scout_bank_phone': '2216209805',
        'scout_bank_concept': 'Nombre del niño y sección',
    })
