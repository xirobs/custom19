# -*- coding: utf-8 -*-
"""Cobranza por ramo y página pública: clasifica pólizas, calcula próximo pago y nombra la app."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Policy = env["insurance.policy"]
    policies = Policy.search([])
    env.add_to_compute(Policy._fields["coll_node_id"], policies)
    env.add_to_compute(Policy._fields["coll_next_date"], policies)
    policies._recompute_recordset(["coll_node_id", "coll_next_date"])
    env.add_to_compute(Policy._fields["coll_status"], policies)
    env.add_to_compute(Policy._fields["coll_rank"], policies)
    policies._recompute_recordset(["coll_status", "coll_rank"])
    icp = env["ir.config_parameter"].sudo()
    if not icp.get_param("web.web_app_name") or icp.get_param("web.web_app_name") == "Odoo":
        icp.set_param("web.web_app_name", "Vive Seguro")
