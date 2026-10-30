# -*- coding: utf-8 -*-
{
    'name': 'Red Seguro Scout',
    'version': '19.0.1.5.0',
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
* Portal del grupo (/mi-grupo): jefe de grupo, secretario y tesorero; carga de asegurados por Excel o PDF con revisión y duplicados.
* Certificados de póliza: se separa el PDF de la aseguradora y cada asegurado recibe el suyo.
* Aviso de privacidad siempre visible.
* Pago obligatorio el mismo día de la captura: Mercado Pago (con su comisión) o transferencia con comprobante.
* Tarifas por trimestre de alta.
* Portal del asesor de seguros (usuario de portal, sin licencia): asegurados, relaciones, pagos y siniestros.
* Acceso solo para quien paga el seguro (Suscripciones BitaScout).
* Personalidad del grupo: logo, colores y lema propios en recibos, relaciones y Excel.
    """,
    'author': 'Jhuliana · BitaScout / Red Seguro Scout',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'portal', 'payment', 'bitascout_suscripciones'],
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
        'views/rss_advisor_views.xml',
        'views/rss_portal_admin_views.xml',
        'views/advisor_portal_templates.xml',
        'views/group_portal_templates.xml',
        'views/rss_app_templates.xml',
        'views/certificate_portal_templates.xml',
        'views/rss_menus.xml',
    ],
    'demo': [
        'demo/rss_demo.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'red_seguro_scout/static/src/scss/red_seguro_scout.scss',
        ],
        'web.assets_frontend': [
            'red_seguro_scout/static/src/scss/rss_advisor_portal.scss',
            'red_seguro_scout/static/src/js/rss_app_install.js',
        ],
        'web.assets_web_dark': [
            'red_seguro_scout/static/src/scss/red_seguro_scout.dark.scss',
        ],
    },
    'application': True,
    'installable': True,
}
