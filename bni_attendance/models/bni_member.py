# -*- coding: utf-8 -*-
import secrets

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

NEW_MEMBER_DAYS = 90

LEAVE_REASONS = [
    ('resignation', 'Renuncia voluntaria'),
    ('no_renewal', 'No renovó membresía'),
    ('attendance', 'Exceso de faltas'),
    ('chapter_change', 'Cambio de capítulo'),
    ('business_closed', 'Cierre o cambio de negocio'),
    ('committee', 'Decisión del comité de membresía'),
    ('other', 'Otro'),
]


class BniMember(models.Model):
    _name = 'bni.member'
    _description = 'Miembro BNI'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'state, name'

    name = fields.Char('Nombre', required=True, tracking=True)
    company_name = fields.Char('Empresa', tracking=True)
    category = fields.Char('Giro / Categoría', tracking=True)
    phone = fields.Char('Teléfono / WhatsApp', tracking=True)
    email = fields.Char('Correo')
    member_code = fields.Char(
        'Código de miembro', copy=False, tracking=True,
        help='Código corto (ej. 4 dígitos) con el que el miembro se identifica en la página del QR.')
    partner_id = fields.Many2one('res.partner', 'Contacto relacionado')
    image_128 = fields.Image('Foto', max_width=128, max_height=128)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', 'Compañía', required=True, default=lambda self: self.env.company)

    # Estado de membresía
    state = fields.Selection([
        ('active', 'Activo'),
        ('inactive', 'Baja'),
    ], string='Estado', default='active', required=True, tracking=True, index=True)
    is_new = fields.Boolean('Nuevo', compute='_compute_is_new',
                            help='Miembro con menos de %s días desde su alta.' % NEW_MEMBER_DAYS)

    # Alta
    join_date = fields.Date('Fecha de alta', required=True, default=fields.Date.context_today, tracking=True)
    sponsor_id = fields.Many2one('bni.member', 'Invitado por / Padrino', tracking=True)
    renewal_date = fields.Date('Fecha de renovación', tracking=True)
    join_notes = fields.Text('Información de alta')

    # Baja
    leave_date = fields.Date('Fecha de baja', tracking=True, readonly=True)
    leave_reason = fields.Selection(LEAVE_REASONS, string='Motivo de baja', tracking=True, readonly=True)
    leave_notes = fields.Text('Detalle de la baja', readonly=True)

    movement_ids = fields.One2many('bni.member.movement', 'member_id', 'Historial de altas y bajas')

    # Token interno (solo para recordar al miembro en su celular; no se comparte)
    access_token = fields.Char(copy=False, readonly=True, default=lambda self: secrets.token_urlsafe(16))

    # Observaciones
    general_observation = fields.Text('Observaciones generales')
    note_ids = fields.One2many('bni.member.note', 'member_id', 'Bitácora de observaciones')

    # Asistencia
    attendance_ids = fields.One2many('bni.attendance', 'member_id', 'Asistencias')
    event_count = fields.Integer('Sesiones', compute='_compute_attendance_stats')
    attended_count = fields.Integer('Sesiones asistidas', compute='_compute_attendance_stats')
    on_time_count = fields.Integer('A tiempo', compute='_compute_attendance_stats')
    late_count = fields.Integer('Retardos', compute='_compute_attendance_stats')
    substitute_count = fields.Integer('Sustitutos', compute='_compute_attendance_stats')
    excused_count = fields.Integer('Justificadas', compute='_compute_attendance_stats')
    absent_count = fields.Integer('Faltas', compute='_compute_attendance_stats')
    attendance_rate = fields.Float(
        '% Participación', compute='_compute_attendance_stats', digits=(5, 1),
        help='(A tiempo + Retardos + Sustitutos) / (Sesiones cerradas - Justificadas)')
    punctuality_rate = fields.Float(
        '% Puntualidad', compute='_compute_attendance_stats', digits=(5, 1),
        help='A tiempo / (A tiempo + Retardos)')

    _member_code_uniq = models.Constraint(
        'UNIQUE(member_code, company_id)', 'El código de miembro debe ser único.')
    _access_token_uniq = models.Constraint('UNIQUE(access_token)', 'El token debe ser único.')

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('join_date', 'state')
    def _compute_is_new(self):
        limit = fields.Date.context_today(self) - relativedelta(days=NEW_MEMBER_DAYS)
        for member in self:
            member.is_new = bool(member.state == 'active' and member.join_date and member.join_date >= limit)

    @api.model
    def stats_from_lines(self, lines):
        """Indicadores a partir de líneas bni.attendance (de eventos cerrados)."""
        counts = {state: 0 for state in ('on_time', 'late', 'substitute', 'excused', 'absent')}
        for line in lines:
            if line.state in counts:
                counts[line.state] += 1
        total = sum(counts.values())
        attended = counts['on_time'] + counts['late'] + counts['substitute']
        base = total - counts['excused']
        punctual_base = counts['on_time'] + counts['late']
        return {
            'total': total,
            'attended': attended,
            'on_time': counts['on_time'],
            'late': counts['late'],
            'substitute': counts['substitute'],
            'excused': counts['excused'],
            'absent': counts['absent'],
            'rate': round(attended * 100.0 / base, 1) if base else 0.0,
            'punctuality': round(counts['on_time'] * 100.0 / punctual_base, 1) if punctual_base else 0.0,
        }

    @api.depends('attendance_ids.state', 'attendance_ids.event_id.state')
    def _compute_attendance_stats(self):
        for member in self:
            lines = member.attendance_ids.filtered(lambda a: a.event_id.state == 'closed')
            stats = self.stats_from_lines(lines)
            member.event_count = stats['total']
            member.attended_count = stats['attended']
            member.on_time_count = stats['on_time']
            member.late_count = stats['late']
            member.substitute_count = stats['substitute']
            member.excused_count = stats['excused']
            member.absent_count = stats['absent']
            member.attendance_rate = stats['rate']
            member.punctuality_rate = stats['punctuality']

    @api.depends('name', 'company_name')
    def _compute_display_name(self):
        for member in self:
            member.display_name = (
                '%s (%s)' % (member.name, member.company_name) if member.company_name else member.name)

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        members = super().create(vals_list)
        self.env['bni.member.movement'].create([{
            'member_id': member.id,
            'movement_type': 'join',
            'date': member.join_date,
            'notes': member.join_notes or False,
        } for member in members])
        return members

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def _open_status_wizard(self, action):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Dar de baja') if action == 'leave' else _('Reingreso'),
            'res_model': 'bni.member.status.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_member_id': self.id, 'default_action': action},
        }

    def action_open_leave_wizard(self):
        return self._open_status_wizard('leave')

    def action_open_reentry_wizard(self):
        return self._open_status_wizard('reentry')

    def action_view_attendance(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Asistencias de %s', self.name),
            'res_model': 'bni.attendance',
            'view_mode': 'list,pivot,graph',
            'domain': [('member_id', '=', self.id)],
            'context': {'search_default_filter_closed': 1},
        }
