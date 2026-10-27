# -*- coding: utf-8 -*-
{
    'name': 'BitaScout · Suscripción',
    'version': '19.0.1.0.0',
    'category': 'Services/Scouts',
    'summary': 'Conecta Gestión de Grupo Scout con Suscripciones BitaScout: '
               '3 meses de prueba y después acceso solo con suscripción pagada',
    'description': """
BitaScout · Suscripción
=======================
* Registra BitaScout como plataforma por suscripción (roles: jefe de sección, secretaría,
  tesorería y jefe de grupo).
* 3 meses de prueba la primera vez que una persona recibe un rol o entra al portal de dirigentes.
* El portal de dirigentes (/dirigentes) también respeta la suscripción y avisa cuando venció.
* No modifica el módulo scout_management; se instala solo cuando ambos módulos están presentes.
    """,
    'author': 'Jhuliana Delgado · BitaScout',
    'license': 'LGPL-3',
    'depends': ['scout_management', 'bitascout_suscripciones'],
    'data': [
        'data/bita_platform_data.xml',
        'views/leader_portal_templates.xml',
    ],
    'auto_install': True,
    'installable': True,
}
