# -*- coding: utf-8 -*-
"""19.0.8.0.0: identidad de la agencia (logo y colores) en la empresa y encabezado de marca en los correos."""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.insurance_management.hooks import apply_brand
    apply_brand(env)
