# -*- coding: utf-8 -*-

def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    configs = env["print.cost.config"].search([], order="id")
    if len(configs) > 1:
        configs[1:].unlink()
