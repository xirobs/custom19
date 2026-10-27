# -*- coding: utf-8 -*-
{
    'name': 'Red Seguro Scout',
    'version': '19.0.1.0.1',
    'category': 'Services',
    'summary': 'Rumbo Seguro · Altas y control del seguro de accidentes personales de grupos scouts, '
               'con la identidad de cada grupo',
    'description': """
Red Seguro Scout · «Rumbo Seguro»
=================================
Administra el seguro de accidentes personales (fracturas, golpes, cortes, etc.)
que cubre a los miembros de los grupos scouts durante sus actividades.

* Registro de asegurados (nombres, apellidos, edad, sexo, grupo, tutor).
* Grupos y agrupaciones (distrito / provincia) con expediente PDF.
* Relación mensual automática (días 1 al 10) en Excel y PDF para la aseguradora.
* Pagos del seguro por grupo con recibo imprimible y seguimiento del proceso.
* Siniestros (accidentes) por asegurado.
* Análisis en lista, pivote y gráfica: por día, mes, trimestre y año; comparativa de grupos.
* Acceso solo para quien paga el seguro (Suscripciones BitaScout).
* Personalidad del grupo: logo, colores y lema propios en recibos, relaciones y Excel.
    """,
    'author': 'Jhuliana · BitaScout / Red Seguro Scout',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'bitascout_suscripciones'],
    'data': [
        'security/rss_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/rss_policy_data.xml',
        'data/mail_template_data.xml',
        'data/ir_cron_data.xml',
        'data/bita_platform_data.xml',
        'report/report_paperformat.xml',
        'report/report_relacion_mensual.xml',
        'report/report_recibo_pago.xml',
        'wizard/rss_attach_pdf_wizard_views.xml',
        'views/rss_policy_views.xml',
        'views/rss_group_cluster_views.xml',
        'views/rss_scout_group_views.xml',
        'views/rss_insured_views.xml',
        'views/rss_payment_views.xml',
        'views/rss_report_views.xml',
        'views/rss_claim_views.xml',
        'views/rss_menus.xml',
    ],
    'demo': [
        'demo/rss_demo.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'red_seguro_scout/static/src/scss/red_seguro_scout.scss',
        ],
        'web.assets_web_dark': [
            'red_seguro_scout/static/src/scss/red_seguro_scout.dark.scss',
        ],
    },
    'application': True,
    'installable': True,
}
