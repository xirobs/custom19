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

from .calendar_event import CATEGORY_HEX, DEFAULT_TZ
from .scout_common import ACTIVITY_TYPES, quarter_bounds
from .scout_progression import PATH_TYPES

ROLE_ORDER = {'guide': 0, 'subguide': 1}
PATROL_COLORS = {'yellow': '#f2c200', 'blue': '#1f6fb5', 'red': '#d7262f', 'green': '#2e9e44',
                 'orange': '#f36f45', 'purple': '#7b3f98', 'brown': '#8a5a2b', 'white': '#d9dde3'}

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
        event = self.save_event(section, None, {
            'name': _('Sesión %s', section.display_name), 'date': today.isoformat(),
            'time_from': '10:00', 'time_to': '13:00', 'activity_type': 'regular'})
        event.write({'scout_plan_state': 'approved', 'scout_plan_note': _('Sesión del día (sin planear)')})
        return event

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
        try:
            day_end = fields.Date.to_date(post.get('date_end')) or day
        except ValueError:
            day_end = day
        if day_end < day:
            day_end = day
        if day_end == day and stop_h <= start_h:
            stop_h = start_h + 1
        activity_type = post.get('activity_type') or 'regular'
        if activity_type not in dict(ACTIVITY_TYPES) or activity_type in ('council', 'group'):
            activity_type = 'regular'
        category = post.get('category')
        if category not in ('outing', 'ordinary'):
            category = 'outing' if activity_type in ('camp', 'outing') else 'ordinary'
        leaders = self._members(section, include_leaders=True).filtered(lambda p: p.scout_type == 'leader')
        responsible_ids = [int(x) for x in (post.get('responsible_ids') or []) if str(x).isdigit()]
        vals = {
            'name': name,
            'start': self._to_utc(day, start_h),
            'stop': self._to_utc(day_end, stop_h),
            'allday': False,
            'scout_activity_type': activity_type,
            'scout_category': category,
            'scout_theme': post.get('theme') or False,
            'scout_objective': post.get('objective') or False,
            'location': post.get('location') or False,
            'scout_recognitions': post.get('recognitions') or False,
            'description': post.get('description') or False,
            'scout_responsible_ids': [fields.Command.set(leaders.filtered(lambda p: p.id in responsible_ids).ids)],
        }
        if 'has_done' in post:
            vals['scout_done'] = bool(post.get('done'))
        if 'result' in post:
            vals['scout_result'] = post.get('result') or False
        Event = self.env['calendar.event'].sudo().with_context(
            no_mail_to_attendees=True, dont_notify=True, mail_create_nosubscribe=True)
        if event:
            key_changed = event.scout_plan_state in ('approved', 'reviewed') and (
                event.start != vals['start'] or event.stop != vals['stop'] or category != event.scout_category
                or activity_type != event.scout_activity_type or (vals['location'] or False) != (event.location or False))
            if key_changed:
                vals['scout_plan_state'] = 'proposed'
            event.with_context(no_mail_to_attendees=True, dont_notify=True).write(vals)
            self._note(event, _('Actividad actualizada; regresa a revisión del consejo') if key_changed
                       else _('Actividad actualizada'))
            return event
        vals['scout_plan_state'] = 'draft'
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
        member = line.member_id
        member.invalidate_recordset(['scout_trial_attendance', 'scout_needs_registration'])
        alert = ''
        if member.scout_type == 'youth' and member.scout_registration_status not in ('paid', 'exempt') \
                and member.scout_trial_attendance:
            alert = _('%s.ª asistencia sin registro', member.scout_trial_attendance)
        return {'status': status, 'present': event.scout_present_count, 'counted': event.scout_counted_count,
                'pct': round(event.scout_attendance_pct), 'alert': alert,
                'needs_registration': member.scout_needs_registration}

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
        return event.scout_attendance_line_ids.member_id._scout_notify_registration_due()

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
        created.write({'user_id': self.env.uid, 'scout_plan_state': 'draft',
                       'scout_approved_by_id': False, 'scout_approved_date': False})
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

    # ------------------------------------------------------------------ equipos (patrulla / seisena / madriguera)
    @api.model
    def team_board(self, section):
        """[(equipo o vacío, miembros)] con guía y subguía primero."""
        members = self._members(section)
        board = []
        for patrol in section.sudo().patrol_ids.sorted(lambda p: (p.sequence, p.id)):
            team = members.filtered(lambda m: m.scout_patrol_id == patrol)
            board.append((patrol, team.sorted(lambda m: (ROLE_ORDER.get(m.scout_position_id.kind, 2), m.name or ''))))
        loose = members.filtered(lambda m: not m.scout_patrol_id or m.scout_patrol_id.section_id != section)
        board.append((self.env['scout.patrol'].sudo(), loose.sorted(lambda m: m.name or '')))
        return board

    @api.model
    def patrol_color(self, patrol):
        return PATROL_COLORS.get(patrol.color_name, '#0f3049') if patrol else '#9aa5b1'

    @api.model
    def move_member(self, section, member_id, patrol_id):
        member = self._member(member_id, section)
        patrol = self.env['scout.patrol'].sudo()
        if patrol_id:
            patrol = patrol.browse(int(patrol_id)).exists()
            if not patrol or patrol.section_id != section:
                raise AccessError(_('Ese equipo no es de tu sección.'))
        old = member.scout_patrol_id
        member._scout_set_patrol(patrol)
        self._note(member, _('Cambio de %(label)s: %(old)s → %(new)s', label=(section.patrol_label or _('equipo')).lower(),
                             old=old.name or _('sin equipo'), new=patrol.name or _('sin equipo')))
        return member

    @api.model
    def set_role(self, section, member_id, kind):
        member = self._member(member_id, section)
        member._scout_set_patrol_role(kind)
        labels = {'guide': section.patrol_role_label or _('Guía'), 'subguide': section.patrol_subrole_label or _('Subguía'),
                  'member': _('Integrante')}
        self._note(member, _('Cargo en %(team)s: %(role)s', team=member.scout_patrol_id.name or '-', role=labels[kind]))
        return member

    @api.model
    def role_label(self, section, member):
        kind = member.scout_position_id.kind
        if kind == 'guide':
            return section.patrol_role_label or _('Guía')
        if kind == 'subguide':
            return section.patrol_subrole_label or _('Subguía')
        return ''

    @api.model
    def create_patrol(self, section, name, color=None):
        name = (name or '').strip()
        if not name:
            raise UserError(_('Escribe el nombre del equipo.'))
        if color not in PATROL_COLORS:
            color = False
        patrol = self.env['scout.patrol'].sudo().create({
            'name': name, 'section_id': section.id, 'color_name': color,
            'sequence': max(section.sudo().patrol_ids.mapped('sequence') or [0]) + 10})
        self._note(section, _('Nuevo equipo: %s', name))
        return patrol

    # ------------------------------------------------------------------ catálogo de adelantos
    @api.model
    def path_stages(self, section):
        """Promesa, adelantos, ruta de enlace e insignia terminal de la sección, en orden."""
        return self.stages(section).filtered(lambda s: s.stage_type in PATH_TYPES).sorted(
            lambda s: (s.type_order, s.sequence, s.id))

    @api.model
    def specialty_categories(self, section):
        specialties = self.stages(section).filtered(lambda s: s.stage_type == 'specialty')
        categories = specialties.category_id.sorted(lambda c: (c.sequence, c.id))
        result = [(c, specialties.filtered(lambda s: s.category_id == c).sorted(lambda s: (s.sequence, s.id)))
                  for c in categories]
        loose = specialties.filtered(lambda s: not s.category_id)
        if loose:
            result.append((self.env['scout.progression.category'].sudo(), loose))
        return result

    # ------------------------------------------------------------------ cuadro de logros
    @api.model
    def achievements(self, section, columns):
        """Filas por miembro: avance del camino de adelantos (termómetro) y celdas de las columnas pedidas."""
        members = self._members(section)
        path = self.path_stages(section)
        progress = self.env['scout.member.progress'].sudo().search([('member_id', 'in', members.ids)])
        by_member = {}
        for p in progress:
            by_member.setdefault(p.member_id.id, {})[p.stage_id.id] = p
        rows = []
        for member in members:
            mine = by_member.get(member.id, {})
            done = [s for s in path if mine.get(s.id) and mine[s.id].state == 'achieved']
            current = next((s for s in path if mine.get(s.id) and mine[s.id].state == 'in_progress'), None)
            if not current:
                current = next((s for s in path if s.id not in mine), None)
            specs = [p for p in mine.values() if p.stage_id.stage_type == 'specialty' and p.state == 'achieved']
            rows.append({
                'member': member, 'cells': [mine.get(c.id) for c in columns],
                'done': len(done), 'total': len(path),
                'pct': round(len(done) * 100.0 / len(path)) if path else 0,
                'next': current, 'specialties': len(specs),
            })
        rows.sort(key=lambda r: (r['member'].scout_patrol_id.sequence or 999, r['member'].scout_patrol_id.name or '',
                                 r['member'].name or ''))
        return rows

    @api.model
    def toggle_progress(self, section, member_id, stage_id):
        """Un toque: vacío → en curso → logrado → vacío."""
        member = self._member(member_id, section)
        stage = self.stages(section).filtered(lambda s: s.id == int(stage_id or 0))
        if not stage:
            raise UserError(_('Ese adelanto no es de tu sección.'))
        Progress = self.env['scout.member.progress'].sudo()
        progress = Progress.search([('member_id', '=', member.id), ('stage_id', '=', stage.id)], limit=1)
        today = self._today()
        if not progress:
            leader = self.env.user.partner_id
            Progress.create({'member_id': member.id, 'stage_id': stage.id, 'state': 'in_progress', 'date_start': today,
                             'leader_id': leader.id if leader.is_scout else False})
            state = 'in_progress'
            self._note(member, _('%s: en curso', stage.name))
        elif progress.state == 'in_progress':
            progress.write({'state': 'achieved', 'date_achieved': today})
            state = 'achieved'
            self._note(member, _('%s: logrado', stage.name))
        else:
            progress.unlink()
            state = 'none'
            self._note(member, _('%s: se quitó el registro', stage.name))
        path = self.path_stages(section)
        achieved = Progress.search_count([('member_id', '=', member.id), ('stage_id', 'in', path.ids),
                                          ('state', '=', 'achieved')])
        return {'state': state, 'done': achieved, 'total': len(path),
                'pct': round(achieved * 100.0 / len(path)) if path else 0}

    # ------------------------------------------------------------------ nuevos ingresos
    @api.model
    def _image_from_post(self, value):
        """Foto tomada con la cámara: llega como data URL (reducida en el celular) o como archivo."""
        import base64
        if hasattr(value, 'read'):
            content = value.read()
            if not content:
                return False
            if len(content) > 8 * 1024 * 1024:
                raise UserError(_('La foto no debe pasar de 8 MB.'))
            return base64.b64encode(content)
        if isinstance(value, str) and value.startswith('data:image'):
            return value.split(',', 1)[1].encode()
        return False

    @api.model
    def set_photo(self, section, member_id, value):
        member = self._member(member_id, section)
        image = self._image_from_post(value)
        if not image:
            raise UserError(_('Toma o elige una foto.'))
        member.write({'image_1920': image})
        self._note(member, _('Foto actualizada'))
        return member

    @api.model
    def create_member(self, section, post, photo=None):
        """Alta rápida de un nuevo ingreso (en prueba) para que aparezca en el pase de lista."""
        first = (post.get('first_name') or '').strip()
        last = (post.get('last_name') or '').strip()
        if not first or not last:
            raise UserError(_('Escribe el nombre y el apellido paterno.'))
        try:
            birthdate = fields.Date.to_date(post.get('birthdate')) if post.get('birthdate') else False
        except ValueError:
            raise UserError(_('La fecha de nacimiento no es válida.'))
        Partner = self.env['res.partner'].sudo()
        guardian = Partner
        g_name = (post.get('guardian_name') or '').strip()
        g_phone = (post.get('guardian_phone') or '').strip()
        g_email = (post.get('guardian_email') or '').strip()
        if g_name or g_phone:
            if g_email:
                guardian = Partner.search([('email', '=ilike', g_email)], limit=1)
            if not guardian and g_phone:
                guardian = Partner.search([('phone', '=', g_phone)], limit=1)
            if not guardian:
                guardian = Partner.create({'name': g_name or _('Tutor de %s', first), 'phone': g_phone or False,
                                           'email': g_email or False})
        vals = {
            'is_scout': True, 'scout_type': 'youth', 'scout_state': 'trial',
            'scout_first_name': first, 'scout_last_name': last,
            'scout_last_name2': (post.get('last_name2') or '').strip() or False,
            'name': ' '.join(p for p in (first, last, post.get('last_name2') or '') if p.strip()),
            'scout_name': (post.get('scout_name') or '').strip() or False,
            'scout_birthdate': birthdate, 'scout_gender': post.get('gender') if post.get('gender') in ('m', 'f') else False,
            'scout_section_id': section.id, 'scout_join_date': self._today(),
            'scout_guardian_id': guardian.id or False,
            'scout_guardian_relationship': (post.get('guardian_relationship') or '').strip() or False,
            'street': (post.get('street') or '').strip() or False,
        }
        if post.get('patrol_id') and str(post['patrol_id']).isdigit():
            patrol = self.env['scout.patrol'].sudo().browse(int(post['patrol_id'])).exists()
            if patrol and patrol.section_id == section:
                vals['scout_patrol_id'] = patrol.id
        image = self._image_from_post(photo) if photo else False
        if image:
            vals['image_1920'] = image
        member = Partner.with_context(scout_change_approved=True).create(vals)
        self._note(member, _('Nuevo ingreso registrado en %s', section.display_name))
        return member

    @api.model
    def register_member(self, section, member, post):
        """Ficha de registro firmada por el tutor (consentimientos) y cargo de la cuota con su liga de pago."""
        member = self._member(member.id, section)
        signature = post.get('guardian_signature') or ''
        if not signature.startswith('data:image'):
            raise UserError(_('Falta la firma del padre, madre o tutor.'))
        consents = ('consent_participation', 'consent_first_aid', 'consent_data', 'consent_risk', 'consent_update')
        if not all(post.get(c) for c in consents):
            raise UserError(_('El tutor debe aceptar todas las autorizaciones obligatorias.'))
        signed_by = (post.get('guardian_signed_by') or '').strip() or member.scout_guardian_id.name
        if not signed_by:
            raise UserError(_('Escribe el nombre de quien firma.'))
        cycle = self.env['scout.cycle'].sudo()._get_current(section.company_id)
        if not cycle:
            raise UserError(_('No hay un ciclo scout abierto; pide a secretaría que lo cree.'))
        Registration = self.env['scout.registration'].sudo()
        reg = Registration.search([('member_id', '=', member.id), ('cycle_id', '=', cycle.id),
                                   ('state', '!=', 'cancel')], limit=1)
        guardian = member.scout_guardian_id
        vals = {
            'cycle_id': cycle.id, 'member_id': member.id, 'source': 'portal', 'state': 'submitted',
            'company_id': section.company_id.id, 'section_id': section.id,
            'first_name': member.scout_first_name or member.name, 'last_name': member.scout_last_name,
            'last_name2': member.scout_last_name2, 'birthdate': member.scout_birthdate, 'street': member.street,
            'guardian_id': guardian.id or False, 'guardian_name': guardian.name or signed_by,
            'guardian_relationship': member.scout_guardian_relationship, 'guardian_phone': guardian.phone,
            'guardian_email': guardian.email,
            'consent_participation': True, 'consent_first_aid': True, 'consent_data': True,
            'consent_risk': True, 'consent_update': True, 'consent_image': bool(post.get('consent_image')),
            'guardian_signed_by': signed_by, 'guardian_signature': signature.split(',', 1)[1],
            'signed_date': self._today(), 'leader_user_id': self.env.uid,
        }
        if reg:
            if reg.state == 'validated':
                raise UserError(_('%s ya tiene su ficha validada en este ciclo.', member.name))
            reg.write(vals)
        else:
            reg = Registration.create(vals)
        if not reg.guardian_id:
            reg.guardian_id = reg._get_or_create_guardian()
            if reg.guardian_id and not member.scout_guardian_id:
                member.with_context(scout_change_approved=True).scout_guardian_id = reg.guardian_id
        reg._create_registration_invoice()
        self._note(reg, _('Ficha capturada en el portal de dirigentes con la firma del tutor'))
        officers = self.env.ref('scout_management.group_scout_officer').sudo().all_user_ids.filtered(
            lambda u: u.active and not u.share and u.id != 1)
        todo = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        for user in officers[:2]:
            reg.activity_schedule(activity_type_id=todo.id if todo else False,
                                  summary=_('Revisar ficha de nuevo ingreso'), user_id=user.id)
        return reg

    @api.model
    def registration(self, member):
        cycle = self.env['scout.cycle'].sudo()._get_current()
        return self.env['scout.registration'].sudo().search(
            [('member_id', '=', member.id), ('cycle_id', '=', cycle.id), ('state', '!=', 'cancel')], limit=1) \
            if cycle else self.env['scout.registration'].sudo()

    @api.model
    def payment_info(self, member):
        """Cargo pendiente del registro con su liga, QR y mensaje para WhatsApp."""
        from urllib.parse import quote
        from .scout_online_payment import scout_qr_data_uri, scout_whatsapp_phone
        reg = self.registration(member)
        invoice = reg.invoice_id if reg else self.env['account.move']
        if not invoice or not invoice.scout_can_pay_online:
            return {'registration': reg, 'invoice': invoice}
        link = invoice._scout_payment_link()
        phone = scout_whatsapp_phone(invoice.partner_id.phone or member.scout_guardian_id.phone)
        text = invoice._scout_payment_message(link)
        return {'registration': reg, 'invoice': invoice, 'link': link, 'qr': scout_qr_data_uri(link),
                'whatsapp': f'https://wa.me/{phone}?text={quote(text)}' if phone else
                f'https://wa.me/?text={quote(text)}'}

    @api.model
    def alerts(self, section):
        """Avisos de la sección: nuevos que ya deben registrarse y miembros por edad."""
        members = self._members(section)
        return {
            'registration': members.filtered('scout_needs_registration'),
            'age': members.filtered(lambda m: m.scout_age_alert in ('over', 'soon')),
        }

    # ------------------------------------------------------------------ planificación: aprobación y calendario general
    @api.model
    def submit_plan(self, section, start, end):
        events = self.period_events(section, start, end).filtered(
            lambda e: e.scout_scope == 'section' and e.scout_plan_state in ('draft', 'changes')
            and self._event_editable(e))
        if not events:
            raise UserError(_('No hay actividades en borrador para enviar.'))
        events.action_scout_plan_submit()
        self._note(section, _('Programa %(start)s – %(end)s enviado al consejo (%(n)s actividades)',
                              start=start.strftime('%d/%m/%Y'), end=end.strftime('%d/%m/%Y'), n=len(events)))
        return events

    @api.model
    def plan_summary(self, events):
        events = events.filtered(lambda e: e.scout_scope == 'section')
        return {state: len(events.filtered(lambda e: e.scout_plan_state == state))
                for state in ('draft', 'proposed', 'reviewed', 'approved', 'changes')}

    @api.model
    def general_calendar(self, month_start):
        """Semanas del mes con las actividades de todas las secciones y del grupo."""
        start = month_start.replace(day=1)
        end = start + relativedelta(months=1, days=-1)
        grid_start = start - timedelta(days=start.weekday())
        grid_end = end + timedelta(days=6 - end.weekday())
        events = self.env['calendar.event'].sudo().search(
            [('is_scout_event', '=', True), ('scout_date', '<=', grid_end),
             '|', ('scout_date_end', '>=', grid_start), ('scout_date', '>=', grid_start)], order='start')
        weeks, day = [], grid_start
        while day <= grid_end:
            week = []
            for _i in range(7):
                todays = events.filtered(lambda e, d=day: e.scout_date <= d <= (e.scout_date_end or e.scout_date))
                week.append((day, todays))
                day += timedelta(days=1)
            weeks.append(week)
        return {'start': start, 'end': end, 'weeks': weeks, 'events': events}

    @api.model
    def category_hex(self, event):
        return CATEGORY_HEX.get(event.scout_category or 'ordinary')


    # ------------------------------------------------------------------ rutas de enlace y permanencia
    @api.model
    def routes(self, section):
        members = self._members(section)
        members._scout_ensure_route()
        routes = self.env['scout.route'].sudo().search([('member_id', 'in', members.ids), ('is_open', '=', True),
                                                       ('section_id', '=', section.id)])
        return routes.sorted(lambda r: (r.alert_order, r.date_pass_official or date.max, r.member_id.name or ''))

    @api.model
    def incoming_routes(self, section):
        """Beneficiarios que recibirá la sección: en ruta, deben iniciar, vencidos o próximos a comenzar."""
        routes = self.env['scout.route'].sudo().search([
            ('destination_id', '=', section.id), ('is_open', '=', True), ('member_state', 'in', ('trial', 'active')),
            '|', ('state', 'in', ('must_start', 'in_route', 'extension', 'overdue')), ('route_soon', '=', True)])
        return routes.sorted(lambda r: (r.alert_order, r.date_pass_official or date.max))

    @api.model
    def start_route(self, section, route_id, day=None):
        route = self.env['scout.route'].sudo().browse(int(route_id or 0)).exists()
        if not route or route.section_id != section:
            raise AccessError(_('Esa ruta no es de tu sección.'))
        self._member(route.member_id.id, section)
        route.action_start_route(day)
        self._note(route.member_id, _('Inicio de ruta de enlace registrado'))
        return route

    @api.model
    def plan_day(self, section, day):
        """La sesión de la sección en ese día (la crea en borrador si no existe) para planearla y dictar su programa."""
        existing = self.env['calendar.event'].sudo().search([
            ('is_scout_event', '=', True), ('scout_section_ids', 'in', section.ids), ('scout_scope', '=', 'section'),
            ('scout_date', '=', day)], order='start', limit=1)
        if existing:
            return existing
        last = self.env['calendar.event'].sudo().search([
            ('is_scout_event', '=', True), ('scout_section_ids', 'in', section.ids), ('scout_scope', '=', 'section'),
            ('scout_activity_type', '=', 'regular')], order='start desc', limit=1)
        start = self._local(last.start) if last else None
        stop = self._local(last.stop) if last else None
        return self.save_event(section, None, {
            'name': _('Sesión %s', section.display_name), 'date': day.isoformat(),
            'time_from': start.strftime('%H:%M') if start else '16:00',
            'time_to': stop.strftime('%H:%M') if stop and stop.date() == start.date() else '18:00',
            'activity_type': 'regular'})
