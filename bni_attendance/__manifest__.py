# -*- coding: utf-8 -*-
{
    'name': 'BNI - Control de Asistencia',
    'version': '19.0.2.0.0',
    'category': 'Productivity',
    'summary': 'Control de asistencia BNI presencial y virtual: QR dinámico, retardos, '
               'sustitutos, invitados, altas/bajas de miembros y % de participación',
    'description': """
Control de asistencia BNI
=========================
* Eventos configurables: nombre, fecha y hora de llegada, modalidad (presencial / virtual).
* Registro por QR dinámico en pantalla (cambia cada minuto) o QR fijo impreso, y registro manual.
* Retardo automático cuando la llegada supera la hora de llegada; el registro sigue abierto hasta cerrar el evento.
* Sustitutos (a quién sustituyen) e invitados (nombre, empresa y quién los invitó).
* Altas, bajas y reingresos de miembros con su historial.
* Observaciones capturadas por el propio miembro y por el equipo.
* % de participación y % de puntualidad por miembro.
* Reportes PDF por evento y por periodo (mes), análisis pivote/gráfico.
""",
    'author': 'Jhuliana',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'web'],
    'external_dependencies': {'python': ['qrcode']},
    'data': [
        'security/bni_security.xml',
        'security/ir.model.access.csv',
        'views/bni_member_views.xml',
        'views/bni_event_views.xml',
        'views/bni_attendance_views.xml',
        'wizard/bni_attendance_report_wizard_views.xml',
        'wizard/bni_member_status_wizard_views.xml',
        'views/checkin_templates.xml',
        'report/bni_reports.xml',
        'report/report_event.xml',
        'report/report_event_qr.xml',
        'report/report_monthly.xml',
        'views/bni_menus.xml',
    ],
    'installable': True,
    'application': True,
}
