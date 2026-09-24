# -*- coding: utf-8 -*-


def mark_insurance_opportunities(env):
    """Marca oportunidades que ya tienen oferta o póliza."""
    leads = env["crm.lead"].sudo().search([
        ("is_insurance_opportunity", "=", False),
        "|",
        ("insurance_scheme_id", "!=", False),
        ("insurance_policy_id", "!=", False),
    ])
    if leads:
        leads.write({"is_insurance_opportunity": True})


def post_init_hook(env):
    mark_insurance_opportunities(env)
