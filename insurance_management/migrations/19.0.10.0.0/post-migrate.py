# -*- coding: utf-8 -*-
"""19.0.10.0.0 «Vive Seguro»: seguimiento anual por ramo (renovación GMM / aniversario vida) y bloques nuevos del portal."""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.insurance_management.hooks import enable_followup_blocks, set_followup_kinds
    set_followup_kinds(env)
    enable_followup_blocks(env)
