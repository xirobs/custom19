# -*- coding: utf-8 -*-
"""Portal de dirigentes: lo que un jefe de sección captura desde el celular, sin entrar al Odoo completo.

Los jefes de sección son usuarios de portal (gratuitos). Cada método valida que la sección, actividad,
miembro o cargo pertenezca a las secciones del dirigente y luego trabaja con sudo, dejando en el historial
quién hizo el movimiento. Secretaría, tesorería y jefatura siguen trabajando en Odoo con acceso completo.
"""
from datetime import date, datetime, time, timedelta

from dateutil.relativedelta import relativedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .calendar_event import DEFAULT_TZ
from .scout_common import ACTIVITY_TYPES, quarter_bounds

QUICK_STATUSES = ('present', 'absent', 'camp', 'outing', 'na')
LEADER_ITEM_TYPES = ('status', 'transition', 'previous', 'objective', 'activity', 'need', 'key', 'progress',
                     'rescue', 'next')


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _scout_leader_sections(self):
        """Secciones que atiende el usuario (vacío = no es dirigente)."""
        self.ensure_one()
        Section = self.env['scout.section'].sudo()
        if not self.share and self.has_group('scout_management.group_scout_officer'):
            return Section.search([])
        sections = Section.search([('leader_ids', 'in', self.id)])
        sections |= self.partner_id.sudo().scout_leading_section_ids
        return sections.filtered('active')

    def _scout_is_leader(self):
        self.ensure_one()
        return bool(self._scout_leader_sections())


class ResPartnerLeaderPortal(models.Model):
    _inherit = 'res.partner'

    def action_scout_grant_leader_portal(self):
        """Da acceso al portal de dirigentes (usuario de portal, sin costo de licencia) y lo liga a sus secciones."""
        for partner in self:
            if partner.scout_type != 'leader':
                raise UserError(_('%s no está marcado como dirigente.', partner.name))
            if not partner.scout_leading_section_ids and not partner.scout_section_id:
                raise UserError(_('Indica las "Secciones a cargo" de %s.', partner.name))
            if not partner.email:
                raise UserError(_('%s necesita un correo para recibir su acceso.', partner.name))
            user = partner.with_context(active_test=False).user_ids[:1]
            if not user:
                wizard = self.env['portal.wizard'].sudo().with_context(
                    active_model='res.partner', active_ids=partner.ids).create({})
                wizard.user_ids.action_grant_access()
                user = partner.with_context(active_test=False).user_ids[:1]
            elif not user.active:
                user.sudo().active = True
            if not user.share and not user.has_group('scout_management.group_scout_officer'):
                # Jefe de sección que era usuario interno: pasa a portal (solo ve /dirigentes, sin licencia).
                user.sudo().write({'group_ids': [fields.Command.set([self.env.ref('base.group_portal').id])]})
                partner.message_post(body=_('El usuario %s pasó de usuario interno a usuario de portal.', user.login))
            partner._scout_sync_leader_sections()
            partner.message_post(body=_('Acceso al portal de dirigentes otorgado (%s).', user.login))
        # Sin depender del correo: liga para crear contraseña (WhatsApp / QR) o contraseña asignada aquí.
        wizard = self.env['scout.leader.access.wizard'].create({'partner_id': self[:1].id})
        return wizard._open()

    def _scout_sync_leader_sections(self):
        """Mantiene "Dirigentes con acceso" de cada sección igual a las "Secciones a cargo" del contacto."""
        for partner in self.sudo():
            users = partner.with_context(active_test=False).user_ids
            if not users or partner.scout_type != 'leader':
                continue
            wanted = partner.scout_leading_section_ids
            if not wanted and partner.scout_section_id:
                wanted = partner.scout_section_id
            for user in users:
                current = self.env['scout.section'].sudo().search([('leader_ids', 'in', user.id)])
                for section in wanted - current:
                    section.leader_ids = [fields.Command.link(user.id)]
                if user.share:  # a los usuarios internos no se les quita nada en automático
                    for section in current - wanted:
                        section.leader_ids = [fields.Command.unlink(user.id)]

    def write(self, vals):
        res = super().write(vals)
        if 'scout_leading_section_ids' in vals or 'scout_section_id' in vals:
            self.filtered(lambda p: p.is_scout and p.scout_type == 'leader' and p.user_ids)._scout_sync_leader_sections()
        return res


class ScoutLeaderAccessWizard(models.TransientModel):
    _name = 'scout.leader.access.wizard'
    _description = 'Acceso al portal de dirigentes'

    partner_id = fields.Many2one('res.partner', string='Dirigente', required=True, readonly=True)
    login = fields.Char(string='Usuario', readonly=True)
    phone = fields.Char(string='WhatsApp')
    link = fields.Char(string='Liga para crear su contraseña', readonly=True,
                       help='Válida 6 días o hasta que entre por primera vez.')
    qr_code = fields.Binary(string='QR', readonly=True)
    portal_url = fields.Char(string='Dirección del portal', readonly=True)
    password = fields.Char(string='O asigna una contraseña')

    @api.model_create_multi
    def create(self, vals_list):
        wizards = super().create(vals_list)
        from .scout_online_payment import scout_qr_png
        import base64
        for wiz in wizards:
            partner = wiz.partner_id.sudo()
            user = partner.with_context(active_test=False).user_ids[:1]
            partner.signup_prepare(signup_type='signup')
            link = partner.with_context(signup_valid=True)._get_signup_url_for_action(url='/dirigentes')[partner.id]
            base = partner.get_base_url().rstrip('/')
            wiz.write({'login': user.login, 'phone': partner.phone or (partner['mobile'] if 'mobile' in partner._fields else False),
                       'link': link, 'qr_code': base64.b64encode(scout_qr_png(link)),
                       'portal_url': f'{base}/dirigentes'})
        return wizards

    def _open(self):
        return {'type': 'ir.actions.act_window', 'name': _('Acceso al portal de dirigentes'), 'res_model': self._name,
                'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    def action_whatsapp(self):
        self.ensure_one()
        from urllib.parse import quote
        from .scout_online_payment import scout_whatsapp_phone
        phone = scout_whatsapp_phone(self.phone)
        if not phone:
            raise UserError(_('Escribe el número de WhatsApp del dirigente.'))
        text = _('Hola %(name)s, ya tienes acceso al Portal de dirigentes de %(group)s. '
                 'Entra a esta liga para crear tu contraseña: %(link)s\n'
                 'Tu usuario es: %(login)s\nDespués entras en: %(url)s',
                 name=self.partner_id.name, group=self.env.company.name, link=self.link,
                 login=self.login, url=self.portal_url)
        self.partner_id.message_post(body=_('Liga de acceso al portal de dirigentes enviada por WhatsApp a %s.', phone))
        return {'type': 'ir.actions.act_url', 'url': f'https://wa.me/{phone}?text={quote(text)}', 'target': 'new'}

    def action_set_password(self):
        self.ensure_one()
        if not self.password or len(self.password) < 6:
            raise UserError(_('La contraseña debe tener al menos 6 caracteres.'))
        user = self.partner_id.sudo().with_context(active_test=False).user_ids[:1]
        user.write({'password': self.password})
        self.partner_id.message_post(body=_('Contraseña del portal de dirigentes asignada por %s.', self.env.user.name))
        login, url = self.login, self.portal_url
        self.password = False
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'type': 'success', 'sticky': True, 'title': _('Contraseña asignada'),
                           'message': _('Usuario: %(login)s — entra en %(url)s', login=login, url=url),
                           'next': {'type': 'ir.actions.act_window_close'}}}


class ScoutLeaderPortal(models.AbstractModel):
    _name = 'scout.leader.portal'
    _description = 'Portal de dirigentes'

    # ------------------------------------------------------------------ acceso
    @api.model
    def _sections(self):
        return self.env.user._scout_leader_sections()

    @api.model
    def _section(self, section_id):
        section = self.env['scout.section'].sudo().browse(int(section_id or 0)).exists()
        if not section or section not in self._sections():
            raise AccessError(_('No tienes acceso a esa sección.'))
        return section

    @api.model
    def _members(self, section, include_leaders=False):
        domain = [('is_scout', '=', True), ('scout_state', 'in', ('trial', 'active'))]
        Partner = self.env['res.partner'].sudo()
        members = Partner.search(domain + [('scout_section_id', '=', section.id), ('scout_type', '=', 'youth')])
        if include_leaders:
            members |= Partner.search(domain + ['|', ('scout_leading_section_ids', 'in', section.ids),
                                                ('scout_section_id', '=', section.id), ('scout_type', '=', 'leader')])
        return members.sorted(lambda p: (p.scout_type == 'leader', p.scout_patrol_id.sequence or 0,
                                         p.scout_patrol_id.name or '', p.name or ''))

    @api.model
    def _member(self, member_id, section=None):
        member = self.env['res.partner'].sudo().browse(int(member_id or 0)).exists()
        sections = section or self._sections()
        if not member or not member.is_scout or not (
                member.scout_section_id in sections or member.scout_leading_section_ids & sections):
            raise AccessError(_('No tienes acceso a ese miembro.'))
        return member

    @api.model
    def _event(self, event_id):
        event = self.env['calendar.event'].sudo().browse(int(event_id or 0)).exists()
        if not event or not event.is_scout_event:
            raise AccessError(_('La actividad no existe.'))
        sections = self._sections()
        if event.scout_scope == 'section' and not (event.scout_section_ids & sections):
            raise AccessError(_('No tienes acceso a esa actividad.'))
        return event

    @api.model
    def _event_editable(self, event):
        """Un dirigente edita las actividades de su(s) sección(es); las de todo el grupo solo si las creó."""
        sections = self._sections()
        if self.env.user.has_group('scout_management.group_scout_officer') and not self.env.user.share:
            return True
        if event.scout_scope == 'section':
            return bool(event.scout_section_ids) and not (event.scout_section_ids - sections)
        return event.create_uid == self.env.user

    def _note(self, record, body):
        record.sudo().message_post(body=_('%(body)s — %(user)s (portal de dirigentes)', body=body,
                                          user=self.env.user.name),
                                   author_id=self.env.user.partner_id.id)

    # ------------------------------------------------------------------ fechas
    @api.model
    def _tz(self):
        return pytz.timezone(self.env.user.tz or DEFAULT_TZ)

    @api.model
    def _today(self):
        return datetime.now(self._tz()).date()

    @api.model
    def _to_utc(self, day, hour_float):
        hours = int(hour_float)
        minutes = int(round((hour_float - hours) * 60))
        local = self._tz().localize(datetime.combine(day, time(hours, minutes)))
        return local.astimezone(pytz.utc).replace(tzinfo=None)

    @api.model
    def _local(self, dt):
        return pytz.utc.localize(dt).astimezone(self._tz()) if dt else None

    # ------------------------------------------------------------------ actividades
    @api.model
    def _events_domain(self, section):
        return [('is_scout_event', '=', True),
                '|', ('scout_section_ids', 'in', section.ids), ('scout_scope', '=', 'group')]

    @api.model
    def today_event(self, section):
        return self.env['calendar.event'].sudo().search(
            self._events_domain(section) + [('scout_date', '=', self._today())], order='start', limit=1)

    @api.model
    def events(self, section, upcoming=True, limit=20):
        today = self._today()
        domain = self._events_domain(section) + [('scout_date', '>=' if upcoming else '<', today)]
        return self.env['calendar.event'].sudo().search(domain, order='start %s' % ('asc' if upcoming else 'desc'),
                                                        limit=limit)

    @api.model
    def create_today_session(self, section):
        today = self._today()
        existing = self.today_event(section)
        if existing:
            return existing
        return self.save_event(section, None, {
            'name': _('Sesión %s', section.display_name), 'date': today.isoformat(),
            'time_from': '10:00', 'time_to': '13:00', 'activity_type': 'regular'})

    @api.model
    def _parse_time(self, value, default):
        try:
            hh, mm = (value or default).split(':')[:2]
            return int(hh) + int(mm) / 60.0
        except (ValueError, AttributeError):
            hh, mm = default.split(':')
            return int(hh) + int(mm) / 60.0

    @api.model
    def save_event(self, section, event, post):
        """Crea o edita una actividad de la sección con lo que llena el dirigente."""
        if event and not self._event_editable(event):
            raise AccessError(_('Esta actividad la edita la jefatura.'))
        name = (post.get('name') or '').strip()
        if not name:
            raise UserError(_('Escribe el nombre de la actividad.'))
        try:
            day = fields.Date.to_date(post.get('date')) or self._today()
        except ValueError:
            raise UserError(_('La fecha no es válida.'))
        start_h = self._parse_time(post.get('time_from'), '10:00')
        stop_h = self._parse_time(post.get('time_to'), '13:00')
        if stop_h <= start_h:
            stop_h = start_h + 1
        activity_type = post.get('activity_type') or 'regular'
        if activity_type not in dict(ACTIVITY_TYPES):
            activity_type = 'regular'
        leaders = self._members(section, include_leaders=True).filtered(lambda p: p.scout_type == 'leader')
        responsible_ids = [int(x) for x in (post.get('responsible_ids') or []) if str(x).isdigit()]
        vals = {
            'name': name,
            'start': self._to_utc(day, start_h),
            'stop': self._to_utc(day, stop_h),
            'allday': False,
            'scout_activity_type': activity_type,
            'scout_theme': post.get('theme') or False,
            'scout_objective': post.get('objective') or False,
            'location': post.get('location') or False,
            'scout_recognitions': post.get('recognitions') or False,
            'description': post.get('description') or False,
            'scout_responsible_ids': [fields.Command.set(leaders.filtered(lambda p: p.id in responsible_ids).ids)],
        }
        if 'done' in post or event:
            vals['scout_done'] = bool(post.get('done'))
        if 'result' in post:
            vals['scout_result'] = post.get('result') or False
        Event = self.env['calendar.event'].sudo().with_context(
            no_mail_to_attendees=True, dont_notify=True, mail_create_nosubscribe=True)
        if event:
            event.with_context(no_mail_to_attendees=True, dont_notify=True).write(vals)
            self._note(event, _('Actividad actualizada'))
            return event
        vals.update({'is_scout_event': True, 'scout_scope': 'section',
                     'scout_section_ids': [fields.Command.set(section.ids)],
                     'user_id': self.env.uid, 'partner_ids': [fields.Command.clear()],
                     'privacy': 'public', 'show_as': 'busy'})
        event = Event.create(vals)
        self._note(event, _('Actividad creada'))
        return event

    # ------------------------------------------------------------------ asistencia
    @api.model
    def attendance_lines(self, event, section):
        if not event.scout_attendance_line_ids and not event.scout_attendance_closed:
            event.action_scout_load_members()
        lines = event.scout_attendance_line_ids
        if event.scout_scope == 'group':
            members = self._members(section, include_leaders=True)
            lines = lines.filtered(lambda l: l.member_id in members or l.section_id == section)
        return lines.sorted(lambda l: (l.member_id.scout_type == 'leader', l.patrol_id.sequence or 0,
                                       l.patrol_id.name or '', l.member_id.name or ''))

    @api.model
    def set_attendance(self, line_id, status):
        line = self.env['scout.attendance.line'].sudo().browse(int(line_id or 0)).exists()
        if not line or status not in QUICK_STATUSES:
            raise UserError(_('Dato no válido.'))
        event = self._event(line.event_id.id)
        sections = self._sections()
        if event.scout_scope == 'group' and not (line.section_id in sections or line.member_id.scout_section_id in sections
                                                 or line.member_id.scout_leading_section_ids & sections):
            raise AccessError(_('Ese miembro no es de tu sección.'))
        if event.scout_attendance_closed:
            raise UserError(_('La lista ya está cerrada.'))
        line.status = status
        return {'status': status, 'present': event.scout_present_count, 'counted': event.scout_counted_count,
                'pct': round(event.scout_attendance_pct)}

    @api.model
    def all_present(self, event, section):
        if event.scout_attendance_closed:
            raise UserError(_('La lista ya está cerrada.'))
        self.attendance_lines(event, section).filtered(lambda l: l.status == 'absent').write({'status': 'present'})

    @api.model
    def close_attendance(self, event, section, result=None):
        self.attendance_lines(event, section)
        vals = {'scout_attendance_closed': True, 'scout_done': True}
        if result:
            vals['scout_result'] = result
        event.write(vals)
        self._note(event, _('Lista cerrada: %(present)s de %(counted)s (%(pct)s %%)', present=event.scout_present_count,
                            counted=event.scout_counted_count, pct=round(event.scout_attendance_pct)))

    @api.model
    def reopen_attendance(self, event):
        event.write({'scout_attendance_closed': False})
        self._note(event, _('Lista reabierta'))

    # ------------------------------------------------------------------ adelantos
    @api.model
    def stages(self, section):
        return self.env['scout.progression.stage'].sudo().search(
            ['|', ('section_ids', '=', False), ('section_ids', 'in', section.ids)])

    @api.model
    def grant_progress(self, section, stage_id, member_ids, state='achieved', date=None):
        stage = self.stages(section).filtered(lambda s: s.id == int(stage_id or 0))
        if not stage:
            raise UserError(_('Elige el adelanto, insignia o especialidad.'))
        if state not in ('achieved', 'in_progress'):
            state = 'achieved'
        day = fields.Date.to_date(date) if date else self._today()
        leader = self.env.user.partner_id
        Progress = self.env['scout.member.progress'].sudo()
        done = self.env['res.partner'].sudo()
        for member_id in member_ids:
            member = self._member(member_id, section)
            progress = Progress.search([('member_id', '=', member.id), ('stage_id', '=', stage.id)], limit=1)
            vals = {'state': state, 'date_achieved': day if state == 'achieved' else False}
            if not progress:
                Progress.create({'member_id': member.id, 'stage_id': stage.id, 'date_start': day,
                                 'leader_id': leader.id if leader.is_scout else False, **vals})
            elif progress.state != 'achieved':
                progress.write(vals)
            else:
                continue
            done |= member
        for member in done:
            self._note(member, _('%(stage)s: %(state)s', stage=stage.name,
                                 state=_('logrado') if state == 'achieved' else _('en curso')))
        return done

    # ------------------------------------------------------------------ planificación
    @api.model
    def quarter_period(self, offset=0):
        start, _end = quarter_bounds(self._today())
        start = start + relativedelta(months=3 * int(offset or 0))
        start, end = quarter_bounds(start)
        return start, end

    @api.model
    def cycle_period(self, section):
        cycle = self.env['scout.cycle'].sudo()._get_current(section.company_id)
        if cycle:
            return cycle.date_start, cycle.date_end, cycle.name
        today = self._today()
        return date(today.year, 1, 1), date(today.year, 12, 31), str(today.year)

    @api.model
    def period_events(self, section, start, end):
        return self.env['calendar.event'].sudo().search(
            self._events_domain(section) + [('scout_date', '>=', start), ('scout_date', '<=', end)], order='start')

    @api.model
    def plan_days(self, section, start, end, weekday=5):
        """[(fecha, actividades)] con cada sábado del periodo y los demás días que ya tienen actividad."""
        events = self.period_events(section, start, end)
        by_day = {}
        for event in events:
            by_day.setdefault(event.scout_date, self.env['calendar.event'].sudo())
            by_day[event.scout_date] |= event
        day = start
        while day.weekday() != weekday:
            day += timedelta(days=1)
        while day <= end:
            by_day.setdefault(day, self.env['calendar.event'].sudo())
            day += timedelta(days=7)
        return sorted(by_day.items())

    @api.model
    def plan_months(self, section, start, end):
        """[(primer día del mes, actividades)] del ciclo."""
        events = self.period_events(section, start, end)
        months = []
        month = start.replace(day=1)
        while month <= end:
            nxt = month + relativedelta(months=1)
            months.append((month, events.filtered(lambda e: month <= e.scout_date < nxt)))
            month = nxt
        return months

    @api.model
    def generate_sessions(self, section, start, end, hour_from=10.0, hour_to=13.0):
        """Crea la sesión de cada sábado (desde hoy) que aún no tenga actividad de la sección."""
        start = max(start, self._today())
        if start > end:
            raise UserError(_('Ese periodo ya pasó.'))
        before = self.period_events(section, start, end)
        self.env['scout.event.generate'].sudo().create({
            'section_ids': [fields.Command.set(section.ids)], 'date_from': start, 'date_to': end,
            'weekday': '5', 'hour_start': hour_from, 'hour_end': hour_to, 'name': _('Sesión'),
            'activity_type': 'regular', 'skip_existing': True,
        }).action_generate()
        created = self.period_events(section, start, end) - before
        for event in created:
            event.user_id = self.env.uid
        return created

    @api.model
    def program_pdf(self, section, start, end, theme=None):
        wizard = self.env['scout.program.report.wizard'].sudo().create({
            'section_id': section.id, 'date_from': start, 'date_to': end, 'theme': theme or False,
            'include_group_events': True})
        pdf, _type = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
            'scout_management.action_report_program', wizard.ids, data={'wizard_id': wizard.id})
        return pdf

    # ------------------------------------------------------------------ control de asistencia
    @api.model
    def attendance_matrix(self, section, start, end):
        """La misma matriz del Excel / PDF de asistencia: miembros × sesiones, con cantidad y porcentaje."""
        return self.env['report.scout_management.report_attendance_matrix'].sudo()._build_section(
            section, start, end, False)

    @api.model
    def attendance_pdf(self, section, start, end):
        wizard = self.env['scout.attendance.report.wizard'].sudo().create({
            'section_ids': [fields.Command.set(section.ids)], 'date_from': start, 'date_to': end})
        pdf, _type = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
            'scout_management.action_report_attendance', wizard.ids, data={'wizard_id': wizard.id})
        return pdf

    # ------------------------------------------------------------------ informes trimestral y anual
    @api.model
    def current_report(self, section, create=True, period='quarter', offset=0):
        if period == 'annual':
            start, end, _label = self.cycle_period(section)
        else:
            period = 'quarter'
            start, end = self.quarter_period(offset)
        Report = self.env['scout.section.report'].sudo()
        report = Report.search([('section_id', '=', section.id), ('date_from', '=', start),
                                ('period_type', '=', period)], limit=1)
        if not report and create:
            report = Report.create({'section_id': section.id, 'date_from': start, 'date_to': end,
                                    'period_type': period,
                                    'presented_to': _('Consejo de Grupo')})
            report.action_refresh()
        return report

    @api.model
    def save_report(self, report, post):
        if report.state != 'draft':
            raise UserError(_('El informe ya se presentó; pide a secretaría que lo regrese a borrador.'))
        vals = {}
        for key in ('situation', 'closing_phrase'):
            if key in post:
                vals[key] = post.get(key) or False
        commands = []
        for item_type in LEADER_ITEM_TYPES:
            key = f'items_{item_type}'
            if key not in post:
                continue
            for item in report.item_ids.filtered(lambda i: i.item_type == item_type and not i.is_auto):
                commands.append(fields.Command.delete(item.id))
            for seq, text in enumerate(t.strip(' -•\t') for t in (post.get(key) or '').splitlines()):
                if text:
                    commands.append(fields.Command.create({'item_type': item_type, 'name': text,
                                                           'sequence': 100 + seq, 'is_auto': False}))
        if commands:
            vals['item_ids'] = commands
        report.write(vals)
        self._note(report, _('Informe actualizado'))
        return report

    # ------------------------------------------------------------------ mensajes con familias
    @api.model
    def conversations(self, section):
        members = self._members(section)
        Conv = self.env['scout.family.conversation'].sudo()
        convs = Conv.search([('member_id', 'in', members.ids)])
        leader_partners = self.env['res.partner'].sudo().search([('user_ids', 'in', section.leader_ids.ids)])
        leader_partners |= self.env.user.partner_id
        result = []
        for conv in convs:
            last = conv.message_ids.filtered(lambda m: m.message_type == 'comment')[:1]
            if not last:
                continue
            result.append({'conv': conv, 'last': last,
                           'needs_reply': last.author_id not in leader_partners and last.author_id.id
                           in (conv.member_id.scout_guardian_id | conv.member_id.scout_guardian2_id).ids})
        result.sort(key=lambda r: r['last'].date, reverse=True)
        return result

    @api.model
    def conversation(self, conv_id):
        conv = self.env['scout.family.conversation'].sudo().browse(int(conv_id or 0)).exists()
        if not conv:
            raise AccessError(_('La conversación no existe.'))
        self._member(conv.member_id.id)
        conv._sync_followers()
        if self.env.user.partner_id not in conv.message_partner_ids:
            conv.message_subscribe(partner_ids=self.env.user.partner_id.ids)
        conv._portal_ensure_token()
        return conv

    @api.model
    def open_conversation_for_member(self, member_id):
        member = self._member(member_id)
        return self.env['scout.family.conversation'].sudo()._get_or_create_for_member(member)

    # ------------------------------------------------------------------ resumen de la portada
    @api.model
    def dashboard(self, section):
        today_event = self.today_event(section)
        q_start, q_end = self.quarter_period()
        planned = self.period_events(section, q_start, q_end)
        return {
            'today_event': today_event,
            'next_events': self.events(section, upcoming=True, limit=3) - today_event,
            'members': self._members(section),
            'planned_count': len(planned),
            'messages_pending': sum(1 for c in self.conversations(section) if c['needs_reply']),
            'report': self.current_report(section, create=False),
            'annual_report': self.current_report(section, create=False, period='annual'),
        }

    @api.model
    def leader_partners(self, section):
        return self._members(section, include_leaders=True).filtered(lambda p: p.scout_type == 'leader')

