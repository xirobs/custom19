# -*- coding: utf-8 -*-
import logging
import secrets
from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.addons.base.models.res_partner import _tz_get

from .bni_tools import check_rotating_code, current_window, generate_qr_base64, rotating_code

_logger = logging.getLogger(__name__)


class BniEvent(models.Model):
    _name = 'bni.event'
    _description = 'Evento / Sesión BNI'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'checkin_limit desc, id desc'

    name = fields.Char('Nombre del evento', required=True, tracking=True, default=lambda self: _('Sesión semanal'))
    modality = fields.Selection([
        ('in_person', 'Presencial'),
        ('virtual', 'Virtual'),
    ], string='Modalidad', required=True, default='in_person', tracking=True)
    checkin_limit = fields.Datetime(
        'Fecha y hora de llegada', required=True, tracking=True,
        help='Quien registre su asistencia después de esta hora queda como Retardo. '
             'El registro sigue abierto mientras el evento no se cierre.')
    tz = fields.Selection(
        _tz_get, string='Zona horaria', required=True,
        default=lambda self: self.env.user.tz or 'America/Mexico_City')
    event_date = fields.Date('Fecha', compute='_compute_event_date', store=True, index=True)
    checkin_limit_local = fields.Char('Llegada (hora local)', compute='_compute_local_strings')
    location = fields.Char('Lugar')
    meeting_url = fields.Char('Link de la reunión virtual')
    notes = fields.Html('Notas del evento')
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('open', 'Registro abierto'),
        ('closed', 'Cerrado'),
    ], string='Estado', default='draft', required=True, copy=False, tracking=True)
    company_id = fields.Many2one('res.company', 'Compañía', required=True, default=lambda self: self.env.company)

    qr_mode = fields.Selection([
        ('dynamic', 'Dinámico en pantalla (cambia cada minuto)'),
        ('static', 'Fijo (para imprimir)'),
    ], string='Tipo de QR', required=True, default='dynamic', tracking=True,
        help='Dinámico: el QR se proyecta en la sesión y cambia cada minuto; una foto o reenvío del QR '
             'deja de funcionar a los pocos minutos, así solo registra quien está en la sesión.\n'
             'Fijo: un QR que no cambia, útil para imprimir en la sala.')
    access_token = fields.Char(copy=False, readonly=True, default=lambda self: secrets.token_urlsafe(16))
    checkin_url = fields.Char('Link de registro (QR fijo)', compute='_compute_checkin_url')
    qr_image = fields.Binary('QR fijo', compute='_compute_checkin_url')

    attendance_ids = fields.One2many('bni.attendance', 'event_id', 'Asistencia', copy=False)
    member_line_ids = fields.One2many(
        'bni.attendance', 'event_id', 'Lista de miembros',
        domain=[('attendee_type', '=', 'member')], copy=False)
    guest_line_ids = fields.One2many(
        'bni.attendance', 'event_id', 'Lista de invitados',
        domain=[('attendee_type', '=', 'guest')], copy=False)

    member_total = fields.Integer('Total miembros', compute='_compute_counts')
    on_time_count = fields.Integer('A tiempo', compute='_compute_counts')
    late_count = fields.Integer('Retardos', compute='_compute_counts')
    substitute_count = fields.Integer('Sustitutos', compute='_compute_counts')
    excused_count = fields.Integer('Justificadas', compute='_compute_counts')
    absent_count = fields.Integer('Faltas', compute='_compute_counts')
    pending_count = fields.Integer('Pendientes', compute='_compute_counts')
    present_count = fields.Integer('Registrados', compute='_compute_counts')
    guest_count = fields.Integer('Total invitados', compute='_compute_counts')
    attendance_rate = fields.Float('% Asistencia', compute='_compute_counts', digits=(5, 1))

    _access_token_uniq = models.Constraint('UNIQUE(access_token)', 'El token del evento debe ser único.')

    # ------------------------------------------------------------------
    # Zona horaria
    # ------------------------------------------------------------------
    def _to_local(self, dt):
        self.ensure_one()
        if not dt:
            return False
        tz = pytz.timezone(self.tz or 'UTC')
        return pytz.utc.localize(dt).astimezone(tz)

    def format_local(self, dt, fmt='%H:%M'):
        """Formatea un datetime UTC (naive) a la hora local del evento."""
        local = self._to_local(dt)
        return local.strftime(fmt) if local else ''

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('checkin_limit', 'tz')
    def _compute_event_date(self):
        for event in self:
            local = event._to_local(event.checkin_limit)
            event.event_date = local.date() if local else False

    @api.depends('checkin_limit', 'tz')
    def _compute_local_strings(self):
        for event in self:
            event.checkin_limit_local = event.format_local(event.checkin_limit, '%d/%m/%Y %H:%M')

    def _compute_checkin_url(self):
        for event in self:
            token = event.sudo().access_token
            if token and event.id:
                url = '%s/bni/e/%s' % (event.get_base_url(), token)
                event.checkin_url = url
                event.qr_image = generate_qr_base64(url) if event.qr_mode == 'static' else False
            else:
                event.checkin_url = False
                event.qr_image = False

    @api.depends('attendance_ids.state', 'attendance_ids.attendee_type')
    def _compute_counts(self):
        for event in self:
            members = event.attendance_ids.filtered(lambda a: a.attendee_type == 'member')

            def _count(state):
                return len(members.filtered(lambda a: a.state == state))

            event.member_total = len(members)
            event.on_time_count = _count('on_time')
            event.late_count = _count('late')
            event.substitute_count = _count('substitute')
            event.excused_count = _count('excused')
            event.absent_count = _count('absent')
            event.pending_count = _count('pending')
            event.guest_count = len(event.attendance_ids.filtered(lambda a: a.attendee_type == 'guest'))
            attended = event.on_time_count + event.late_count + event.substitute_count
            event.present_count = attended
            base = event.member_total - event.excused_count
            event.attendance_rate = round(attended * 100.0 / base, 1) if base else 0.0

    # ------------------------------------------------------------------
    # QR dinámico
    # ------------------------------------------------------------------
    def get_dynamic_checkin_url(self):
        """URL del QR vigente en este minuto (para la pantalla de la sesión)."""
        self.ensure_one()
        token = self.sudo().access_token
        url = '%s/bni/e/%s' % (self.get_base_url(), token)
        if self.qr_mode == 'dynamic':
            url += '?k=%s' % rotating_code(token, current_window())
        return url

    def check_access_code(self, code, max_minutes):
        """Valida el código del QR dinámico. En modo fijo siempre es válido."""
        self.ensure_one()
        if self.qr_mode == 'static':
            return True
        return check_rotating_code(self.sudo().access_token, code, max_minutes)

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    def copy_data(self, default=None):
        """Al duplicar, se propone la misma sesión una semana después."""
        default = dict(default or {})
        vals_list = super().copy_data(default=default)
        for event, vals in zip(self, vals_list):
            if 'checkin_limit' not in default and event.checkin_limit:
                vals['checkin_limit'] = event.checkin_limit + timedelta(days=7)
        return vals_list

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------
    def action_load_members(self):
        """Agrega como 'Pendiente' a los miembros activos (dados de alta a la fecha del evento)."""
        Attendance = self.env['bni.attendance']
        for event in self:
            if event.state == 'closed':
                raise UserError(_('El evento "%s" ya está cerrado.', event.name))
            domain = [('company_id', '=', event.company_id.id), ('state', '=', 'active')]
            if event.event_date:
                domain.append(('join_date', '<=', event.event_date))
            members = self.env['bni.member'].search(domain)
            existing = event.member_line_ids.member_id
            vals_list = [{
                'event_id': event.id,
                'attendee_type': 'member',
                'member_id': member.id,
            } for member in members - existing]
            if vals_list:
                Attendance.create(vals_list)
        return True

    def action_open(self):
        for event in self.filtered(lambda e: e.state == 'draft'):
            event.action_load_members()
            event.state = 'open'
        return True

    def action_close(self):
        for event in self.filtered(lambda e: e.state == 'open'):
            event.state = 'closed'
            event.message_post(body=_(
                'Evento cerrado. A tiempo: %(on_time)s · Retardos: %(late)s · Sustitutos: %(sub)s · '
                'Justificadas: %(exc)s · Faltas: %(absent)s · Invitados: %(guests)s · Asistencia: %(rate)s%%',
                on_time=event.on_time_count, late=event.late_count, sub=event.substitute_count,
                exc=event.excused_count, absent=event.absent_count, guests=event.guest_count,
                rate=event.attendance_rate))
        return True

    def action_reopen(self):
        self.filtered(lambda e: e.state == 'closed').write({'state': 'open'})
        return True

    def action_reset_draft(self):
        self.write({'state': 'draft'})
        return True

    def action_regenerate_token(self):
        for event in self:
            event.access_token = secrets.token_urlsafe(16)
        return True

    def action_open_screen(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': '/bni/screen/%s' % self.id, 'target': 'new'}

    def action_print_qr(self):
        self.ensure_one()
        if self.qr_mode != 'static':
            raise UserError(_('Este evento usa QR dinámico: usa "Mostrar QR en pantalla". '
                              'Para imprimir, cambia el tipo de QR a Fijo.'))
        return self.env.ref('bni_attendance.action_report_bni_event_qr').report_action(self)

    def action_print_report(self):
        return self.env.ref('bni_attendance.action_report_bni_event').report_action(self)

    # ------------------------------------------------------------------
    # Registro de asistencia (backend y páginas públicas)
    # ------------------------------------------------------------------
    def _check_open(self):
        self.ensure_one()
        if self.state != 'open':
            raise UserError(_('El registro de asistencia para "%s" no está abierto.', self.name))

    def _get_member_line(self, member):
        return self.attendance_ids.filtered(lambda a: a.member_id == member)[:1]

    def register_member_checkin(self, member, method='manual', observation=False):
        """Registra la llegada de un miembro. Devuelve (línea, es_nuevo_registro)."""
        self._check_open()
        line = self._get_member_line(member)
        if line and line.is_substitute:
            raise UserError(_('Ya se registró un sustituto (%s) en tu lugar para este evento.',
                              line.substitute_name))
        if line and line.check_in:
            return line, False
        vals = {'check_in': fields.Datetime.now(), 'checkin_method': method}
        if line:
            line.write(vals)
        else:
            vals.update({'event_id': self.id, 'attendee_type': 'member', 'member_id': member.id})
            line = self.env['bni.attendance'].create(vals)
        if observation:
            line.add_member_observation(observation)
        return line, True

    def register_substitute_checkin(self, member, substitute_name, substitute_company=False, method='qr'):
        """Registra al sustituto en la línea del miembro al que reemplaza."""
        self._check_open()
        if not (substitute_name or '').strip():
            raise UserError(_('El nombre del sustituto es obligatorio.'))
        line = self._get_member_line(member)
        if line and line.check_in and not line.is_substitute:
            raise UserError(_('%s ya registró su asistencia en este evento.', member.name))
        if line and line.is_substitute and line.check_in:
            return line, False
        vals = {
            'is_substitute': True,
            'substitute_name': substitute_name.strip(),
            'substitute_company': (substitute_company or '').strip() or False,
            'check_in': fields.Datetime.now(),
            'checkin_method': method,
        }
        if line:
            line.write(vals)
        else:
            vals.update({'event_id': self.id, 'attendee_type': 'member', 'member_id': member.id})
            line = self.env['bni.attendance'].create(vals)
        return line, True

    def register_guest_checkin(self, name, company_name=False, invited_by=False, method='qr'):
        """Registra a un invitado (nombre, empresa y quién lo invitó)."""
        self._check_open()
        if not (name or '').strip():
            raise UserError(_('El nombre del invitado es obligatorio.'))
        return self.env['bni.attendance'].create({
            'event_id': self.id,
            'attendee_type': 'guest',
            'name': name.strip(),
            'company_name': (company_name or '').strip() or False,
            'invited_by_id': invited_by.id if invited_by else False,
            'check_in': fields.Datetime.now(),
            'checkin_method': method,
        })
