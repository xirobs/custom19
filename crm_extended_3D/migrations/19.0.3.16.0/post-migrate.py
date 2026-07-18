# -*- coding: utf-8 -*-

def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid in ("crm.lost_reason_1", "crm.lost_reason_2", "crm.lost_reason_3"):
        reason = env.ref(xmlid, raise_if_not_found=False)
        if reason:
            reason.active = False
