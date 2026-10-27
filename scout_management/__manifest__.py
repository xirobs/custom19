# -*- coding: utf-8 -*-
{
    'name': 'Gestión de Grupo Scout',
    'version': '19.0.4.18.0',
    'summary': 'Grupo scout sobre Contactos, Calendario, Contabilidad y Conversaciones: miembros, actividades, asistencia, adelantos, actas, tesorería y portal para padres',
    'description': """
Gestión de Grupo Scout
======================
* Miembros como Contactos: expediente con padres, emergencia, ficha médica, sección, adelantos, registro y adeudos.
* Actividades en el Calendario (grupo o sección): responsables, temática, objetivos, horario, asistencia y "realizada".
* Tesorería sobre Contabilidad: caja y bancos, cargos a miembros (CxC), facturas de proveedor (CxP) y balance para el Consejo.
* Actas de consejo y juntas con acuerdos.
* Aprobaciones del jefe de grupo: gastos y pagos, actas, fichas, bajas y cambios de sección.
* Informe trimestral por sección y PDFs con la estructura del grupo.
* Portal para padres: información de su hijo, ficha en línea, pago en línea y chat con los dirigentes.
* Portal de dirigentes (celular): pase de lista, control de asistencia, planificación trimestral y anual, adelantos, informes trimestral y anual y mensajes de su sección.
* Patrullas / seisenas / madrigueras como fichas (mover y cambiar guía), cuadro de logros con termómetro, especialidades por categoría y ruta de enlace.
* Nuevos ingresos con foto, firma del tutor y liga de pago; aviso al llegar a 2 asistencias sin registro; credencial electrónica con QR; avisos por edad.
* Control de rutas de enlace y permanencia: contador de tiempo restante, alertas amarilla y roja, excepciones de hasta 3 meses, avisos a jefes de origen y destino, partida del Clan e historial.
* Programa semanal de cada sesión con el formato del grupo (Programa y Método Scout), dictado por voz con intérprete por reglas, PDF y Excel.
* Programa con visto bueno: el consejo revisa, el jefe de grupo autoriza salidas; calendario general por colores (rojo grupo, amarillo salida, verde ordinaria).
* Cobro en línea con Mercado Pago: botón Pagar, QR y ligas por WhatsApp o correo.
* Tienda scout: inventario, ventas (tienda, alimentos, eventos), recibos PDF por correo/WhatsApp y parte de ventas.
* Carga masiva de miembros desde Excel.

Ver static/description/index.html para la ficha completa y doc/ para el manual de uso.
""",
    'author': 'Jhuliana Delgado',
    'maintainer': 'Jhuliana Delgado',
    'category': 'Services/Scouts',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'portal', 'contacts', 'calendar', 'account', 'product',
                'account_payment', 'payment_mercado_pago'],
    'data': [
        'security/scout_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/scout_data.xml',
        'data/scout_progression_data.xml',
        'data/scout_progression_extra_data.xml',
        'data/scout_branding.xml',
        'data/scout_online_payment.xml',
        'data/scout_route_cron.xml',
        'data/scout_resource_data.xml',
        'data/scout_online_fee.xml',
        'data/scout_promo_data.xml',
        'report/scout_report_actions.xml',
        'report/report_registration.xml',
        'report/report_attendance.xml',
        'report/report_treasury.xml',
        'report/report_program.xml',
        'report/report_section_report.xml',
        'report/report_member_meeting.xml',
        'report/report_store.xml',
        'report/report_member_card.xml',
        'report/report_session_plan.xml',
        'views/scout_section_views.xml',
        'views/res_partner_views.xml',
        'views/scout_registration_views.xml',
        'views/calendar_event_views.xml',
        'views/scout_progression_views.xml',
        'views/scout_treasury_views.xml',
        'views/scout_meeting_views.xml',
        'views/scout_store_views.xml',
        'views/scout_route_views.xml',
        'views/scout_resource_views.xml',
        'wizard/scout_event_generate_views.xml',
        'wizard/scout_attendance_report_wizard_views.xml',
        'wizard/scout_program_report_wizard_views.xml',
        'wizard/scout_calendar_report_wizard_views.xml',
        'wizard/scout_treasury_report_wizard_views.xml',
        'wizard/scout_member_import_views.xml',
        'wizard/scout_product_import_views.xml',
        'views/scout_menus.xml',
        'views/scout_promo_views.xml',
        'views/portal_templates.xml',
        'views/portal_classroom_templates.xml',
        'views/leader_portal_templates.xml',
        'views/scout_promo_templates.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'scout_management/static/src/scss/scout_backend.scss',
            'scout_management/static/src/js/scout_calendar.js',
        ],
        'web.assets_frontend': [
            'scout_management/static/src/js/scout_signature.js',
            'scout_management/static/src/js/scout_leader.js',
            'scout_management/static/src/js/scout_plan_voice.js',
            'scout_management/static/src/js/scout_app_install.js',
            'scout_management/static/src/scss/scout_portal.scss',
            'scout_management/static/src/scss/scout_promo.scss',
        ],
    },
    'images': ['static/description/1.png'],
    'post_init_hook': 'post_init_hook',
    'application': True,
    'installable': True,
}
