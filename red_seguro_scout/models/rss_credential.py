# -*- coding: utf-8 -*-
"""Credencial del seguro (frente y reverso), con o sin foto."""
import base64
import secrets

from markupsafe import Markup

from odoo import _, api, fields, models

from .rss_common import section_for_age
from odoo.tools.image import image_data_uri

_STATIC_CACHE = {}


def static_data_uri(path):
    """Imagen del módulo como data URI (el PDF no depende de que el servidor sirva /static)."""
    if path not in _STATIC_CACHE:
        from odoo.tools.misc import file_open
        with file_open('red_seguro_scout/' + path, 'rb') as f:
            _STATIC_CACHE[path] = 'data:image/png;base64,%s' % base64.b64encode(f.read()).decode()
    return _STATIC_CACHE[path]


def font_face_css():
    """@font-face de Poppins incrustada (la credencial se ve igual en pantalla y en PDF)."""
    if 'fonts' not in _STATIC_CACHE:
        from odoo.tools.misc import file_open
        css = []
        for weight, name in ((400, 'Regular'), (500, 'Medium'), (700, 'Bold')):
            with file_open('red_seguro_scout/static/src/fonts/Poppins-%s.ttf' % name, 'rb') as f:
                data = base64.b64encode(f.read()).decode()
            css.append("@font-face { font-family: 'RSS Poppins'; font-weight: %s; font-style: normal; "
                       "src: url(data:font/truetype;charset=utf-8;base64,%s) format('truetype'); }" % (weight, data))
        _STATIC_CACHE['fonts'] = Markup('\n'.join(css))
    return _STATIC_CACHE['fonts']


def _nice(text):
    """JULIETA ARRIAGA -> Julieta Arriaga (respeta «de», «la», «del»)."""
    if not text:
        return ''
    small = {'de', 'del', 'la', 'las', 'los', 'y', 'e'}
    words = text.split()
    return ' '.join(w.lower() if (i and w.lower() in small) else w[:1].upper() + w[1:].lower()
                    for i, w in enumerate(words))


MONTHS_SHORT = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']
RELATION = {'father': 'Padre', 'mother': 'Madre', 'tutor': 'Tutor(a)', 'other': 'Contacto'}


def _d(value):
    return value.strftime('%d/%m/%Y') if value else ''


CYCLE_PARAM = 'red_seguro_scout.credential_cycle_start'


def credential_cycle(env, ref=None):
    """Vigencia de la credencial: ciclo scout del 19 de agosto al 19 de agosto del año siguiente
    (el que contiene la fecha de referencia). El día de inicio se puede cambiar con el parámetro
    del sistema red_seguro_scout.credential_cycle_start (MM-DD)."""
    from datetime import date
    ref = ref or fields.Date.context_today(env['rss.insured'])
    try:
        month, day = (int(x) for x in (env['ir.config_parameter'].sudo().get_param(CYCLE_PARAM) or '08-19').split('-'))
        date(2000, month, day)
    except ValueError:
        month, day = 8, 19
    year = ref.year if (ref.month, ref.day) >= (month, day) else ref.year - 1
    return date(year, month, day), date(year + 1, month, day)


class RssInsuredCredential(models.Model):
    _inherit = 'rss.insured'

    photo = fields.Image('Foto', max_width=600, max_height=600,
                         help='Opcional. Si no hay foto, la credencial usa otro acomodo.')
    credential_token = fields.Char('Código de la credencial', copy=False, readonly=True, index=True)

    def _credential_ensure_token(self):
        for rec in self.sudo().filtered(lambda r: not r.credential_token):
            rec.credential_token = secrets.token_urlsafe(12)

    def _credential_url(self):
        self.ensure_one()
        self._credential_ensure_token()
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        return '%s/credencial/%s' % (base, self.sudo().credential_token)

    def _credential_values(self):
        """Todo lo que imprime la credencial, ya con respaldo cuando falta un dato."""
        self.ensure_one()
        rec = self.sudo()
        policy = rec.group_id.policy_id
        coverage = rec.coverage_ids[:1] if 'coverage_ids' in rec._fields else False
        if coverage:
            main_cov = '%s: %s' % (coverage.name, coverage.sum_insured_text or '$ {:,.2f}'.format(coverage.sum_insured))
        else:
            main_cov = policy.coverage_type or _('Accidentes personales (según condiciones de la póliza de la Red de Scouts).')
        start, end = credential_cycle(self.env)
        if rec.tutor_name or rec.tutor_phone:
            em_name = rec.tutor_name or _('Padre, madre o tutor')
            em_role = RELATION.get(rec.tutor_relationship) or _('Tutor(a)')
            em_phone = rec.tutor_phone or ''
        else:
            em_name = rec.group_id.leader_name or (rec.group_id.name or '').split('–')[0].strip()
            em_role = _('Jefe de grupo')
            em_phone = rec.group_id.leader_phone or ''
        qr_png = self._credential_qr_png(rec._credential_url())
        second = rec.second_name if (rec.second_name or '').strip().lower() != (rec.first_name or '').strip().lower() else ''
        first = _nice(' '.join(x for x in (rec.first_name, second) if x))
        last = _nice(' '.join(x for x in (rec.last_name_father, rec.last_name_mother) if x))
        return {
            'first': first, 'last': last,
            'initials': ''.join(x[0] for x in (rec.first_name, rec.last_name_father) if x).upper(),
            'badge': _('Jefatura / Adulto') if rec.section == 'dirigente' else _('Miembro Scout'),
            'section': (dict(rec._fields['section']._description_selection(self.env)).get(
                rec.section or (section_for_age(rec.age) if rec.birthdate else False)) or '').split(' (')[0],
            'group': rec.group_id.name.split('–')[0].strip() if rec.group_id else '',
            'city': (rec.group_id.seat_label or rec.group_id.city or '') if rec.group_id else '',
            'group_logo': image_data_uri(rec.group_id.logo) if rec.group_id.logo else False,
            'photo': image_data_uri(rec.photo) if rec.photo else False,
            'start': _d(start), 'end': _d(end),
            'active': rec.state == 'active',
            'state_label': dict(rec._fields['state']._description_selection(self.env)).get(rec.state, ''),
            'product': rec.cert_product or policy.name or _('Accidentes Personales Scout'),
            'policy_number': rec.cert_policy_number or policy.policy_number or '—',
            'insurer': rec.cert_insurer or policy.insurer_id.name or '—',
            'certificate': rec.certificate_number or '',
            'coverage': main_cov,
            'em_name': _nice(em_name) if em_name and em_name.isupper() else em_name,
            'em_role': em_role, 'em_phone': em_phone, 'fonts': font_face_css(),
            'qr': 'data:image/png;base64,%s' % base64.b64encode(qr_png).decode(),
            'logo': static_data_uri('static/description/red_seguro_scout_logo.png'),
            'emblem': static_data_uri('static/src/img/emblema_red_seguro_scout.png'),
        }

    def _credential_qr_png(self, value):
        try:
            import io
            import qrcode
            qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=1)
            qr.add_data(value)
            qr.make(fit=True)
            out = io.BytesIO()
            qr.make_image(fill_color='#1E2B3A', back_color='white').save(out, format='PNG')
            return out.getvalue()
        except ImportError:
            return self.env['ir.actions.report'].barcode('QR', value, width=320, height=320)

    @api.model
    def _credential_fonts(self):
        return font_face_css()

    def action_print_credential(self):
        self._credential_ensure_token()
        return self.env.ref('red_seguro_scout.action_report_rss_credential').report_action(self)
