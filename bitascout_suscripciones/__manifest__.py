# -*- coding: utf-8 -*-
{
    'name': 'Suscripciones BitaScout',
    'version': '19.0.1.0.0',
    'category': 'Services/Scouts',
    'summary': 'Acceso por suscripción pagada a BitaScout y Red Seguro Scout: '
               'solo quien tiene su suscripción vigente conserva los permisos de cada plataforma',
    'description': """
Suscripciones BitaScout
=======================
* Plataformas (BitaScout, Red Seguro Scout) con los grupos de permisos que controla cada una.
* Suscripciones por usuario y plataforma con vigencia (mensual, anual o personalizada) y registro del pago.
* Un usuario puede tener una, otra o ambas plataformas; cada una se cobra y vence por separado.
* Al activar o vencer una suscripción se otorgan o retiran automáticamente los permisos.
* Revisión al iniciar sesión, cron diario y aviso por correo antes del vencimiento.
* Bloquea asignar a mano permisos de una plataforma a quien no tiene suscripción vigente.
* Usuarios exentos (soporte, administración) y suscripciones de cortesía para arrancar sin cortar a nadie.
    """,
    'author': 'Jhuliana Delgado · BitaScout',
    'license': 'LGPL-3',
    'depends': ['base', 'mail'],
    'data': [
        'security/bita_security.xml',
        'security/ir.model.access.csv',
        'data/bita_data.xml',
        'views/bita_platform_views.xml',
        'views/bita_subscription_views.xml',
        'views/res_users_views.xml',
        'views/bita_menus.xml',
    ],
    'application': True,
    'installable': True,
}
