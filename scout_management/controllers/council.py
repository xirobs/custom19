# -*- coding: utf-8 -*-
"""Consejo en vivo (/scouts/consejo): reloj por punto, firma de asistencia, transcripción y acta."""
import base64
import logging
from datetime import timedelta

from odoo import _, fields, http
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.http import request

from .leader_portal import ScoutLeaderPortal

_logger = logging.getLogger(__name__)

MAX_AUDIO = 20 * 1024 * 1024


class ScoutCouncil(http.Controller):

    # ------------------------------------------------------------------ ayudas
    @staticmethod
    def _meeting(meeting_id, moderate=False):
        meeting = request.env['scout.meeting'].sudo().browse(int(meeting_id or 0)).exists()
        if not meeting:
            raise MissingError(_('La reunión no existe.'))
        # sudo conserva al usuario (env.user): los permisos los revisa _scout_check
        meeting._scout_check(moderate=moderate)
        return meeting

    @staticmethod
    def _section():
        svc = request.env['scout.leader.portal']
        sections = svc._sections()
        return (sections.filtered('is_leadership')[:1] or sections[:1]) or None

    def _render(self, template, **values):
        return ScoutLeaderPortal()._render(template, self._section(), 'council', **values)

    @staticmethod
    def _decode(data):
        if isinstance(data, str) and data.startswith('data:'):
            head, _sep, body = data.partition(',')
            return base64.b64decode(body), head[5:].split(';')[0] or 'application/octet-stream'
        return base64.b64decode(data or ''), 'application/octet-stream'

    @staticmethod
    def _api(fn):
        try:
            return fn()
        except (UserError, AccessError, MissingError) as e:
            return {'error': str(e.args[0] if e.args else e)}

    # ------------------------------------------------------------------ páginas
    @http.route(['/scouts/consejo'], type='http', auth='user', website=True)
    def council_list(self, **kw):
        user = request.env.user
        svc = request.env['scout.leader.portal']
        if user.share and not svc._sections():
            return request.redirect('/my')
        today = fields.Datetime.now()
        Event = request.env['calendar.event'].sudo()
        events = Event.search([('is_scout_event', '=', True), ('scout_activity_type', '=', 'council'),
                               ('start', '>=', today - timedelta(days=45)), ('start', '<=', today + timedelta(days=120))],
                              order='start')
        Meeting = request.env['scout.meeting'].sudo()
        company = user.company_id
        meetings = Meeting.search([('company_id', '=', company.id), ('state', '!=', 'cancel')], limit=12,
                                  order='date desc')
        by_event = {m.event_id.id: m for m in Meeting.search([('event_id', 'in', events.ids)])}
        upcoming = [e for e in events if e.stop and e.stop >= today]
        return self._render('scout_management.council_list', events=events, upcoming=upcoming, meetings=meetings,
                            by_event=by_event, can_create=bool(svc._is_group_manager()))

    @http.route(['/scouts/consejo/evento/<int:event_id>'], type='http', auth='user', website=True)
    def council_from_event(self, event_id, **kw):
        event = request.env['calendar.event'].sudo().browse(event_id).exists()
        if not event or not event.is_scout_event:
            return request.redirect('/scouts/consejo')
        meeting = request.env['scout.meeting'].sudo().search([('event_id', '=', event.id),
                                                              ('state', '!=', 'cancel')], limit=1)
        if not meeting:
            if not request.env['scout.leader.portal']._is_group_manager() and request.env.user.share:
                return ScoutLeaderPortal()._back('/scouts/consejo', error=_(
                    'La jefatura de grupo o la secretaría todavía no preparan esta reunión.'))
            meeting = request.env['scout.meeting']._scout_for_event(event)
        return request.redirect('/scouts/consejo/%s' % meeting.id)

    @http.route(['/scouts/consejo/nuevo'], type='http', auth='user', website=True, methods=['POST'])
    def council_new(self, **post):
        if not request.env['scout.leader.portal']._is_group_manager():
            return request.redirect('/scouts/consejo')
        meeting = request.env['scout.meeting'].sudo().create({
            'title': (post.get('title') or '').strip() or _('Consejo de Grupo'),
            'company_id': request.env.user.company_id.id, 'mode': 'online',
            'secretary_id': request.env.user.partner_id.id,
        })
        meeting.action_scout_load_template()
        return request.redirect('/scouts/consejo/%s' % meeting.id)

    @http.route(['/scouts/consejo/<int:meeting_id>'], type='http', auth='user', website=True)
    def council_live(self, meeting_id, **kw):
        try:
            meeting = self._meeting(meeting_id)
        except (AccessError, MissingError):
            return request.redirect('/scouts/consejo')
        roster = meeting.sudo()._scout_roster() if meeting._scout_can_moderate() else request.env['res.partner']
        signed = {c.partner_id.id: c for c in meeting.sudo().checkin_ids if c.partner_id}
        Event = request.env['calendar.event']
        provider, key, _model = Event._scout_ai_config()
        return self._render('scout_management.council_live', meeting=meeting.sudo(),
                            can_moderate=meeting._scout_can_moderate(), roster=roster, signed=signed,
                            ai_ready=bool(key), ai_audio=bool(key) and provider in ('gemini', 'openai'),
                            backend_url='/odoo/action-scout_management.action_scout_meeting/%s' % meeting.id
                            if not request.env.user.share else False)

    # ------------------------------------------------------------------ API (jsonrpc)
    @http.route(['/scouts/consejo/api/estado'], type='jsonrpc', auth='user')
    def api_state(self, meeting_id, full=True, **kw):
        return self._api(lambda: self._meeting(meeting_id)._scout_live_payload(full=bool(full)))

    @http.route(['/scouts/consejo/api/canal'], type='jsonrpc', auth='public')
    def api_channel(self, channel_id, **kw):
        """Para el reloj dentro de la videollamada de Odoo."""
        if request.env.user._is_public():
            return {}
        meeting = request.env['scout.meeting']._scout_for_channel(channel_id)
        if not meeting or not meeting._scout_can_view():
            return {}
        payload = meeting._scout_live_payload(full=False)
        payload['ai_audio'] = (lambda c: bool(c[1]) and c[0] in ('gemini', 'openai'))(
            request.env['calendar.event']._scout_ai_config())
        return payload

    @http.route(['/scouts/consejo/api/control'], type='jsonrpc', auth='user')
    def api_control(self, meeting_id, op, line_id=None, minutes=None, partner_id=None, name=None, **kw):
        return self._api(lambda: self._meeting(meeting_id, True)._scout_live_control(
            op, line_id, minutes, partner_id=partner_id, name=name))

    @http.route(['/scouts/consejo/api/agotado'], type='jsonrpc', auth='user')
    def api_expired(self, meeting_id, key=None, **kw):
        return self._api(lambda: self._meeting(meeting_id)._scout_turn_expired(key))

    @http.route(['/scouts/consejo/api/ajustes'], type='jsonrpc', auth='user')
    def api_settings(self, meeting_id, auto_mute=None, grace=None, chat_notices=None, **kw):
        cfg = {k: v for k, v in {'auto_mute': auto_mute, 'grace': grace, 'chat_notices': chat_notices}.items()
               if v is not None}
        return self._api(lambda: self._meeting(meeting_id, True)._scout_live_control('settings', cfg=cfg))

    @http.route(['/scouts/consejo/api/agenda'], type='jsonrpc', auth='user')
    def api_agenda(self, meeting_id, points=None, text=None, template=False, file=None, preview=False, **kw):
        def run():
            meeting = self._meeting(meeting_id, True).sudo()
            if template:
                from ..models.scout_council_live import COUNCIL_TEMPLATE
                pts = [{'name': n, 'minutes': m} for n, m in COUNCIL_TEMPLATE]
            elif file:
                content, mimetype = self._decode(file)
                if len(content) > 15 * 1024 * 1024:
                    raise UserError(_('El archivo pesa más de 15 MB.'))
                pts = meeting._scout_agenda_from_file(content, mimetype)
            elif text is not None:
                from ..models.scout_council_live import parse_agenda_text
                pts = parse_agenda_text(text)
            else:
                pts = points or []
            if preview:
                return {'points': pts}
            if not pts:
                raise UserError(_('No encontré puntos en el orden del día.'))
            meeting._scout_set_agenda(pts)
            return meeting._scout_live_payload()
        return self._api(run)

    @http.route(['/scouts/consejo/api/lista'], type='jsonrpc', auth='user')
    def api_roll(self, meeting_id, partner_id, present=True, **kw):
        def run():
            meeting = self._meeting(meeting_id, True)
            meeting._scout_mark_attendance(partner_id, present)
            if present:
                partner = request.env['res.partner'].sudo().browse(int(partner_id))
                request.env['scout.meeting.checkin'].sudo()._scout_sign(
                    meeting, partner=partner, source='roll', ip=request.httprequest.remote_addr)
            return {'ok': True}
        return self._api(run)

    @http.route(['/scouts/consejo/api/firmar'], type='jsonrpc', auth='user')
    def api_sign(self, meeting_id, **kw):
        def run():
            meeting = self._meeting(meeting_id)
            request.env['scout.meeting.checkin'].sudo()._scout_sign(
                meeting, partner=request.env.user.partner_id, source='portal', ip=request.httprequest.remote_addr)
            return {'my_checkin': meeting._scout_my_checkin()}
        return self._api(run)

    @http.route(['/scouts/consejo/api/grabar'], type='jsonrpc', auth='user')
    def api_record(self, meeting_id, on=True, **kw):
        return self._api(lambda: self._meeting(meeting_id, True)._scout_recording(bool(on)))

    @http.route(['/scouts/consejo/api/audio'], type='jsonrpc', auth='user')
    def api_audio(self, meeting_id, data, mimetype=None, speakers=None, source='call', **kw):
        def run():
            meeting = self._meeting(meeting_id, True)
            content, mime = self._decode(data)
            if len(content) > MAX_AUDIO:
                raise UserError(_('El audio pesa más de 20 MB; súbelo en partes.'))
            return meeting._scout_transcribe(content, mimetype or mime, speakers=speakers,
                                             source=source if source in ('call', 'mic', 'file') else 'call')
        return self._api(run)

    @http.route(['/scouts/consejo/api/texto'], type='jsonrpc', auth='user')
    def api_text(self, meeting_id, text, source='manual', **kw):
        def run():
            meeting = self._meeting(meeting_id, True)
            rec = meeting._scout_add_text(text, source=source if source in ('browser', 'manual') else 'manual')
            return {'ok': bool(rec)}
        return self._api(run)

    @http.route(['/scouts/consejo/api/acta'], type='jsonrpc', auth='user')
    def api_minutes(self, meeting_id, **kw):
        def run():
            meeting = self._meeting(meeting_id, True)
            result = meeting._scout_generate_minutes()
            m = meeting.sudo()
            result.update({
                'summary': m.ai_summary or '',
                'minutes': m.minutes or '',
                'agreements': [{'name': a.name, 'responsible': a.responsible_id.name or '',
                                'due': a.due_date and a.due_date.strftime('%d/%m/%Y') or ''}
                               for a in m.agreement_ids],
            })
            return result
        return self._api(run)

    @http.route(['/scouts/consejo/api/videollamada'], type='jsonrpc', auth='user')
    def api_videocall(self, meeting_id, **kw):
        return self._api(lambda: {'url': self._meeting(meeting_id, True)._scout_ensure_videocall()})
