# -*- coding: utf-8 -*-
{
    "name": "Gestión de Aseguradoras",
    "version": "19.0.4.8.1",
    "author": "Jhuliana Delgado",
    "maintainer": "Jhuliana Delgado",
    "website": "",
    "category": "Services/Insurance",
    "summary": (
        "Seguros México: tablero, ofertas, pólizas, cobranza CFDI 4.0, "
        "renovación, siniestros y expediente en el contacto."
    ),
    "description": """
Gestión de Aseguradoras
=======================

Tablero por sucursal, ofertas, venta de pólizas (individual o familiar),
cobranza y CFDI 4.0, renovación anual, siniestros con consumo de cobertura
y expediente documental en el contacto.

Ver static/description/index.html para la ficha completa.
    """,
    "depends": [
        "base",
        "mail",
        "contacts",
        "crm",
        "calendar",
        "project",
        "account",
        "product",
        "sale",
    ],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "data/product_data.xml",
        "data/insurance_data.xml",
        "data/mail_activity_data.xml",
        "data/mail_template_data.xml",
        "data/cron_data.xml",
        "report/insurance_policy_report.xml",
        "report/insurance_policy_templates.xml",
        "views/insurance_company_views.xml",
        "views/insurance_branch_views.xml",
        "views/insurance_policy_type_views.xml",
        "views/insurance_scheme_views.xml",
        "views/insurance_agent_views.xml",
        "views/insurance_document_views.xml",
        "views/insurance_policy_views.xml",
        "views/insurance_claim_views.xml",
        "views/insurance_installment_views.xml",
        "views/account_move_views.xml",
        "views/insurance_dashboard_views.xml",
        "views/res_partner_views.xml",
        "views/crm_lead_views.xml",
        "views/menus.xml",
    ],
    "demo": [
        "data/insurance_demo.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "insurance_management/static/src/scss/dashboard.scss",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
    "license": "LGPL-3",
    "post_init_hook": "post_init_hook",
}
