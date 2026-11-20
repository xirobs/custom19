# -*- coding: utf-8 -*-
"""Páginas públicas de registro de asistencia BNI.

* /bni/e/<token>        -> página que abre el QR (miembros, sustitutos e invitados).
* /bni/screen/<id>      -> pantalla para proyectar el QR dinámico (solo usuarios BNI).

Con QR dinámico el link incluye un código que cambia cada minuto: una foto o un
reenvío del QR deja de servir a los pocos minutos, así solo registra quien está en la sesión.
Nunca se registra asistencia con un GET; siempre se pulsa un botón (POST + CSRF).
"""
import logging

from odoo import _, http
from odoo.exceptions import UserError
from odoo.http import request

from ..models.bni_tools import generate_qr_base64, normalize_phone

_logger = logging.getLogger(__name__)

COOKIE_NAME = 'bni_member'
COOKIE_MAX_AGE = 60 * 60 * 24 * 365
OPEN_PAGE_MINUTES = 3      # el QR escaneado sirve para abrir la página durante 3 min
SUBMIT_MINUTES = 10        # y para enviar el formulario durante 10 min

STATE_LABELS = {
    'on_time': 'A tiempo',
    'late': 'Retardo',
    'substitute': 'Sustituto',
    'excused': 'Justificada',
    'absent': 'Falta',
    'pending': 'Pendiente',
}


class BniCheckinController(http.Controller):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _get_event(self, token):
        if not token:
            return request.env['bni.event']
        return request.env['bni.event'].sudo().search([('access_token', '=', token)], limit=1)

    def _member_from_cookie(self, event):
        token = request.httprequest.cookies.get(COOKIE_NAME)
        Member = request.env['bni.member'].sudo()
        if not token:
            return Member
        return Member.search([
            ('access_token', '=', token), ('state', '=', 'active'),
            ('company_id', '=', event.company_id.id)], limit=1)

    def _active_members(self, event):
        return request.env['bni.member'].sudo().search([
            ('company_id', '=', event.company_id.id), ('state', '=', 'active')], order='name')

    def _find_member(self, identifier, event):
        """Busca al miembro activo por código, correo o teléfono (últimos 10 dígitos)."""
        Member = request.env['bni.member'].sudo()
        ident = (identifier or '').strip()
        if not ident:
            return Member
        base = [('company_id', '=', event.company_id.id), ('state', '=', 'active')]
        member = Member.search(base + [('member_code', '=ilike', ident)], limit=1)
        if not member and '@' in ident:
            member = Member.search(base + [('email', '=ilike', ident)], limit=1)
        if not member:
            phone = normalize_phone(ident)
            if len(phone) >= 8:
                candidates = Member.search(base + [('phone', '!=', False)])
                member = candidates.filtered(lambda m: normalize_phone(m.phone) == phone)[:1]
        return member

    def _values(self, event, **kw):
        values = {'csrf_token': request.csrf_token(), 'event': event}
        values.update(kw)
        return values

    def _render_message(self, title, message, kind='error', event=None, code=None):
        return request.render('bni_attendance.checkin_message', self._values(
            event, title=title, message=message, kind=kind, code=code))

    def _render_qr_expired(self, event):
        return self._render_message(
            _('Escanea el QR de la sesión'),
            _('Este QR ya expiró. Para registrar tu asistencia escanea el QR que se muestra en la sesión en este momento.'),
            kind='warning')

    def _set_cookie(self, response, member):
        response.set_cookie(
            COOKIE_NAME, member.access_token, max_age=COOKIE_MAX_AGE,
            httponly=True, samesite='Lax', secure=request.httprequest.scheme == 'https')

    def _render_result(self, event, line, is_new, code=None, member=None):
        values = self._values(
            event,
            line=line,
            is_new=is_new,
            code=code,
            time_local=event.format_local(line.check_in),
            state_label=STATE_LABELS.get(line.state, ''),
            can_observe=bool(member) and line.member_id == member and not line.is_substitute,
            meeting_url=event.meeting_url if event.modality == 'virtual' else False,
        )
        response = request.render('bni_attendance.checkin_result', values)
        if member:
            self._set_cookie(response, member)
        return response

    # ------------------------------------------------------------------
    # Página del QR
    # ------------------------------------------------------------------
    @http.route('/bni/e/<string:token>', type='http', auth='public', methods=['GET'])
    def event_page(self, token, k=None, **kw):
        event = self._get_event(token)
        if not event:
            return request.not_found()
        if event.state == 'open' and not event.check_access_code(k, OPEN_PAGE_MINUTES):
            return self._render_qr_expired(event)
        member = self._member_from_cookie(event)
        line = event._get_member_line(member) if member else request.env['bni.attendance']
        already = line if line and line.check_in and not line.is_substitute else request.env['bni.attendance']
        return request.render('bni_attendance.checkin_event_page', self._values(
            event,
            code=k or '',
            member=member,
            already=already,
            already_time=event.format_local(already.check_in) if already else '',
            members=self._active_members(event),
            limit_local=event.format_local(event.checkin_limit, '%d/%m/%Y %H:%M'),
        ))

    @http.route('/bni/e/<string:token>/member', type='http', auth='public', methods=['POST'])
    def event_member_checkin(self, token, k=None, identifier=None, use_cookie=None, observation=None, **kw):
        event = self._get_event(token)
        if not event:
            return request.not_found()
        if not event.check_access_code(k, SUBMIT_MINUTES):
            return self._render_qr_expired(event)
        member = self._member_from_cookie(event) if use_cookie else self._find_member(identifier, event)
        if not member:
            return self._render_message(
                _('No te encontramos'),
                _('Verifica tu código de miembro, correo o teléfono, o pide apoyo al equipo de membresía.'),
                event=event, code=k)
        try:
            line, is_new = event.register_member_checkin(member, method='qr', observation=observation)
        except UserError as e:
            return self._render_message(_('Registro no disponible'), str(e), kind='warning', event=event, code=k)
        _logger.info('BNI check-in: evento %s miembro %s', event.id, member.id)
        return self._render_result(event, line, is_new, code=k, member=member)

    @http.route('/bni/e/<string:token>/substitute', type='http', auth='public', methods=['POST'])
    def event_substitute_checkin(self, token, k=None, member_id=None, substitute_name=None,
                                 substitute_company=None, **kw):
        event = self._get_event(token)
        if not event:
            return request.not_found()
        if not event.check_access_code(k, SUBMIT_MINUTES):
            return self._render_qr_expired(event)
        member = request.env['bni.member']
        if member_id and str(member_id).isdigit():
            member = self._active_members(event).filtered(lambda m: m.id == int(member_id))
        if not member:
            return self._render_message(_('Selecciona al miembro'),
                                        _('Indica a qué miembro vienes a sustituir.'), kind='warning',
                                        event=event, code=k)
        try:
            line, is_new = event.register_substitute_checkin(member, substitute_name, substitute_company)
        except UserError as e:
            return self._render_message(_('No se pudo registrar'), str(e), kind='warning', event=event, code=k)
        return self._render_result(event, line, is_new, code=k)

    @http.route('/bni/e/<string:token>/guest', type='http', auth='public', methods=['POST'])
    def event_guest_checkin(self, token, k=None, name=None, company_name=None, invited_by_id=None, **kw):
        event = self._get_event(token)
        if not event:
            return request.not_found()
        if not event.check_access_code(k, SUBMIT_MINUTES):
            return self._render_qr_expired(event)
        invited_by = request.env['bni.member']
        if invited_by_id and str(invited_by_id).isdigit():
            invited_by = self._active_members(event).filtered(lambda m: m.id == int(invited_by_id))
        try:
            line = event.register_guest_checkin(name, company_name=company_name, invited_by=invited_by)
        except UserError as e:
            return self._render_message(_('No se pudo registrar'), str(e), kind='warning', event=event, code=k)
        return self._render_result(event, line, True, code=k)

    @http.route('/bni/e/<string:token>/observation', type='http', auth='public', methods=['POST'])
    def event_observation(self, token, observation=None, **kw):
        """El miembro agrega una observación a su propia asistencia (lo reconoce su celular)."""
        event = self._get_event(token)
        if not event:
            return request.not_found()
        member = self._member_from_cookie(event)
        line = event._get_member_line(member) if member else request.env['bni.attendance']
        if not line or not line.check_in or line.is_substitute or event.state != 'open':
            return self._render_message(_('No disponible'),
                                        _('Solo puedes agregar observaciones a tu asistencia mientras el evento está abierto.'),
                                        kind='warning')
        line.add_member_observation(observation)
        return self._render_message(_('¡Gracias!'), _('Tu observación quedó registrada.'), kind='success')

    # ------------------------------------------------------------------
    # Pantalla del QR dinámico (para proyectar en la sesión)
    # ------------------------------------------------------------------
    @http.route('/bni/screen/<int:event_id>', type='http', auth='user', methods=['GET'])
    def event_screen(self, event_id, **kw):
        if not request.env.user.has_group('bni_attendance.group_bni_user'):
            return request.not_found()
        event = request.env['bni.event'].search([('id', '=', event_id)], limit=1)
        if not event:
            return request.not_found()
        url = event.get_dynamic_checkin_url()
        return request.render('bni_attendance.checkin_screen', {
            'event': event,
            'qr': generate_qr_base64(url).decode(),
            'limit_local': event.format_local(event.checkin_limit, '%d/%m/%Y %H:%M'),
            'refresh': 20 if event.qr_mode == 'dynamic' else 300,
        })
