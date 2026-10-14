# -*- coding: utf-8 -*-
import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .scout_common import (ACTIVITY_TYPES, ATTENDED_STATUSES, COUNTED_STATUSES, DEFAULT_STATUS_BY_TYPE)

DEFAULT_TZ = 'America/Mexico_City'


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    is_scout_event = fields.Boolean(string='Actividad scout', index=True,
                                    help='Actividad del grupo o de una sección.')
    scout_scope = fields.Selection([('group', 'Todo el grupo'), ('section', 'Sección(es)')],
                                   string='Alcance', default='section')
    scout_section_ids = fields.Many2many('scout.section', 'calendar_event_scout_section_rel', 'event_id',
                                         'section_id', string='Secciones')
    scout_activity_type = fields.Selection(ACTIVITY_TYPES, string='Tipo de actividad', default='regular')
    scout_theme = fields.Char(string='Mística / temática', help='Ej. La selva invasora, Los perros jaros.')
    scout_objective = fields.Text(string='Objetivos')
    scout_responsible_ids = fields.Many2many('res.partner', 'calendar_event_scout_responsible_rel', 'event_id',
                                             'partner_id', string='Responsables',
                                             domain="[('is_scout', '=', True), ('scout_type', '=', 'leader')]",
                                             help='Dirigentes que organizan la actividad (Akela, Raksha…).')
    scout_recognitions = fields.Char(string='Reconocimientos / entregas',
                                     help='Ej. Entrega de Promesa, entrega de adelantos.')
    scout_done = fields.Boolean(string='Realizada', tracking=True)
    scout_result = fields.Text(string='Resultado / observaciones',
                               help='Qué se logró, incidencias o pendientes de la actividad.')
    scout_date = fields.Date(string='Fecha (local)', compute='_compute_scout_date', store=True, index=True)
    scout_attendance_line_ids = fields.One2many('scout.attendance.line', 'event_id', string='Lista de asistencia')
    scout_attendance_closed = fields.Boolean(string='Lista cerrada', copy=False, tracking=True)
    scout_present_count = fields.Integer(compute='_compute_scout_counts', string='Asistieron')
    scout_counted_count = fields.Integer(compute='_compute_scout_counts', string='Contables')
    scout_attendance_pct = fields.Float(compute='_compute_scout_counts', string='% Asistencia')
    scout_section_names = fields.Char(compute='_compute_scout_section_names', string='Sección / grupo')

    @api.depends('start', 'start_date', 'allday')
    def _compute_scout_date(self):
        tz = pytz.timezone(self.env.user.tz or DEFAULT_TZ)
        for event in self:
            if event.allday and event.start_date:
                event.scout_date = event.start_date
            elif event.start:
                event.scout_date = pytz.utc.localize(event.start).astimezone(tz).date()
            else:
                event.scout_date = False

    @api.depends('scout_attendance_line_ids.status')
    def _compute_scout_counts(self):
        for event in self:
            lines = event.scout_attendance_line_ids
            present = len(lines.filtered(lambda l: l.status in ATTENDED_STATUSES))
            counted = len(lines.filtered(lambda l: l.status in COUNTED_STATUSES))
            event.scout_present_count = present
            event.scout_counted_count = counted
            event.scout_attendance_pct = present / counted * 100.0 if counted else 0.0

    @api.depends('scout_scope', 'scout_section_ids')
    def _compute_scout_section_names(self):
        for event in self:
            if not event.is_scout_event:
                event.scout_section_names = False
            elif event.scout_scope == 'group' or not event.scout_section_ids:
                event.scout_section_names = _('Todo el grupo')
            else:
                event.scout_section_names = ', '.join(event.scout_section_ids.mapped('display_name'))

    @api.onchange('scout_scope')
    def _onchange_scout_scope(self):
        if self.scout_scope == 'group':
            self.scout_section_ids = False

    # ------------------------------------------------------------------ permisos por sección
    def _scout_user_can_manage(self):
        """Mesa directiva y jefatura editan todo; un jefe de sección solo las actividades de sus secciones."""
        if self.env.su or self.env.user.has_group('scout_management.group_scout_officer'):
            return True
        if not self.env.user.has_group('scout_management.group_scout_leader'):
            return True  # usuarios sin rol scout: reglas estándar del calendario
        my_sections = self.env['scout.section'].search([('leader_ids', 'in', self.env.uid)])
        for event in self.filtered('is_scout_event'):
            if event.scout_scope == 'group' or not event.scout_section_ids:
                if event.create_uid and event.create_uid != self.env.user:
                    return False
            elif event.scout_section_ids - my_sections:
                return False
        return True

    def _scout_check_manage(self):
        if not self._scout_user_can_manage():
            raise AccessError(_('Solo puedes modificar actividades de tus secciones. '
                                'Los eventos de todo el grupo los edita la jefatura o la mesa directiva.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('is_scout_event') or self.env.context.get('default_is_scout_event'):
                vals.setdefault('privacy', 'public')
                vals.setdefault('show_as', 'busy')
        events = super().create(vals_list)
        scout_events = events.filtered('is_scout_event')
        scout_events._scout_check_manage()
        # La lista de asistencia queda precargada con los miembros y dirigentes de la sección.
        if not self.env.context.get('scout_no_autoload'):
            scout_events.filtered(
                lambda e: e.scout_scope == 'section' and e.scout_section_ids
                and e.scout_activity_type not in ('none', 'council', 'training')).action_scout_load_members()
        return events

    def write(self, vals):
        self.filtered('is_scout_event')._scout_check_manage()
        res = super().write(vals)
        if 'scout_section_ids' in vals or 'scout_scope' in vals:
            self.filtered('is_scout_event')._scout_check_manage()
        return res

    def unlink(self):
        scout_events = self.filtered('is_scout_event')
        scout_events._scout_check_manage()
        if scout_events.filtered('scout_attendance_closed'):
            raise UserError(_('No se puede eliminar una actividad con lista de asistencia cerrada.'))
        return super().unlink()

    # ------------------------------------------------------------------ asistencia
    def _scout_members_to_call(self):
        self.ensure_one()
        """Miembros de la(s) sección(es) más sus dirigentes (jefes, subjefes y quien la tenga a cargo)."""
        Partner = self.env['res.partner'].sudo()
        domain = [('is_scout', '=', True), ('scout_state', 'in', ('trial', 'active'))]
        if self.scout_scope == 'section' and self.scout_section_ids:
            sections = self.scout_section_ids
            members = Partner.search(domain + ['|', ('scout_section_id', 'in', sections.ids),
                                               ('scout_leading_section_ids', 'in', sections.ids)])
            members |= sections.leader_ids.partner_id.filtered('is_scout')
            return members.sorted(lambda p: (p.scout_type == 'leader', p.scout_patrol_id.sequence or 0, p.name or ''))
        return Partner.search(domain)

    def action_scout_load_members(self):
        for event in self:
            if event.scout_attendance_closed:
                raise UserError(_('La lista ya está cerrada; reábrela para modificarla.'))
            existing = event.scout_attendance_line_ids.member_id
            status = DEFAULT_STATUS_BY_TYPE.get(event.scout_activity_type, 'absent')
            members = event._scout_members_to_call() - existing
            event.scout_attendance_line_ids = [
                fields.Command.create({'member_id': m.id, 'status': status}) for m in members]
        return True

    def action_scout_mark_all_present(self):
        for event in self:
            event.scout_attendance_line_ids.filtered(lambda l: l.status == 'absent').write({'status': 'present'})

    def action_scout_close_attendance(self):
        for event in self:
            if not event.scout_attendance_line_ids:
                event.action_scout_load_members()
            event.write({'scout_attendance_closed': True, 'scout_done': True})

    def action_scout_reopen_attendance(self):
        self.write({'scout_attendance_closed': False})

    def action_scout_mark_done(self):
        self.write({'scout_done': True})


class ScoutAttendanceLine(models.Model):
    _name = 'scout.attendance.line'
    _description = 'Registro de asistencia'
    _order = 'date desc, member_id'

    event_id = fields.Many2one('calendar.event', string='Actividad', required=True, ondelete='cascade', index=True)
    member_id = fields.Many2one('res.partner', string='Miembro', required=True, index=True, ondelete='cascade',
                                domain="[('is_scout', '=', True)]")
    status = fields.Selection([
        ('present', 'SI - Asistió'), ('absent', 'NO - No asistió'), ('camp', 'CA - Campamento'),
        ('outing', 'M - Museo / salida'), ('dropped', 'B - Baja'), ('na', 'N/A - No aplica'),
        ('no_roll', '/ - No se tomó lista'),
    ], string='Asistencia', required=True, default='absent')
    status_code = fields.Char(compute='_compute_flags', store=True, string='Código')
    attended = fields.Integer(compute='_compute_flags', store=True, string='Asistió', aggregator='sum')
    counts = fields.Integer(compute='_compute_flags', store=True, string='Sesiones contables', aggregator='sum')
    note = fields.Char(string='Nota')
    date = fields.Date(related='event_id.scout_date', store=True, index=True, string='Fecha')
    section_id = fields.Many2one('scout.section', string='Sección', index=True,
                                 help='Sección del miembro al momento de pasar lista.')
    patrol_id = fields.Many2one(related='member_id.scout_patrol_id', string='Equipo')
    closed = fields.Boolean(related='event_id.scout_attendance_closed', string='Lista cerrada')

    _event_member_uniq = models.Constraint(
        'UNIQUE (event_id, member_id)', 'El miembro ya está en la lista de esta actividad.')

    @api.depends('status')
    def _compute_flags(self):
        from .scout_common import STATUS_CODES
        for line in self:
            line.status_code = STATUS_CODES.get(line.status, '')
            line.attended = 1 if line.status in ATTENDED_STATUSES else 0
            line.counts = 1 if line.status in COUNTED_STATUSES else 0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('member_id') and not vals.get('section_id'):
                vals['section_id'] = self.env['res.partner'].sudo().browse(vals['member_id']).scout_section_id.id
        return super().create(vals_list)

    def write(self, vals):
        if any(line.event_id.scout_attendance_closed for line in self) and 'status' in vals:
            raise UserError(_('La lista está cerrada; reábrela para corregir la asistencia.'))
        return super().write(vals)
