# -*- coding: utf-8 -*-
"""Portal de dirigentes (/dirigentes): pantallas sencillas para usar en el celular durante la junta."""
import logging
from urllib.parse import quote, urlencode

from odoo import _, http
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.addons.portal.controllers.web import Home

from ..models.scout_common import ACTIVITY_TYPES, quarter_label
from ..models.scout_leader_portal import LEADER_ITEM_TYPES
from ..models.scout_section_report import ITEM_TYPES

_logger = logging.getLogger(__name__)

STATUS_LABELS = {
    'present': ('Asistió', 'ok'), 'absent': ('Faltó', 'no'), 'camp': ('Campamento', 'ok'),
    'outing': ('Salida', 'ok'), 'na': ('No aplica', 'na'), 'dropped': ('Baja', 'na'), 'no_roll': ('Sin lista', 'na'),
}


class ScoutLeaderPortal(http.Controller):

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _svc():
        return request.env['scout.leader.portal']

    def _back(self, url, error=None, ok=None):
        params = {}
        if error:
            params['error'] = error
        if ok:
            params['ok'] = ok
        return request.redirect(url + ('?' + urlencode(params) if params else ''))

    def _render(self, template, section=None, page='home', **values):
        svc = self._svc()
        base = CustomerPortal()._prepare_portal_layout_values()
        base.update({
            'page_name': 'scout_leader',
            'leader_page': page,
            'sections': svc._sections(),
            'section': section,
            'svc': svc,
            'error': request.params.get('error'),
            'ok': request.params.get('ok'),
            'status_labels': STATUS_LABELS,
            'money': lambda v: '$ {:,.2f}'.format(v or 0.0),
            'local_dt': svc._local,
        })
        base.update(values)
        return request.render(template, base)

    def _section_or_redirect(self, section_id):
        try:
            return self._svc()._section(section_id), None
        except (AccessError, MissingError):
            return None, request.redirect('/dirigentes')

    @staticmethod
    def _ids(values):
        return [int(v) for v in values if str(v).isdigit()]

    # ------------------------------------------------------------------ inicio
    @http.route(['/dirigentes'], type='http', auth='user', website=True)
    def leader_index(self, **kw):
        sections = self._svc()._sections()
        if not sections:
            return self._render('scout_management.leader_no_access', page='none')
        if len(sections) == 1:
            return request.redirect(f'/dirigentes/{sections.id}')
        return self._render('scout_management.leader_choose_section', page='choose')

    @http.route(['/dirigentes/<int:section_id>'], type='http', auth='user', website=True)
    def leader_home(self, section_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        return self._render('scout_management.leader_home', section, 'home', dash=self._svc().dashboard(section))

    # ------------------------------------------------------------------ pase de lista
    @http.route(['/dirigentes/<int:section_id>/hoy'], type='http', auth='user', website=True, methods=['POST'])
    def leader_today(self, section_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        event = self._svc().create_today_session(section)
        return request.redirect(f'/dirigentes/{section.id}/lista/{event.id}')

    @http.route(['/dirigentes/<int:section_id>/lista/<int:event_id>'], type='http', auth='user', website=True)
    def leader_attendance(self, section_id, event_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        try:
            event = svc._event(event_id)
        except AccessError:
            return request.redirect(f'/dirigentes/{section.id}')
        lines = svc.attendance_lines(event, section)
        return self._render('scout_management.leader_attendance', section, 'list', event=event, lines=lines)

    @http.route(['/dirigentes/api/asistencia'], type='jsonrpc', auth='user')
    def leader_api_attendance(self, line_id, status, **kw):
        try:
            return {'ok': True, **self._svc().set_attendance(line_id, status)}
        except (UserError, AccessError) as e:
            return {'ok': False, 'error': str(e.args[0] if e.args else e)}

    @http.route(['/dirigentes/<int:section_id>/lista/<int:event_id>/<string:op>'], type='http', auth='user',
                website=True, methods=['POST'])
    def leader_attendance_op(self, section_id, event_id, op, **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        url = f'/dirigentes/{section.id}/lista/{event_id}'
        try:
            event = svc._event(event_id)
            if op == 'todos':
                svc.all_present(event, section)
            elif op == 'cerrar':
                svc.close_attendance(event, section, post.get('result'))
                return self._back(url, ok=_('Lista cerrada. ¡Gracias!'))
            elif op == 'reabrir':
                svc.reopen_attendance(event)
        except (UserError, AccessError) as e:
            return self._back(url, error=str(e.args[0]))
        return request.redirect(url)

    # ------------------------------------------------------------------ actividades
    @http.route(['/dirigentes/<int:section_id>/actividades'], type='http', auth='user', website=True)
    def leader_activities(self, section_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        return self._render('scout_management.leader_activities', section, 'activities',
                            upcoming=svc.events(section, True, 30), past=svc.events(section, False, 15))

    @http.route(['/dirigentes/<int:section_id>/actividad/nueva', '/dirigentes/<int:section_id>/actividad/<int:event_id>'],
                type='http', auth='user', website=True, methods=['GET'])
    def leader_activity_form(self, section_id, event_id=None, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        event = request.env['calendar.event']
        if event_id:
            try:
                event = svc._event(event_id)
            except AccessError:
                return request.redirect(f'/dirigentes/{section.id}/actividades')
        start = svc._local(event.start) if event else None
        stop = svc._local(event.stop) if event else None
        return self._render('scout_management.leader_activity_form', section, 'activities', event=event,
                            editable=not event or svc._event_editable(event),
                            date=(start.date().isoformat() if start else kw.get('date') or svc._today().isoformat()),
                            time_from=start.strftime('%H:%M') if start else '10:00',
                            time_to=stop.strftime('%H:%M') if stop else '13:00',
                            leaders=svc.leader_partners(section), activity_types=ACTIVITY_TYPES)

    @http.route(['/dirigentes/<int:section_id>/actividad/guardar'], type='http', auth='user', website=True,
                methods=['POST'])
    def leader_activity_save(self, section_id, **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        event = None
        values = dict(post)
        values['responsible_ids'] = request.httprequest.form.getlist('responsible_ids')
        try:
            if post.get('event_id'):
                event = svc._event(post['event_id'])
            event = svc.save_event(section, event, values)
        except (UserError, AccessError, ValidationError) as e:
            back = f'/dirigentes/{section.id}/actividad/' + (str(event.id) if event else 'nueva')
            return self._back(back, error=str(e.args[0]))
        if post.get('then') == 'lista':
            return request.redirect(f'/dirigentes/{section.id}/lista/{event.id}')
        return self._back(f'/dirigentes/{section.id}/plan', ok=_('Actividad guardada.'))

    # ------------------------------------------------------------------ adelantos
    @http.route(['/dirigentes/<int:section_id>/adelantos'], type='http', auth='user', website=True,
                methods=['GET'])
    def leader_progress(self, section_id, stage_id=None, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        stages = svc.stages(section)
        stage = stages.filtered(lambda s: s.id == int(stage_id or 0))
        members = svc._members(section)
        progress = {}
        if stage:
            for p in request.env['scout.member.progress'].sudo().search(
                    [('stage_id', '=', stage.id), ('member_id', 'in', members.ids)]):
                progress[p.member_id.id] = p
        return self._render('scout_management.leader_progress', section, 'progress', stages=stages, stage=stage,
                            members=members, progress=progress, today=svc._today().isoformat())

    @http.route(['/dirigentes/<int:section_id>/adelantos'], type='http', auth='user', website=True,
                methods=['POST'])
    def leader_progress_save(self, section_id, **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        member_ids = self._ids(request.httprequest.form.getlist('member_ids'))
        url = f'/dirigentes/{section.id}/adelantos?stage_id={post.get("stage_id") or ""}'
        if not member_ids:
            return request.redirect(url + '&' + urlencode({'error': _('Toca a los miembros que lo lograron.')}))
        try:
            done = self._svc().grant_progress(section, post.get('stage_id'), member_ids, post.get('state'),
                                              post.get('date'))
        except (UserError, AccessError) as e:
            return request.redirect(url + '&' + urlencode({'error': str(e.args[0])}))
        return request.redirect(url + '&' + urlencode({'ok': _('Registrado para %s miembro(s).', len(done))}))

    # ------------------------------------------------------------------ planificación
    def _plan_period(self, section, tipo, q):
        svc = self._svc()
        if tipo == 'anual':
            start, end, label = svc.cycle_period(section)
            return 'anual', start, end, _('Ciclo %s', label), 0
        q = str(q or '0')
        offset = int(q) if q.lstrip('-').isdigit() else 0
        start, end = svc.quarter_period(offset)
        return 'trimestral', start, end, quarter_label(start, end), offset

    @http.route(['/dirigentes/<int:section_id>/plan'], type='http', auth='user', website=True, methods=['GET'])
    def leader_plan(self, section_id, tipo='trimestral', q='0', **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        tipo, start, end, label, offset = self._plan_period(section, tipo, q)
        values = {'tipo': tipo, 'start': start, 'end': end, 'label': label, 'offset': offset}
        if tipo == 'anual':
            values['months'] = svc.plan_months(section, start, end)
        else:
            values['days'] = svc.plan_days(section, start, end)
        return self._render('scout_management.leader_plan', section, 'plan', **values)

    @http.route(['/dirigentes/<int:section_id>/plan/sesiones'], type='http', auth='user', website=True,
                methods=['POST'])
    def leader_plan_sessions(self, section_id, q='0', **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        _tipo, start, end, _label, offset = self._plan_period(section, 'trimestral', q)
        url = f'/dirigentes/{section.id}/plan?tipo=trimestral&q={offset}'
        try:
            created = self._svc().generate_sessions(section, start, end)
        except (UserError, AccessError) as e:
            return request.redirect(url + '&' + urlencode({'error': str(e.args[0])}))
        msg = _('Se crearon %s sesiones; toca cada una para poner su temática y objetivos.', len(created)) \
            if created else _('Todos los sábados ya tienen actividad.')
        return request.redirect(url + '&' + urlencode({'ok': msg}))

    @http.route(['/dirigentes/<int:section_id>/plan/pdf'], type='http', auth='user', website=True)
    def leader_plan_pdf(self, section_id, tipo='trimestral', q='0', **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        tipo, start, end, label, _offset = self._plan_period(section, tipo, q)
        pdf = self._svc().program_pdf(section, start, end)
        name = f'Programa {tipo} {section.display_name} {label}'
        headers = [('Content-Type', 'application/pdf'), ('Content-Length', len(pdf)),
                   ('Content-Disposition', f'inline; filename="{name}.pdf"')]
        return request.make_response(pdf, headers=headers)

    # ------------------------------------------------------------------ control de asistencia
    @http.route(['/dirigentes/<int:section_id>/asistencia'], type='http', auth='user', website=True)
    def leader_attendance_control(self, section_id, tipo='trimestral', q='0', **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        tipo, start, end, label, offset = self._plan_period(section, tipo, q)
        matrix = self._svc().attendance_matrix(section, start, end)
        return self._render('scout_management.leader_attendance_control', section, 'attendance', matrix=matrix,
                            tipo=tipo, label=label, offset=offset)

    @http.route(['/dirigentes/<int:section_id>/asistencia/pdf'], type='http', auth='user', website=True)
    def leader_attendance_pdf(self, section_id, tipo='trimestral', q='0', **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        tipo, start, end, label, _offset = self._plan_period(section, tipo, q)
        pdf = self._svc().attendance_pdf(section, start, end)
        headers = [('Content-Type', 'application/pdf'), ('Content-Length', len(pdf)),
                   ('Content-Disposition', f'inline; filename="Asistencia {section.display_name} {label}.pdf"')]
        return request.make_response(pdf, headers=headers)

    # ------------------------------------------------------------------ informes trimestral y anual
    @staticmethod
    def _report_period(tipo):
        return 'annual' if tipo == 'anual' else 'quarter'

    @http.route(['/dirigentes/<int:section_id>/informe'], type='http', auth='user', website=True, methods=['GET'])
    def leader_report(self, section_id, tipo='trimestral', **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        report = self._svc().current_report(section, period=self._report_period(tipo))
        blocks = []
        labels = dict(ITEM_TYPES)
        if report.period_type == 'annual':
            labels.update({'objective': _('Objetivos del ciclo'), 'activity': _('Actividades del ciclo'),
                           'progress': _('Avances del ciclo'), 'previous': _('Situación al iniciar el ciclo')})
        for item_type in LEADER_ITEM_TYPES:
            items = report.item_ids.filtered(lambda i: i.item_type == item_type)
            blocks.append({'key': item_type, 'label': labels[item_type],
                           'auto': items.filtered('is_auto'),
                           'manual': '\n'.join(items.filtered(lambda i: not i.is_auto).mapped('name'))})
        return self._render('scout_management.leader_report', section, 'report', report=report, blocks=blocks,
                            tipo='anual' if report.period_type == 'annual' else 'trimestral')

    @http.route(['/dirigentes/<int:section_id>/informe'], type='http', auth='user', website=True, methods=['POST'])
    def leader_report_save(self, section_id, tipo='trimestral', **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        tipo = 'anual' if tipo == 'anual' else 'trimestral'
        url = f'/dirigentes/{section.id}/informe?tipo={tipo}'
        report = svc.current_report(section, period=self._report_period(tipo))
        try:
            if post.get('op') == 'refresh':
                if report.state != 'draft':
                    raise UserError(_('El informe ya se presentó.'))
                report.action_refresh()
                return request.redirect(url + '&' + urlencode({'ok': _('Datos actualizados con la asistencia y los adelantos.')}))
            svc.save_report(report, post)
        except (UserError, AccessError) as e:
            return request.redirect(url + '&' + urlencode({'error': str(e.args[0])}))
        return request.redirect(url + '&' + urlencode({'ok': _('Informe guardado.')}))

    @http.route(['/dirigentes/<int:section_id>/informe/pdf'], type='http', auth='user', website=True)
    def leader_report_pdf(self, section_id, tipo='trimestral', **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        report = self._svc().current_report(section, period=self._report_period(tipo))
        pdf, _type = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
            'scout_management.action_report_section_report', report.ids)
        headers = [('Content-Type', 'application/pdf'), ('Content-Length', len(pdf)),
                   ('Content-Disposition', f'inline; filename="{report.name}.pdf"')]
        return request.make_response(pdf, headers=headers)

    # ------------------------------------------------------------------ mensajes
    @http.route(['/dirigentes/<int:section_id>/mensajes'], type='http', auth='user', website=True)
    def leader_messages(self, section_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        return self._render('scout_management.leader_messages', section, 'messages',
                            conversations=self._svc().conversations(section), members=self._svc()._members(section))

    @http.route(['/dirigentes/<int:section_id>/mensajes/<int:conv_id>'], type='http', auth='user', website=True)
    def leader_message_thread(self, section_id, conv_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        try:
            conv = self._svc().conversation(conv_id)
        except AccessError:
            return request.redirect(f'/dirigentes/{section.id}/mensajes')
        pid = request.env.user.partner_id.id
        return self._render('scout_management.leader_message_thread', section, 'messages', conversation=conv,
                            member=conv.member_id, object=conv, token=conv.access_token, pid=pid,
                            hash=conv._sign_token(pid))

    @http.route(['/dirigentes/<int:section_id>/miembro/<int:member_id>/chat'], type='http', auth='user', website=True)
    def leader_member_chat(self, section_id, member_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        try:
            conv = self._svc().open_conversation_for_member(member_id)
        except AccessError:
            return request.redirect(f'/dirigentes/{section.id}/miembros')
        return request.redirect(f'/dirigentes/{section.id}/mensajes/{conv.id}')

    # ------------------------------------------------------------------ miembros
    @http.route(['/dirigentes/<int:section_id>/miembros'], type='http', auth='user', website=True)
    def leader_members(self, section_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        return self._render('scout_management.leader_members', section, 'members',
                            members=self._svc()._members(section, include_leaders=True))

    @http.route(['/dirigentes/<int:section_id>/miembro/<int:member_id>'], type='http', auth='user', website=True)
    def leader_member(self, section_id, member_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        try:
            member = self._svc()._member(member_id, section)
        except AccessError:
            return request.redirect(f'/dirigentes/{section.id}/miembros')
        progress = member.scout_progress_ids.sorted(lambda p: (p.state != 'achieved', p.date_achieved or p.date_start))
        lines = request.env['scout.attendance.line'].sudo().search(
            [('member_id', '=', member.id), ('closed', '=', True)], limit=8)
        return self._render('scout_management.leader_member', section, 'members', member=member, progress=progress,
                            attendance=lines)


class ScoutLeaderHome(Home):

    def _login_redirect(self, uid, redirect=None):
        """Los dirigentes de portal llegan directo a su portal al iniciar sesión."""
        if not redirect:
            user = request.env['res.users'].sudo().browse(uid)
            if user.share and user._scout_is_leader():
                redirect = '/dirigentes'
        return super()._login_redirect(uid, redirect=redirect)
