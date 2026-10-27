# -*- coding: utf-8 -*-
"""Portal de dirigentes (/dirigentes): pantallas sencillas para usar en el celular durante la junta."""
import json
import logging
from datetime import date
from urllib.parse import quote, urlencode


from dateutil.relativedelta import relativedelta

from odoo import _, http
from odoo.tools import html2plaintext
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.addons.portal.controllers.web import Home

from ..models.calendar_event import PLAN_STATES, SCOUT_CATEGORIES
from ..models.scout_common import ACTIVITY_TYPES, MONTHS, quarter_label
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
        sep = '&' if '?' in url else '?'
        return request.redirect(url + (sep + urlencode(params) if params else ''))

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
            'plan_states': dict(PLAN_STATES),
            'relativedelta': relativedelta,
            'url_quote': quote,
            'plain_text': lambda v: html2plaintext(v).strip() if v else '',
            'categories_sel': dict(SCOUT_CATEGORIES),
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
                            sl_can_edit=not event or svc._event_editable(event),
                            date=(start.date().isoformat() if start else kw.get('date') or svc._today().isoformat()),
                            time_from=start.strftime('%H:%M') if start else (kw.get('time_from') or '10:00'),
                            time_to=stop.strftime('%H:%M') if stop else (kw.get('time_to') or '13:00'),
                            date_end=(stop.date().isoformat() if stop else kw.get('date_end') or ''),
                            preset_type=kw.get('tipo') if kw.get('tipo') in dict(ACTIVITY_TYPES) else None,
                            leaders=svc.leader_partners(section),
                            activity_types=[t for t in ACTIVITY_TYPES if t[0] not in ('council', 'group')])

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
        if post.get('then') == 'plan':
            return self._back(f'/dirigentes/{section.id}/plan', ok=_('Actividad guardada.'))
        return self._back(f'/dirigentes/{section.id}/actividad/{event.id}/programa',
                          ok=_('Datos de la sesión guardados. Ahora dicta o captura su programa.'))

    @http.route(['/dirigentes/<int:section_id>/plan/dia/<string:day>'], type='http', auth='user', website=True)
    def leader_plan_day(self, section_id, day, **kw):
        """Botón "Planear" del sábado: abre (o crea) la sesión y va directo a editarla y dictar su programa."""
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        try:
            from odoo.fields import Date
            event = self._svc().plan_day(section, Date.to_date(day))
        except (ValueError, UserError, AccessError) as e:
            return self._back(f'/dirigentes/{section.id}/plan', error=str(e.args[0] if e.args else e))
        return request.redirect(f'/dirigentes/{section.id}/actividad/{event.id}/programa')

    # ------------------------------------------------------------------ jefatura: avances de todo el grupo
    def _group_progress_page(self, section, seccion=None):
        svc = self._svc()
        data = svc.group_progress()
        try:
            selected = int(seccion or 0)
        except ValueError:
            selected = 0
        return self._render('scout_management.leader_group_progress', section, 'progress', groups=data,
                            selected=selected, svc=svc)

    # ------------------------------------------------------------------ adelantos
    @http.route(['/dirigentes/<int:section_id>/adelantos'], type='http', auth='user', website=True,
                methods=['GET'])
    def leader_progress(self, section_id, stage_id=None, grupo=None, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        if section.is_leadership:
            return self._group_progress_page(section, kw.get('seccion'))
        stages = svc.stages(section)
        stage = stages.filtered(lambda s: s.id == int(stage_id or 0) if str(stage_id or '').isdigit() else False)
        path = svc.path_stages(section)
        categories = svc.specialty_categories(section)
        groups = [('adelantos', _('Adelantos, terminal y ruta de enlace'), path)]
        groups += [(f'cat{c.id or 0}', _('Especialidades · %s', c.name or _('Otras')), st) for c, st in categories]
        if stage and not grupo:
            grupo = next((key for key, _label, st in groups if stage in st), None)
        current = next((g for g in groups if g[0] == grupo), None)
        if stage and current and stage not in current[2]:
            stage = stages.browse()
        members = svc._members(section)
        progress = {}
        if stage:
            for p in request.env['scout.member.progress'].sudo().search(
                    [('stage_id', '=', stage.id), ('member_id', 'in', members.ids)]):
                progress[p.member_id.id] = p
        return self._render('scout_management.leader_progress', section, 'progress', groups=groups,
                            group=current, stage=stage, members=members, progress=progress,
                            today=svc._today().isoformat())

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
        values = {'tipo': tipo, 'start': start, 'end': end, 'label': label, 'offset': offset,
                  'summary': svc.plan_summary(svc.period_events(section, start, end))}
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

    @http.route(['/dirigentes/<int:section_id>/plan/enviar'], type='http', auth='user', website=True,
                methods=['POST'])
    def leader_plan_submit(self, section_id, tipo='trimestral', q='0', **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        tipo, start, end, _label, offset = self._plan_period(section, tipo, q)
        url = f'/dirigentes/{section.id}/plan?tipo={tipo}&q={offset}'
        try:
            events = self._svc().submit_plan(section, start, end)
        except (UserError, AccessError) as e:
            return request.redirect(url + '&' + urlencode({'error': str(e.args[0])}))
        return request.redirect(url + '&' + urlencode(
            {'ok': _('Se enviaron %s actividades al consejo. El jefe de grupo dará el visto bueno.', len(events))}))

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
        if section.is_leadership and kw.get('ver') != 'jefatura':
            ver = kw.get('ver') if kw.get('ver') in ('jefes', 'secciones') else 'resumen'
            return self._render('scout_management.leader_group_attendance', section, 'attendance',
                                data=self._svc().group_attendance(start, end), ver=ver, tipo=tipo, label=label,
                                offset=offset, today_event=self._svc().today_event(section))
        matrix = self._svc().attendance_matrix(section, start, end)
        return self._render('scout_management.leader_attendance_control', section, 'attendance', matrix=matrix,
                            tipo=tipo, label=label, offset=offset, today_event=self._svc().today_event(section))

    @http.route(['/dirigentes/<int:section_id>/asistencia/grupo.xlsx'], type='http', auth='user', website=True)
    def leader_group_attendance_xlsx(self, section_id, tipo='trimestral', q='0', **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        if not section.is_leadership:
            return request.redirect(f'/dirigentes/{section.id}/asistencia')
        tipo, start, end, label, _offset = self._plan_period(section, tipo, q)
        content = self._svc().group_attendance_xlsx(start, end, label)
        headers = [('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                   ('Content-Length', len(content)),
                   ('Content-Disposition', f'attachment; filename="Asistencia del grupo {label}.xlsx"')]
        return request.make_response(content, headers=headers)

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
        svc = self._svc()
        if section.is_leadership:
            conversations, members = [], request.env['res.partner']
            for sec in svc._group_sections():
                conversations += svc.conversations(sec)
                members |= svc._members(sec)
            conversations.sort(key=lambda r: r['last'].date, reverse=True)
            return self._render('scout_management.leader_messages', section, 'messages',
                                conversations=conversations, members=members.sorted('name'))
        return self._render('scout_management.leader_messages', section, 'messages',
                            conversations=svc.conversations(section), members=svc._members(section))

    @http.route(['/dirigentes/<int:section_id>/whatsapp'], type='http', auth='user', website=True,
                methods=['GET', 'POST'])
    def leader_whatsapp(self, section_id, **post):
        """Aviso por WhatsApp a las familias o a los jefes de una o varias secciones."""
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        options = svc._group_sections() if section.is_leadership else section
        form = request.httprequest.form if request.httprequest.method == 'POST' else request.httprequest.args
        chosen_ids = {int(x) for x in form.getlist('seccion') if str(x).isdigit()}
        chosen = options.filtered(lambda s: s.id in chosen_ids) if chosen_ids else (
            options if not section.is_leadership else options.browse())
        audience = 'leaders' if post.get('para') == 'jefes' else 'families'
        text = (post.get('texto') or '').strip()
        recipients, missing = [], []
        if request.httprequest.method == 'POST' and text and chosen:
            recipients, missing = svc.whatsapp_recipients(chosen, audience, text)
        default_text = _('Hola {nombre}, te escribimos de %s: ', section.display_name if not section.is_leadership
                         else request.env.company.name)
        return self._render('scout_management.leader_whatsapp', section, 'messages', options=options,
                            chosen=chosen, audience=audience, text=text or default_text, recipients=recipients,
                            missing=missing, sent=request.httprequest.method == 'POST')

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
        if section.is_leadership and kw.get('ver') != 'jefatura':
            return self._render('scout_management.leader_group_members', section, 'members',
                                groups=self._svc().group_members(), alerts=self._svc().alerts(section),
                                open_id=int(kw.get('seccion') or 0) if str(kw.get('seccion') or '').isdigit() else 0)
        members = self._svc()._members(section, include_leaders=True)
        orden = kw.get('orden') if kw.get('orden') in ('equipo', 'nombre', 'edad', 'alertas') else 'equipo'
        youth = members.filtered(lambda m: m.scout_type == 'youth')
        leaders = members - youth
        if orden == 'nombre':
            youth = youth.sorted(lambda m: m.name or '')
        elif orden == 'edad':
            youth = youth.sorted(lambda m: (m.scout_birthdate or date.max))
        elif orden == 'alertas':
            youth = youth.sorted(lambda m: (not m.scout_needs_registration, not m.scout_age_alert, m.name or ''))
        return self._render('scout_management.leader_members', section, 'members', members=leaders + youth,
                            orden=orden, alerts=self._svc().alerts(section))

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
                            attendance=lines, pay=self._svc().payment_info(member) if member.scout_type == 'youth' else {},
                            role=self._svc().role_label(section, member))

    # ------------------------------------------------------------------ programa de la sesión (dictado)
    def _plan_event(self, section, event_id):
        svc = self._svc()
        event = svc._event(event_id)
        return event, svc._event_editable(event)

    @http.route(['/dirigentes/<int:section_id>/actividad/<int:event_id>/programa'], type='http', auth='user',
                website=True)
    def leader_session_plan(self, section_id, event_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        try:
            event, editable = self._plan_event(section, event_id)
        except AccessError:
            return request.redirect(f'/dirigentes/{section.id}/plan')
        from ..models import scout_plan_parser as parser
        data = {
            'section_id': section.id, 'event_id': event.id, 'editable': editable,
            'approved': event.scout_plan_state == 'approved' and bool(event.scout_plan_line_ids),
            'lines': event._scout_plan_lines_dicts(), 'stages': parser.STAGES, 'kinds': parser.KINDS,
            'flags': [[f, parser.FLAG_LABELS[f]] for f in parser.FLAGS],
            'leaders': event._scout_plan_context()[2],
            'template': event._scout_plan_template() if editable else [],
            'copy_sources': event._scout_plan_copy_sources() if editable else [],
            'session_start': '%02d:%02d' % divmod(event._scout_plan_window()[0], 60),
        }
        svc = self._svc()
        start, stop = svc._local(event.start), svc._local(event.stop)
        return self._render('scout_management.leader_session_plan', section, 'plan', event=event, sl_can_edit=editable,
                            plan_json=json.dumps(data, ensure_ascii=False), copy_sources=data['copy_sources'],
                            date=start.date().isoformat(), date_end=stop.date().isoformat(),
                            time_from=start.strftime('%H:%M'), time_to=stop.strftime('%H:%M'),
                            preset_type=None, leaders=svc.leader_partners(section),
                            activity_types=[t for t in ACTIVITY_TYPES if t[0] not in ('council', 'group')])

    @http.route(['/dirigentes/api/plan/interpretar'], type='jsonrpc', auth='user')
    def leader_api_plan_interpret(self, section_id, event_id, text, lines=None, **kw):
        try:
            section = self._svc()._section(section_id)
            event, editable = self._plan_event(section, event_id)
            if not editable:
                raise AccessError(_('Esta actividad la edita la jefatura.'))
            return {'ok': True, **event.scout_plan_interpret(text, lines)}
        except (UserError, AccessError, ValidationError) as e:
            return {'ok': False, 'error': str(e.args[0] if e.args else e)}

    @http.route(['/dirigentes/api/plan/copiar'], type='jsonrpc', auth='user')
    def leader_api_plan_copy(self, section_id, event_id, source_id, **kw):
        """Actividades de otra junta de la sección, recorridas al horario de esta (no se guarda hasta "Guardar")."""
        try:
            section = self._svc()._section(section_id)
            event, editable = self._plan_event(section, event_id)
            if not editable:
                raise AccessError(_('Esta actividad la edita la jefatura.'))
            source = self._svc()._event(source_id)
            if not (source.scout_section_ids & event.scout_section_ids):
                raise AccessError(_('Esa junta no es de tu sección.'))
            return {'ok': True, 'lines': event._scout_plan_copy_from(source)}
        except (UserError, AccessError, ValidationError) as e:
            return {'ok': False, 'error': str(e.args[0] if e.args else e)}

    @http.route(['/dirigentes/api/plan/archivo'], type='http', auth='user', methods=['POST'])
    def leader_api_plan_file(self, section_id=None, event_id=None, file=None, **kw):
        """Lee el programa de un archivo (Excel, PDF o imagen) y lo regresa al editor sin guardarlo."""
        try:
            section = self._svc()._section(int(section_id or 0))
            event, editable = self._plan_event(section, int(event_id or 0))
            if not editable:
                raise AccessError(_('Esta actividad la edita la jefatura.'))
            if not file or not getattr(file, 'filename', None):
                raise UserError(_('Elige el archivo del programa.'))
            result = event.sudo().scout_plan_from_file(file.filename, file.read())
            payload = {'ok': True, **result}
        except (UserError, AccessError, ValidationError) as e:
            payload = {'ok': False, 'error': str(e.args[0] if e.args else e)}
        return request.make_json_response(payload)

    @http.route(['/dirigentes/api/plan/guardar'], type='jsonrpc', auth='user')
    def leader_api_plan_save(self, section_id, event_id, lines, dictation=None, confirm_approved=False, **kw):
        try:
            section = self._svc()._section(section_id)
            event, editable = self._plan_event(section, event_id)
            if not editable:
                raise AccessError(_('Esta actividad la edita la jefatura.'))
            saved = event.scout_plan_save(lines, dictation, bool(confirm_approved))
            self._svc()._note(event, _('Programa de la sesión guardado (%s actividades)', len(saved)))
            return {'ok': True, 'count': len(saved), 'lines': event._scout_plan_lines_dicts()}
        except (UserError, AccessError, ValidationError) as e:
            return {'ok': False, 'error': str(e.args[0] if e.args else e)}

    @http.route(['/dirigentes/<int:section_id>/actividad/<int:event_id>/programa.pdf',
                 '/dirigentes/<int:section_id>/actividad/<int:event_id>/programa.xlsx'], type='http', auth='user',
                website=True)
    def leader_session_plan_file(self, section_id, event_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        try:
            event, _editable = self._plan_event(section, event_id)
        except AccessError:
            return request.redirect(f'/dirigentes/{section.id}/plan')
        name = f'Programa {section.display_name} {event.scout_date or ""}'
        if request.httprequest.path.endswith('.xlsx'):
            content = event.scout_plan_xlsx()
            ctype, ext = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'xlsx'
        else:
            content, _type = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
                'scout_management.action_report_session_plan', event.ids)
            ctype, ext = 'application/pdf', 'pdf'
        headers = [('Content-Type', ctype), ('Content-Length', len(content)),
                   ('Content-Disposition', f'inline; filename="{name}.{ext}"')]
        return request.make_response(content, headers=headers)

    # ------------------------------------------------------------------ nuevos ingresos y reinscripción
    @http.route(['/dirigentes/<int:section_id>/reinscripcion', '/dirigentes/<int:section_id>/nuevos'], type='http',
                auth='user', website=True, methods=['GET'])
    def leader_enrollment(self, section_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        mode = 'new' if request.httprequest.path.rstrip('/').endswith('/nuevos') else 'renew'
        data = self._svc().enrollment(section)
        return self._render('scout_management.leader_enrollment', section, 'members', mode=mode, enroll=data,
                            groups=data[mode])

    # ------------------------------------------------------------------ reingresos
    @http.route(['/dirigentes/<int:section_id>/reingreso'], type='http', auth='user', website=True, methods=['GET'])
    def leader_returns(self, section_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        return self._render('scout_management.leader_returns', section, 'members',
                            former=self._svc().former_members(section))

    @http.route(['/dirigentes/<int:section_id>/reingreso/<int:member_id>'], type='http', auth='user', website=True,
                methods=['POST'])
    def leader_return_request(self, section_id, member_id, **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        try:
            svc.request_return(section, member_id, post.get('reason'))
        except (UserError, AccessError, ValidationError) as e:
            return self._back(f'/dirigentes/{section.id}/reingreso', error=str(e.args[0]))
        reg = svc.registration(request.env['res.partner'].sudo().browse(member_id))
        if reg and reg.state == 'validated':
            return self._back(f'/dirigentes/{section.id}/reingreso',
                              ok=_('Reingreso solicitado al jefe de grupo. Su ficha de este ciclo ya estaba validada, '
                                   'así que no hace falta llenarla otra vez.'))
        return self._back(f'/dirigentes/{section.id}/miembro/{member_id}/registro',
                          ok=_('Reingreso solicitado al jefe de grupo. Llena la ficha con la INE y la firma del tutor.'))

    # ------------------------------------------------------------------ rutas de enlace y permanencia
    @http.route(['/dirigentes/<int:section_id>/rutas'], type='http', auth='user', website=True, methods=['GET'])
    def leader_routes(self, section_id, ver='mia', **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        if section.is_leadership:
            groups = svc.group_routes()
            return self._render('scout_management.leader_group_routes', section, 'members', groups=groups,
                                seccion=int(kw.get('seccion') or 0), today=svc._today().isoformat(), svc=svc)
        return self._render('scout_management.leader_routes', section, 'members', ver='recibo' if ver == 'recibo' else 'mia',
                            routes=svc.routes(section), incoming=svc.incoming_routes(section),
                            today=svc._today().isoformat(), svc=svc)

    @http.route(['/dirigentes/<int:section_id>/rutas/iniciar'], type='http', auth='user', website=True,
                methods=['POST'])
    def leader_route_start(self, section_id, **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        url = f'/dirigentes/{section.id}/rutas'
        try:
            route = self._svc().start_route(section, post.get('route_id'), post.get('date') or None)
        except (UserError, AccessError, ValidationError) as e:
            return self._back(url, error=str(e.args[0]))
        return self._back(url, ok=_('Ruta de enlace iniciada para %s. Se avisó a los jefes de ambas secciones.',
                                    route.member_id.name))

    @http.route(['/dirigentes/<int:section_id>/rutas/pase'], type='http', auth='user', website=True,
                methods=['POST'])
    def leader_route_pass(self, section_id, **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        url = f'/dirigentes/{section.id}/rutas'
        try:
            member, dest = self._svc().give_pass(section, post.get('route_id'), post.get('destination_id'),
                                                 post.get('date') or None)
        except (UserError, AccessError, ValidationError) as e:
            return self._back(url, error=str(e.args[0]))
        return self._back(url, ok=_('%(name)s pasó a %(dest)s. Se avisó a sus nuevos jefes.',
                                    name=member.name, dest=dest.name))

    @http.route(['/dirigentes/<int:section_id>/rutas/recibir'], type='http', auth='user', website=True,
                methods=['POST'])
    def leader_route_receive(self, section_id, **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        try:
            member = self._svc().receive_member(section, post.get('route_id'))
        except (UserError, AccessError, ValidationError) as e:
            return self._back(f'/dirigentes/{section.id}/rutas?ver=recibo', error=str(e.args[0]))
        return self._back(f'/dirigentes/{section.id}/equipos',
                          ok=_('%(name)s ya es parte de %(section)s. Asígnale su %(team)s cuando corresponda.',
                               name=member.name, section=section.display_name,
                               team=(section.patrol_label or _('equipo')).lower()))

    # ------------------------------------------------------------------ ligas para las familias
    @http.route(['/dirigentes/<int:section_id>/liga'], type='http', auth='user', website=True)
    def leader_family_link(self, section_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        from ..models.scout_online_payment import scout_qr_data_uri
        link = request.env['res.partner'].sudo()._scout_signup_url(section)
        text = _('Hola, esta es la liga para inscribir a tu hijo(a) en %(group)s: llenas la ficha, firmas, adjuntas tu '
                 'INE y eliges pagar en línea o en efectivo. %(link)s', group=request.env.company.name, link=link)
        return self._render('scout_management.leader_family_link', section, 'members', link=link,
                            qr=scout_qr_data_uri(link), whatsapp=f'https://wa.me/?text={quote(text)}')

    @http.route(['/dirigentes/<int:section_id>/miembro/<int:member_id>/pago/efectivo'], type='http', auth='user',
                website=True, methods=['POST'])
    def leader_member_cash(self, section_id, member_id, **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        try:
            member = svc._member(member_id, section)
            reg = svc.registration(member)
            if not reg:
                raise UserError(_('Primero llena la ficha con la firma del tutor.'))
            reg._scout_set_payment_method('cash')
            svc._note(reg, _('La familia pagará en efectivo'))
        except (UserError, AccessError) as e:
            return self._back(f'/dirigentes/{section.id}/miembro/{member_id}/pago', error=str(e.args[0]))
        return self._back(f'/dirigentes/{section.id}/miembro/{member_id}/pago',
                          ok=_('Anotado: pagará en efectivo. Tesorería registra el pago al recibirlo.'))

    @http.route(['/dirigentes/<int:section_id>/equipos/<int:patrol_id>/editar'], type='http', auth='user',
                website=True, methods=['POST'])
    def leader_team_edit(self, section_id, patrol_id, **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        try:
            self._svc().edit_patrol(section, patrol_id, post.get('name'), post.get('color_hex'))
        except (UserError, AccessError, ValidationError) as e:
            return self._back(f'/dirigentes/{section.id}/equipos', error=str(e.args[0]))
        return self._back(f'/dirigentes/{section.id}/equipos', ok=_('Equipo actualizado.'))

    # ------------------------------------------------------------------ equipos
    @http.route(['/dirigentes/<int:section_id>/equipos'], type='http', auth='user', website=True, methods=['GET'])
    def leader_teams(self, section_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        if section.is_leadership:
            return self._render('scout_management.leader_org_chart', section, 'members', org=svc.org_chart())
        return self._render('scout_management.leader_teams', section, 'members', board=svc.team_board(section),
                            arrivals=svc.recent_arrivals(section))

    @http.route(['/dirigentes/<int:section_id>/equipos/nuevo'], type='http', auth='user', website=True,
                methods=['POST'])
    def leader_team_create(self, section_id, **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        url = f'/dirigentes/{section.id}/equipos'
        try:
            self._svc().create_patrol(section, post.get('name'), post.get('color'))
        except (UserError, AccessError, ValidationError) as e:
            return self._back(url, error=str(e.args[0]))
        return self._back(url, ok=_('Equipo creado.'))

    @http.route(['/dirigentes/api/equipo'], type='jsonrpc', auth='user')
    def leader_api_team(self, section_id, member_id, patrol_id=None, role=None, **kw):
        svc = self._svc()
        try:
            section = svc._section(section_id)
            member = svc._member(member_id, section)
            if patrol_id is not None:
                member = svc.move_member(section, member_id, patrol_id or False)
            if role:
                member = svc.set_role(section, member_id, role)
            changed = request.env['res.partner'].sudo().search(
                [('scout_patrol_id', '=', member.scout_patrol_id.id)]) if member.scout_patrol_id else member
            return {'ok': True, 'patrol_id': member.scout_patrol_id.id or 0,
                    'roles': {str(m.id): svc.role_label(section, m) for m in changed | member}}
        except (UserError, AccessError, ValidationError) as e:
            return {'ok': False, 'error': str(e.args[0] if e.args else e)}

    # ------------------------------------------------------------------ recursos para dirigentes
    def _resource_or_404(self, section, resource_id):
        resource = request.env['scout.resource']._scout_for_section(section).filtered(lambda r: r.id == int(resource_id))
        return resource[:1]

    @http.route(['/dirigentes/<int:section_id>/recursos'], type='http', auth='user', website=True)
    def leader_resources(self, section_id, categoria=None, tipo=None, q=None, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        if section.is_leadership and not (categoria or tipo or q):
            groups = self._svc().group_resources()
            sel = int(kw.get('seccion') or 0) if str(kw.get('seccion') or '').isdigit() else 0
            return self._render('scout_management.leader_group_resources', section, 'resources', groups=groups,
                                selected=sel, general=request.env['scout.resource']._scout_for_section(section))
        everything = request.env['scout.resource']._scout_for_section(section)
        resources = everything
        category = request.env['scout.resource.category'].sudo().browse(int(categoria)).exists() \
            if str(categoria or '').isdigit() else request.env['scout.resource.category']
        if category:
            resources = resources.filtered(lambda r: r.category_id == category)
        if tipo:
            resources = resources.filtered(lambda r: r.kind == tipo)
        if q:
            needle = q.strip().lower()
            resources = resources.filtered(lambda r: needle in (r.name or '').lower()
                                           or needle in (r.description or '').lower()
                                           or needle in (r.category_id.name or '').lower())
        categories = everything.category_id.sorted(lambda c: (c.sequence, c.name or ''))
        groups = []
        for cat in categories:
            items = resources.filtered(lambda r: r.category_id == cat)
            if items:
                groups.append((cat, items))
        loose = resources.filtered(lambda r: not r.category_id)
        if loose:
            groups.append((request.env['scout.resource.category'], loose))
        counts = {cat.id: len(everything.filtered(lambda r: r.category_id == cat)) for cat in categories}
        return self._render('scout_management.leader_resources', section, 'resources', groups=groups,
                            categories=categories, counts=counts, category=category, tipo=tipo or '', q=q or '',
                            pinned=everything.filtered('pinned') if not (category or tipo or q) else everything.browse(),
                            total=len(everything), found=len(resources))

    @http.route(['/dirigentes/<int:section_id>/recursos/<int:resource_id>'], type='http', auth='user', website=True)
    def leader_resource(self, section_id, resource_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        resource = self._resource_or_404(section, resource_id)
        if not resource:
            return request.redirect(f'/dirigentes/{section.id}/recursos')
        return self._render('scout_management.leader_resource', section, 'resources', resource=resource)

    @http.route(['/dirigentes/<int:section_id>/recursos/<int:resource_id>/archivo'], type='http', auth='user')
    def leader_resource_file(self, section_id, resource_id, descargar=None, **kw):
        try:
            section = self._svc()._section(section_id)
        except (AccessError, MissingError):
            return request.not_found()
        resource = self._resource_or_404(section, resource_id)
        if not resource or not resource.file:
            return request.not_found()
        stream = request.env['ir.binary']._get_stream_from(
            resource.sudo(), 'file', filename=resource.file_name or resource.name,
            mimetype=resource._scout_mimetype())
        return stream.get_response(as_attachment=bool(descargar))

    # ------------------------------------------------------------------ cuadro de logros
    @http.route(['/dirigentes/<int:section_id>/logros'], type='http', auth='user', website=True)
    def leader_achievements(self, section_id, ver='adelantos', **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        if section.is_leadership:
            return self._group_progress_page(section, kw.get('seccion'))
        categories = svc.specialty_categories(section)
        columns, title, current = svc.path_stages(section), _('Adelantos'), 'adelantos'
        if ver == 'colores':
            spec_categories, spec_rows = svc.specialty_summary(section)
            return self._render('scout_management.leader_achievements', section, 'progress', columns=columns,
                                title=_('Especialidades por colores y técnicas'), ver='colores',
                                categories=categories, rows=[], spec_categories=spec_categories,
                                spec_rows=spec_rows, level_colors=svc.LEVEL_COLORS)
        if ver and ver.startswith('cat'):
            key = ver[3:]
            for category, stages in categories:
                if str(category.id or 0) == key:
                    columns, title, current = stages, category.name or _('Otras especialidades'), ver
                    break
        return self._render('scout_management.leader_achievements', section, 'progress', columns=columns,
                            title=title, ver=current, categories=categories,
                            rows=svc.achievements(section, columns), spec_categories=[], spec_rows=[],
                            level_colors=svc.LEVEL_COLORS)

    @http.route(['/dirigentes/api/logro'], type='jsonrpc', auth='user')
    def leader_api_achievement(self, section_id, member_id, stage_id, **kw):
        svc = self._svc()
        try:
            section = svc._section(section_id)
            return {'ok': True, **svc.toggle_progress(section, member_id, stage_id)}
        except (UserError, AccessError, ValidationError) as e:
            return {'ok': False, 'error': str(e.args[0] if e.args else e)}

    # ------------------------------------------------------------------ nuevos ingresos
    @http.route(['/dirigentes/<int:section_id>/nuevo'], type='http', auth='user', website=True, methods=['GET'])
    def leader_new_member(self, section_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        return self._render('scout_management.leader_new_member', section, 'members',
                            patrols=section.sudo().patrol_ids, values=kw)

    @http.route(['/dirigentes/<int:section_id>/nuevo'], type='http', auth='user', website=True, methods=['POST'])
    def leader_new_member_save(self, section_id, **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        photo = post.get('photo_data') or post.get('photo')
        try:
            member = svc.create_member(section, post, photo)
        except (UserError, AccessError, ValidationError) as e:
            values = {k: v for k, v in post.items() if isinstance(v, str) and k not in ('photo_data', 'csrf_token')}
            return self._render('scout_management.leader_new_member', section, 'members',
                                patrols=section.sudo().patrol_ids, values=values, error=str(e.args[0]))
        suggested = request.env['scout.section'].sudo()._scout_suggest(member.scout_birthdate, member.scout_gender)
        if suggested and suggested != section:
            svc._note(member, _('Por su edad y sexo le corresponde %s.', suggested.display_name))
            return self._back(f'/dirigentes/{section.id}/miembro/{member.id}',
                              error=_('Ojo: por su edad y sexo a %(name)s le corresponde %(sec)s. Pide a secretaría '
                                      'el cambio de sección si aplica.', name=member.name, sec=suggested.display_name))
        if post.get('then') == 'registro':
            return request.redirect(f'/dirigentes/{section.id}/miembro/{member.id}/registro')
        return self._back(f'/dirigentes/{section.id}/miembro/{member.id}',
                          ok=_('%s ya aparece en el pase de lista (en prueba).', member.name))

    @http.route(['/dirigentes/<int:section_id>/miembro/<int:member_id>/registro'], type='http', auth='user',
                website=True, methods=['GET', 'POST'])
    def leader_member_register(self, section_id, member_id, **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        try:
            member = svc._member(member_id, section)
        except AccessError:
            return request.redirect(f'/dirigentes/{section.id}/miembros')
        error = None
        if request.httprequest.method == 'POST':
            try:
                if post.get('photo_data'):
                    svc.set_photo(section, member.id, post['photo_data'])
                svc.register_member(section, member, post)
            except (UserError, AccessError, ValidationError) as e:
                error = str(e.args[0])
            else:
                return request.redirect(f'/dirigentes/{section.id}/miembro/{member.id}/pago')
        cycle = request.env['scout.cycle'].sudo()._get_registration(section.company_id)
        return self._render('scout_management.leader_member_register', section, 'members', member=member,
                            cycle=cycle, registration=svc.registration(member), error=error)

    @http.route(['/dirigentes/<int:section_id>/miembro/<int:member_id>/pago'], type='http', auth='user', website=True)
    def leader_member_payment(self, section_id, member_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        try:
            member = svc._member(member_id, section)
        except AccessError:
            return request.redirect(f'/dirigentes/{section.id}/miembros')
        try:
            pay = svc.payment_info(member)
        except UserError as e:
            pay, kw['error'] = {}, str(e.args[0])
        return self._render('scout_management.leader_member_payment', section, 'members', member=member, pay=pay)

    @http.route(['/dirigentes/<int:section_id>/miembro/<int:member_id>/foto'], type='http', auth='user', website=True,
                methods=['POST'])
    def leader_member_photo(self, section_id, member_id, **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        url = f'/dirigentes/{section.id}/miembro/{member_id}'
        try:
            self._svc().set_photo(section, member_id, post.get('photo_data') or post.get('photo'))
        except (UserError, AccessError, ValidationError) as e:
            return self._back(url, error=str(e.args[0]))
        return self._back(url, ok=_('Foto guardada.'))

    @http.route(['/dirigentes/<int:section_id>/miembro/<int:member_id>/credencial'], type='http', auth='user',
                website=True)
    def leader_member_card(self, section_id, member_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        try:
            member = self._svc()._member(member_id, section)
        except AccessError:
            return request.redirect(f'/dirigentes/{section.id}/miembros')
        return self._render('scout_management.leader_member_card', section, 'members', member=member,
                            card=_card_values(member))

    @http.route(['/dirigentes/<int:section_id>/miembro/<int:member_id>/credencial.pdf'], type='http', auth='user',
                website=True)
    def leader_member_card_pdf(self, section_id, member_id, **kw):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        try:
            member = self._svc()._member(member_id, section)
        except AccessError:
            return request.redirect(f'/dirigentes/{section.id}/miembros')
        pdf, _type = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
            'scout_management.action_report_member_card', member.ids)
        headers = [('Content-Type', 'application/pdf'), ('Content-Length', len(pdf)),
                   ('Content-Disposition', f'inline; filename="Credencial {member.name}.pdf"')]
        return request.make_response(pdf, headers=headers)

    # ------------------------------------------------------------------ calendario general del grupo
    @http.route(['/dirigentes/calendario', '/dirigentes/<int:section_id>/calendario'], type='http', auth='user',
                website=True)
    def leader_group_calendar(self, section_id=None, mes=None, **kw):
        svc = self._svc()
        section = None
        if section_id:
            section, redirect = self._section_or_redirect(section_id)
            if redirect:
                return redirect
        elif not svc._sections():
            return request.redirect('/dirigentes')
        try:
            year, month = (int(x) for x in (mes or '').split('-')[:2])
            month_start = date(year, month, 1)
        except (ValueError, TypeError):
            month_start = svc._today().replace(day=1)
        ver = kw.get('ver') if kw.get('ver') in ('grupo', 'juntas') else 'todo'
        cal = svc.general_calendar(month_start, ver)
        prev_start = date(month_start.year - (month_start.month == 1), (month_start.month - 2) % 12 + 1, 1)
        next_start = date(month_start.year + (month_start.month == 12), month_start.month % 12 + 1, 1)
        all_sections = request.env['scout.section'].sudo().search([('is_leadership', '=', False)])
        return self._render('scout_management.leader_group_calendar', section, 'plan', cal=cal,
                            month_label=f'{MONTHS[month_start.month - 1].capitalize()} {month_start.year}',
                            prev_mes=prev_start.strftime('%Y-%m'), next_mes=next_start.strftime('%Y-%m'),
                            all_sections=all_sections, today=svc._today(), ver=ver,
                            can_import=svc._is_group_manager(), type_labels=dict(ACTIVITY_TYPES),
                            base_url=f'/dirigentes/{section.id}/calendario' if section else '/dirigentes/calendario')

    # ------------------------------------------------------------------ jefatura: subir el calendario del grupo
    @http.route(['/dirigentes/<int:section_id>/calendario/subir'], type='http', auth='user', website=True,
                methods=['GET', 'POST'])
    def leader_calendar_upload(self, section_id, file=None, **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        if not svc._is_group_manager():
            return self._back(f'/dirigentes/{section.id}/calendario', error=_('El calendario del grupo lo sube la jefatura.'))
        values = {'events': [], 'notes': [], 'source': '', 'type_labels': dict(ACTIVITY_TYPES),
                  'type_options': [t for t in ACTIVITY_TYPES if t[0] != 'regular']}
        if request.httprequest.method == 'POST' and file and getattr(file, 'filename', None):
            try:
                data = request.env['calendar.event']._scout_calendar_parse(file.filename, file.read())
                values.update(data, events_json=json.dumps(data['events']))
            except (UserError, ValidationError) as e:
                values['error'] = str(e.args[0] if e.args else e)
        return self._render('scout_management.leader_calendar_upload', section, 'plan', **values)

    @http.route(['/dirigentes/<int:section_id>/calendario/agregar'], type='http', auth='user', website=True,
                methods=['POST'])
    def leader_calendar_add(self, section_id, events_json='[]', **post):
        section, redirect = self._section_or_redirect(section_id)
        if redirect:
            return redirect
        svc = self._svc()
        if not svc._is_group_manager():
            return request.redirect(f'/dirigentes/{section.id}/calendario')
        try:
            events = json.loads(events_json or '[]')
        except ValueError:
            events = []
        form = request.httprequest.form
        chosen = []
        for idx, ev in enumerate(events):
            if not form.get(f'sel_{idx}'):
                continue
            ev['title'] = (form.get(f'title_{idx}') or ev.get('title') or '').strip()
            ev['type'] = form.get(f'type_{idx}') or ev.get('type')
            chosen.append(ev)
        created = request.env['calendar.event']._scout_calendar_create(chosen)
        first = min(created.mapped('scout_date')) if created else svc._today()
        return self._back(f'/dirigentes/{section.id}/calendario?mes={first.strftime("%Y-%m")}',
                          ok=_('%s actividad(es) agregadas al calendario del grupo.', len(created)))


def _card_values(member):
    """Datos de la credencial electrónica (pantalla y PDF)."""
    member = member.sudo()
    reg, cycle = member._scout_card_info()
    return {
        'valid': member._scout_card_valid(),
        'folio': reg.name if reg and member.scout_type == 'youth' else '',
        'cycle': cycle,
        'logo': member._scout_card_logo(),
        'qr': member._scout_card_qr(),
        'company': member.company_id or request.env.company,
    }


class ScoutCardPublic(http.Controller):

    @http.route(['/scout/credencial/<int:member_id>/<string:token>'], type='http', auth='public', website=True)
    def scout_card_verify(self, member_id, token, **kw):
        """Verificación pública de la credencial (al escanear su QR): solo nombre, sección y vigencia."""
        from odoo.tools import consteq
        member = request.env['res.partner'].sudo().browse(member_id).exists()
        if not member or not member.is_scout or not member.scout_card_token or not consteq(token, member.scout_card_token):
            return request.not_found()
        _reg, cycle = member._scout_card_info()
        return request.render('scout_management.scout_card_verify', {
            'member': member, 'valid': member._scout_card_valid(), 'cycle': cycle, 'logo': member._scout_card_logo(),
            'company': member.company_id or request.env.company, 'today': date.today()})


class ScoutLeaderHome(Home):

    def _login_redirect(self, uid, redirect=None):
        """Los dirigentes de portal llegan directo a su portal al iniciar sesión."""
        if not redirect:
            user = request.env['res.users'].sudo().browse(uid)
            if user.share and user._scout_is_leader():
                redirect = '/dirigentes'
        return super()._login_redirect(uid, redirect=redirect)
