# -*- coding: utf-8 -*-


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.crm_extended_3D.hooks import _dedupe_duplicate_stages

    _dedupe_duplicate_stages(env)
