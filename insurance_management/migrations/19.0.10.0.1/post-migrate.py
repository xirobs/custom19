# -*- coding: utf-8 -*-
"""19.0.10.0.1: los prospectos creados desde el portal o asignados al equipo de seguros quedan marcados
como oportunidades de seguros (el portal ya solo muestra esas y no las de otros negocios)."""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE crm_lead
           SET is_insurance_opportunity = true
         WHERE COALESCE(is_insurance_opportunity, false) = false
           AND (team_user_id IS NOT NULL
                OR insurance_policy_type_id IS NOT NULL
                OR insurance_offer_id IS NOT NULL
                OR insurance_scheme_id IS NOT NULL
                OR insurance_policy_id IS NOT NULL)
    """)
