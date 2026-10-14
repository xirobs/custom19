# -*- coding: utf-8 -*-
"""Constantes y utilerías compartidas del módulo scout."""
from dateutil.relativedelta import relativedelta

BLOOD_TYPES = [
    ('o+', 'O Rh+'), ('o-', 'O Rh-'), ('a+', 'A Rh+'), ('a-', 'A Rh-'),
    ('b+', 'B Rh+'), ('b-', 'B Rh-'), ('ab+', 'AB Rh+'), ('ab-', 'AB Rh-'),
    ('unknown', 'Desconocido'),
]
MEDICAL_SERVICES = [
    ('imss', 'IMSS'), ('issste', 'ISSSTE'), ('private', 'Seguro particular'), ('none', 'No tengo'),
]
SHIRT_SIZES = [
    ('4', 'Infantil 4'), ('6', 'Infantil 6'), ('8', 'Infantil 8'), ('10', 'Infantil 10'),
    ('12', 'Infantil 12'), ('14', 'Infantil 14'), ('16', 'Infantil 16'),
    ('xs', 'XS'), ('s', 'CH'), ('m', 'M'), ('l', 'G'), ('xl', 'XG'), ('xxl', 'XXG'),
]
PATROL_ROLES = [
    ('leader', 'Guía (madera / seisenero)'), ('sub', 'Subguía (submadera / subseisenero)'),
    ('member', 'Integrante'),
]
LEADER_ROLES = [
    ('group_chief', 'Jefe de grupo'), ('chief', 'Jefe de sección'), ('assistant', 'Subjefe'),
    ('candidate', 'Candidato a subjefe'), ('secretary', 'Secretaría'), ('treasurer', 'Tesorería'),
    ('support', 'Apoyo / Comité'),
]

# Leyenda del formato de asistencia del grupo.
ATTENDANCE_STATUS = [
    ('present', 'SI - Asistió'),
    ('absent', 'NO - No asistió'),
    ('camp', 'CA - Campamento'),
    ('outing', 'M - Museo / salida'),
    ('dropped', 'B - Baja'),
    ('na', 'N/A - No aplica'),
    ('no_roll', '/ - No se tomó lista'),
]
STATUS_CODES = {
    'present': 'SI', 'absent': 'NO', 'camp': 'CA', 'outing': 'M',
    'dropped': 'B', 'na': 'N/A', 'no_roll': '/',
}
ATTENDED_STATUSES = ('present', 'camp', 'outing')
COUNTED_STATUSES = ('present', 'absent', 'camp', 'outing')

ACTIVITY_TYPES = [
    ('regular', 'Sesión sabatina'),
    ('camp', 'Campamento'),
    ('outing', 'Salida / excursión'),
    ('special', 'Actividad especial'),
    ('council', 'Consejo de grupo / junta'),
    ('group', 'Evento de grupo'),
    ('training', 'Formación de dirigentes'),
    ('none', 'Sin actividades'),
]
# Estado de asistencia que se propone al cargar la lista según el tipo de actividad.
DEFAULT_STATUS_BY_TYPE = {'camp': 'camp', 'outing': 'outing'}

MONTHS = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
          'septiembre', 'octubre', 'noviembre', 'diciembre']


def quarter_bounds(day):
    start = day.replace(month=3 * ((day.month - 1) // 3) + 1, day=1)
    return start, start + relativedelta(months=3, days=-1)


def quarter_label(date_from, date_to):
    if not date_from or not date_to:
        return ''
    if date_from.year == date_to.year:
        return f'{MONTHS[date_from.month - 1].capitalize()} - {MONTHS[date_to.month - 1].capitalize()} {date_to.year}'
    return (f'{MONTHS[date_from.month - 1].capitalize()} {date_from.year} - '
            f'{MONTHS[date_to.month - 1].capitalize()} {date_to.year}')


def join_names(names):
    names = [n for n in names if n]
    if len(names) <= 1:
        return ''.join(names)
    return ', '.join(names[:-1]) + ' y ' + names[-1]


def short_name(partner):
    """'Leonardo (TIMY)' a partir del contacto del miembro."""
    first = (partner.scout_first_name or partner.name or '').split(' ')[0]
    return f'{first} ({partner.scout_name})' if partner.scout_name else first
