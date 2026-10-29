# -*- coding: utf-8 -*-
"""Utilidades compartidas por los modelos de Red Seguro Scout."""
import re
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import _
from odoo.exceptions import ValidationError

MONTHS_ES = [
    'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio',
    'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
]

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
CURP_RE = re.compile(r'^[A-Z][AEIOUX][A-Z]{2}\d{6}[HM][A-Z]{5}[A-Z\d]\d$')

SEX_SELECTION = [
    ('male', 'Masculino'),
    ('female', 'Femenino'),
]

SECTION_SELECTION = [
    ('manada', 'Manada (Lobatos)'),
    ('tropa', 'Tropa (Scouts)'),
    ('comunidad', 'Comunidad (Caminantes)'),
    ('clan', 'Clan (Rovers)'),
    ('dirigente', 'Dirigente / Adulto'),
]

INJURY_SELECTION = [
    ('fracture', 'Fractura'),
    ('bruise', 'Golpe / contusión'),
    ('cut', 'Corte / herida'),
    ('sprain', 'Esguince / luxación'),
    ('burn', 'Quemadura'),
    ('sting', 'Picadura / mordedura'),
    ('medical', 'Gastos médicos por accidente'),
    ('other', 'Otro'),
]


def month_label(day):
    """'Octubre 2026' a partir de una fecha."""
    if not day:
        return ''
    return '%s %s' % (MONTHS_ES[day.month - 1], day.year)


def month_bounds(day):
    """Primer y último día del mes de ``day``."""
    start = day.replace(day=1)
    end = start + relativedelta(months=1, days=-1)
    return start, end


def overlap_days(start, end, period_start, period_end):
    """Días (inclusivos) en que [start, end] se cruza con el periodo.

    ``end`` vacío significa cobertura abierta (sin baja).
    """
    if not start:
        return 0
    real_start = max(start, period_start)
    real_end = min(end or period_end, period_end)
    if real_end < real_start:
        return 0
    return (real_end - real_start).days + 1


def age_on(birthdate, on_day=None):
    if not birthdate:
        return 0
    on_day = on_day or date.today()
    return max(relativedelta(on_day, birthdate).years, 0)


def section_for_age(age):
    """Rama sugerida según la edad (esquema típico en México)."""
    if age < 11:
        return 'manada'
    if age < 15:
        return 'tropa'
    if age < 18:
        return 'comunidad'
    if age < 22:
        return 'clan'
    return 'dirigente'


def check_email(value, label):
    if value and not EMAIL_RE.match(value.strip()):
        raise ValidationError(_('El correo "%(email)s" de %(label)s no es válido.',
                                email=value, label=label))


def check_pdf_attachments(attachments, owner_label):
    """Solo se permiten archivos PDF en los expedientes."""
    for att in attachments:
        is_pdf = (att.mimetype == 'application/pdf'
                  or (att.name or '').lower().endswith('.pdf'))
        if not is_pdf:
            raise ValidationError(_(
                'Solo se permiten archivos PDF en %(owner)s. "%(file)s" no es un PDF.',
                owner=owner_label, file=att.name))


def normalize_text(value):
    """Mayúsculas, sin acentos ni signos y con espacios sencillos (para comparar nombres)."""
    import unicodedata
    if not value:
        return ''
    value = unicodedata.normalize('NFKD', str(value))
    value = ''.join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r'[^A-Za-z0-9Ññ@.\s]', ' ', value).upper()
    return ' '.join(value.split())


def name_key(first_name, second_name, last_name_father, last_name_mother, birthdate):
    """Llave para detectar duplicados: nombre completo normalizado + fecha de nacimiento."""
    parts = [first_name, second_name, last_name_father, last_name_mother]
    full = normalize_text(' '.join(p for p in parts if p))
    return '%s|%s' % (full, birthdate.isoformat() if birthdate else '')
