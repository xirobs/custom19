# -*- coding: utf-8 -*-

def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.crm_extended_3D.hooks import _sync_sheet_defaults

    _sync_sheet_defaults(env)
    leads = env["crm.lead"].search([
        "|", "|", "|",
        ("filament_grams", ">", 0),
        ("print_hours", ">", 0),
        ("print_time_minutes", ">", 0),
        ("mo_operation_hours", ">", 0),
    ])
    leads._apply_cost_config_masters()
