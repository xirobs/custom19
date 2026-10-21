# -*- coding: utf-8 -*-
"""19.0.7.0.0: las pólizas que ya existían se consideran entregadas; las de borrador quedan como solicitud."""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE insurance_policy
           SET emission_stage = CASE WHEN state = 'draft' THEN 'request' ELSE 'delivered' END
         WHERE emission_stage IS NULL OR emission_stage = 'request'
    """)
