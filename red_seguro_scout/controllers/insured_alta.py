# -*- coding: utf-8 -*-
"""Alta de asegurados uno por uno desde los portales (igual que «Nuevo» en la plataforma).

* Grupo (/mi-grupo/alta): solo el capturista. La persona entra a la carga a mano del día
  (una sola carga para todas las altas del día) y se paga el mismo día, como cualquier carga.
* Asesor (/seguro/alta): para cualquiera de sus grupos; queda en preinscripción, igual que
  al darla de alta en la plataforma.
"""
from datetime import date

from odoo import _, fields, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

from .advisor_portal import RssAdvisorPortal
from .group_portal import RssGroupPortal

FIELDS = ('first_name', 'second_name', 'last_name_father', 'last_name_mother', 'birthdate', 'sex',
          'curp', 'tutor_name', 'tutor_relationship', 'tutor_phone', 'tutor_email')


def _clean_form(post):
    form = {k: (post.get(k) or '').strip() for k in FIELDS + ('group_id',)}
    vals = {k: form[k] or False for k in FIELDS}
    try:
        vals['birthdate'] = date.fromisoformat(form['birthdate']) if form['birthdate'] else False
    except ValueError:
        vals['birthdate'] = False
    if vals['sex'] not in ('male', 'female'):
        vals['sex'] = False
    if vals['tutor_relationship'] not in ('father', 'mother', 'tutor', 'other'):
        vals['tutor_relationship'] = False
    if vals['curp']:
        vals['curp'] = vals['curp'].upper()
    return form, vals


def _missing(vals):
    labels = {'first_name': _('primer nombre'), 'last_name_father': _('apellido paterno'),
              'birthdate': _('fecha de nacimiento'), 'sex': _('sexo'), 'tutor_name': _('nombre del tutor'),
              'tutor_phone': _('teléfono de emergencia')}
    missing = [label for key, label in labels.items() if not vals.get(key)]
    if vals.get('birthdate') and vals['birthdate'] > date.today():
        missing.append(_('fecha de nacimiento (no puede ser futura)'))
    return missing


def _full_name(vals):
    return ' '.join(v for v in (vals.get('first_name'), vals.get('second_name'),
                                vals.get('last_name_father'), vals.get('last_name_mother')) if v)


class RssGroupAlta(RssGroupPortal):

    def _alta_values(self, form=None):
        return {'action': '/mi-grupo/alta', 'alta_groups': False, 'form': form or {},
                'back_url': '/mi-grupo/asegurados',
                'alta_note': _('Se agrega a la captura de hoy de tu grupo. Al terminar, revisa la lista y paga '
                               'el mismo día; al confirmarse el pago queda asegurado.')}

    @http.route(['/mi-grupo/alta'], type='http', auth='user', website=True, methods=['GET'], sitemap=False)
    def group_alta_form(self, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        if not member.can_capture:
            return self._back('/mi-grupo/asegurados',
                              error=_('Solo el capturista de tu grupo puede dar de alta asegurados.'))
        return self._render('red_seguro_scout.group_insured_alta', member, 'insured', **self._alta_values())

    @http.route(['/mi-grupo/alta'], type='http', auth='user', website=True, methods=['POST'], sitemap=False)
    def group_alta_save(self, **post):
        member, denied = self._guard()
        if denied:
            return denied
        if not member.can_capture:
            return self._back('/mi-grupo/asegurados',
                              error=_('Solo el capturista de tu grupo puede dar de alta asegurados.'))
        form, vals = _clean_form(post)

        def again(msg):
            values = self._alta_values(form)
            values['error'] = msg
            return self._render('red_seguro_scout.group_insured_alta', member, 'insured', **values)

        is_open, msg = self._capture_window(member)
        if not is_open:
            return again(msg)
        if not post.get('privacy'):
            return again(_('Debes aceptar el aviso de privacidad para continuar.'))
        missing = _missing(vals)
        if missing:
            return again(_('Falta: %s.', ', '.join(missing)))
        Batch = request.env['rss.import.batch'].sudo()
        group = member.group_id.sudo()
        today_start = fields.Datetime.to_datetime(fields.Date.context_today(Batch))
        batch = Batch.search([('group_id', '=', group.id), ('member_id', '=', member.id), ('source', '=', 'manual'),
                              ('state', '=', 'review'), ('create_date', '>=', today_start)],
                             order='id desc', limit=1)
        if not batch:
            batch = Batch.create({'group_id': group.id, 'member_id': member.id, 'source': 'manual',
                                  'privacy_accepted': True})
        # Quita renglones vacíos de la tabla a mano
        batch.line_ids.filtered(lambda l: l._is_blank()).unlink()
        seq = max(batch.line_ids.mapped('sequence') or [0]) + 1
        line = request.env['rss.import.line'].sudo().create(dict(vals, batch_id=batch.id, sequence=seq))
        batch.action_validate()
        if line.status == 'error':
            problem = line.message or _('revisa los datos')
            line.unlink()
            return again(_('No se pudo agregar: %s.', problem))
        name = _full_name(vals)
        if line.status == 'duplicate':
            note = _('%(name)s se agregó, pero parece repetida: %(msg)s. Revísalo antes de pagar.',
                     name=name, msg=line.message or '')
            return self._back('/mi-grupo/importar/%s' % batch.id, error=note)
        if post.get('next') == 'another':
            return self._back('/mi-grupo/alta', ok=_('%(name)s quedó en la captura de hoy (%(n)s personas). '
                                                     'Agrega a la siguiente.', name=name, n=len(batch.line_ids)))
        return self._back('/mi-grupo/importar/%s' % batch.id,
                          ok=_('%s quedó en la captura de hoy. Revisa la lista y paga para que quede asegurado.', name))


class RssAdvisorAlta(RssAdvisorPortal):

    def _alta_values(self, advisor, form=None):
        return {'action': '/seguro/alta', 'alta_groups': self._groups(advisor), 'form': form or {},
                'back_url': '/seguro/asegurados',
                'alta_note': _('Queda en preinscripción en el grupo que elijas, igual que al darlo de alta en la '
                               'plataforma. Pasa a «Asegurado» cuando se registra su pago.')}

    @http.route(['/seguro/alta'], type='http', auth='user', website=True, methods=['GET'], sitemap=False)
    def advisor_alta_form(self, **kw):
        advisor, denied = self._guard()
        if denied:
            return denied
        if not advisor.can_register_insured:
            return self._back('/seguro/asegurados', error=_('No tienes permiso para dar de alta asegurados.'))
        form = {'group_id': kw.get('grupo') or ''}
        return self._render('red_seguro_scout.advisor_insured_alta', advisor, 'insured',
                            **self._alta_values(advisor, form))

    @http.route(['/seguro/alta'], type='http', auth='user', website=True, methods=['POST'], sitemap=False)
    def advisor_alta_save(self, **post):
        advisor, denied = self._guard()
        if denied:
            return denied
        if not advisor.can_register_insured:
            return self._back('/seguro/asegurados', error=_('No tienes permiso para dar de alta asegurados.'))
        form, vals = _clean_form(post)

        def again(msg):
            values = self._alta_values(advisor, form)
            values['error'] = msg
            return self._render('red_seguro_scout.advisor_insured_alta', advisor, 'insured', **values)

        group = self._groups(advisor).filtered(lambda g: str(g.id) == form['group_id'])
        if not group:
            return again(_('Elige uno de tus grupos.'))
        if not post.get('privacy'):
            return again(_('Debes aceptar el aviso de privacidad para continuar.'))
        missing = _missing(vals)
        if missing:
            return again(_('Falta: %s.', ', '.join(missing)))
        Insured = request.env['rss.insured'].sudo()
        dup = Insured._find_duplicates(vals['first_name'], vals['second_name'], vals['last_name_father'],
                                       vals['last_name_mother'], vals['birthdate'], company=group.company_id)
        if dup:
            return again(_('%(name)s ya está registrado en %(group)s.', name=dup[0].name, group=dup[0].group_id.name))
        try:
            insured = Insured.create(dict(vals, group_id=group.id, state='draft', capture_source='portal',
                                          captured_by_id=request.env.user.id))
        except (UserError, ValidationError) as exc:
            return again(str(exc))
        insured.message_post(body=_('Alta desde el portal del asesor por %s.', advisor.name))
        if post.get('next') == 'another':
            return self._back('/seguro/alta?grupo=%s' % group.id,
                              ok=_('%s quedó en preinscripción. Agrega al siguiente.', insured.name))
        return self._back('/seguro/asegurados?grupo=%s&estatus=all' % group.id,
                          ok=_('%s quedó en preinscripción en %s.', insured.name, group.name))
