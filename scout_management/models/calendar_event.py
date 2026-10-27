# -*- coding: utf-8 -*-
import pytz

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .scout_common import (ACTIVITY_TYPES, ATTENDED_STATUSES, COUNTED_STATUSES, DEFAULT_STATUS_BY_TYPE)

DEFAULT_TZ = 'America/Mexico_City'

# Colores del calendario general: rojo = todo el grupo, amarillo = salida de sección, verde = ordinaria.
SCOUT_CATEGORIES = [('group', 'Todo el grupo (rojo)'), ('outing', 'Salida de sección (amarillo)'),
                    ('ordinary', 'Ordinaria (verde)')]
CATEGORY_HEX = {'group': '#d7262f', 'outing': '#f2b705', 'ordinary': '#2e9e44'}
CATEGORY_COLOR_INDEX = {'group': 1, 'outing': 3, 'ordinary': 10}
PLAN_STATES = [('draft', 'Borrador'), ('proposed', 'Enviada al consejo'), ('reviewed', 'Revisada en consejo'),
               ('approved', 'Visto bueno'), ('changes', 'Con cambios')]
# Cambiar estos datos de una actividad ya aprobada la regresa a revisión.
PLAN_KEY_FIELDS = ('start', 'stop', 'scout_activity_type', 'scout_category', 'location', 'scout_section_ids')


ATTENDANCE_FIELDS = {'scout_attendance_line_ids', 'scout_attendance_closed', 'scout_done', 'scout_result'}


_logger = logging.getLogger(__name__)

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
    scout_category = fields.Selection(SCOUT_CATEGORIES, string='Clasificación', compute='_compute_scout_category',
                                      store=True, readonly=False, tracking=True,
                                      help='Rojo: todo el grupo (la genera el jefe de grupo). Amarillo: salida de la '
                                           'sección (la autoriza el jefe de grupo). Verde: actividad ordinaria.')
    scout_main_section_id = fields.Many2one('scout.section', string='Sección (color)', compute='_compute_scout_main_section',
                                            store=True, index=True,
                                            help='Primera sección de la actividad; da el color en el calendario.')
    scout_section_color = fields.Integer(string='Color en el calendario', compute='_compute_scout_section_color', store=True,
                                 help='Color de la sección (Configuración → Secciones); las actividades de grupo van en gris.')
    scout_color = fields.Integer(string='Color', compute='_compute_scout_color', store=True)
    scout_color_hex = fields.Char(compute='_compute_scout_color')
    scout_plan_state = fields.Selection(PLAN_STATES, string='Aprobación', default='draft', tracking=True, copy=False,
                                        index=True)
    scout_needs_manager = fields.Boolean(compute='_compute_scout_color', string='Requiere al jefe de grupo')
    scout_approved_by_id = fields.Many2one('res.users', string='Visto bueno de', readonly=True, copy=False)
    scout_approved_date = fields.Date(string='Fecha del visto bueno', readonly=True, copy=False)
    scout_plan_note = fields.Char(string='Comentario del consejo', tracking=True, copy=False)
    scout_date_end = fields.Date(string='Termina (local)', compute='_compute_scout_date', store=True)

    @api.depends('start', 'start_date', 'stop', 'stop_date', 'allday')
    def _compute_scout_date(self):
        tz = pytz.timezone(self.env.user.tz or DEFAULT_TZ)
        for event in self:
            if event.allday and event.start_date:
                event.scout_date = event.start_date
                event.scout_date_end = event.stop_date or event.start_date
            elif event.start:
                event.scout_date = pytz.utc.localize(event.start).astimezone(tz).date()
                event.scout_date_end = pytz.utc.localize(event.stop).astimezone(tz).date() \
                    if event.stop else event.scout_date
            else:
                event.scout_date = event.scout_date_end = False

    @api.depends('scout_scope', 'scout_activity_type')
    def _compute_scout_category(self):
        for event in self:
            if event.scout_scope == 'group':
                event.scout_category = 'group'
            elif event.scout_activity_type in ('camp', 'outing'):
                event.scout_category = 'outing'
            else:
                event.scout_category = 'ordinary'

    @api.depends('is_scout_event', 'scout_scope', 'scout_section_ids', 'scout_section_ids.color')
    def _compute_scout_section_color(self):
        for event in self:
            section = event.scout_section_ids[:1] if event.is_scout_event and event.scout_scope == 'section' else False
            event.scout_section_color = section.color if section else 0

    @api.depends('is_scout_event', 'scout_scope', 'scout_section_ids')
    def _compute_scout_main_section(self):
        for event in self:
            event.scout_main_section_id = event.scout_section_ids[:1] \
                if event.is_scout_event and event.scout_scope == 'section' else False

    @api.depends('name', 'is_scout_event', 'scout_scope', 'scout_section_ids.short_name',
                 'scout_section_ids.name', 'scout_section_ids.unit_name')
    def _compute_display_name(self):
        """En el calendario se lee primero la sección: "Lobatos · Sesión" en vez de "Sesión Manada de Lobatos…"."""
        super()._compute_display_name()
        for event in self.filtered('is_scout_event'):
            if event.display_name == _('Busy'):
                continue
            name = event.name or ''
            if event.scout_scope == 'group' or not event.scout_section_ids:
                event.display_name = _('Grupo · %s', name)
                continue
            sections = event.scout_section_ids.sudo()
            for section in sections:
                for long_name in filter(None, (section.display_name, section.unit_name, section.name)):
                    name = name.replace(' ' + long_name, '').replace(long_name, '')
            name = name.strip(' -·') or event.name
            label = ' / '.join(sections[:2].mapped(lambda s: s._scout_short()))
            if len(sections) > 2:
                label += ' +%s' % (len(sections) - 2)
            event.display_name = '%s · %s' % (label, name)

    @api.depends('scout_category', 'is_scout_event')
    def _compute_scout_color(self):
        for event in self:
            category = event.scout_category or 'ordinary'
            event.scout_color = CATEGORY_COLOR_INDEX[category] if event.is_scout_event else 0
            event.scout_color_hex = CATEGORY_HEX[category]
            event.scout_needs_manager = category in ('group', 'outing')

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

    def _scout_user_can_take_attendance(self):
        """Pasar lista: mesa directiva, jefatura, tesorería y secretaría en todas; cada jefe en las actividades
        de sus secciones y en las de todo el grupo."""
        if self.env.su or self.env.user.has_group('scout_management.group_scout_officer'):
            return True
        my_sections = self.env.user._scout_leader_sections()
        if not my_sections:
            return False
        for event in self.filtered('is_scout_event'):
            if event.scout_scope == 'section' and event.scout_section_ids and not (event.scout_section_ids & my_sections):
                return False
        return True

    def _scout_check_attendance(self):
        if not self._scout_user_can_take_attendance():
            raise AccessError(_('No puedes pasar lista en esta actividad.'))

    def _scout_check_manage(self):
        if not self._scout_user_can_manage():
            raise AccessError(_('Solo puedes modificar actividades de tus secciones. '
                                'Los eventos de todo el grupo los edita la jefatura o la mesa directiva.'))

    @api.model
    def _scout_is_group_chief(self):
        return self.env['scout.approval.mixin']._scout_is_manager()

    @api.model_create_multi
    def create(self, vals_list):
        chief = self._scout_is_group_chief()
        for vals in vals_list:
            if vals.get('is_scout_event') or self.env.context.get('default_is_scout_event'):
                vals.setdefault('privacy', 'public')
                vals.setdefault('show_as', 'busy')
                internal = vals.get('scout_activity_type') in ('council', 'training', 'none')
                if 'scout_plan_state' not in vals:
                    vals['scout_plan_state'] = 'approved' if chief or internal else 'draft'
                if vals['scout_plan_state'] == 'approved':
                    vals.setdefault('scout_approved_by_id', self.env.uid)
                    vals.setdefault('scout_approved_date', fields.Date.context_today(self))
        if any(v.get('is_scout_event') for v in vals_list) or self.env.context.get('default_is_scout_event'):
            # Sin el bloque "Organizado por…" que el calendario agrega a la descripción
            self = self.with_context(skip_contact_description=True)
        events = super().create(vals_list)
        scout_events = events.filtered('is_scout_event')
        if not chief and not self.env.context.get('scout_allow_group_event') \
                and scout_events.filtered(lambda e: e.scout_category == 'group'):
            raise AccessError(_('Las actividades de todo el grupo (rojas) las genera el jefe de grupo.'))
        scout_events._scout_check_manage()
        # La lista de asistencia queda precargada con los miembros y dirigentes de la sección.
        if not self.env.context.get('scout_no_autoload'):
            scout_events.filtered(
                lambda e: e.scout_scope == 'section' and e.scout_section_ids
                and e.scout_activity_type not in ('none', 'council', 'training')).action_scout_load_members()
        return events

    def write(self, vals):
        scout_events = self.filtered('is_scout_event')
        if vals and set(vals) <= ATTENDANCE_FIELDS:
            scout_events._scout_check_attendance()
            return super(CalendarEvent, self.sudo() if scout_events and not self.env.su else self).write(vals)
        scout_events._scout_check_manage()
        chief = self._scout_is_group_chief()
        if not chief and 'scout_plan_state' in vals and vals['scout_plan_state'] == 'approved' \
                and not self.env.context.get('scout_plan_approving'):
            raise AccessError(_('El visto bueno lo da el jefe de grupo o el consejo.'))
        recheck = self.browse()
        if not chief and set(PLAN_KEY_FIELDS) & set(vals) and 'scout_plan_state' not in vals:
            recheck = scout_events.filtered(lambda e: e.scout_plan_state in ('approved', 'reviewed'))
        res = super().write(vals)
        if 'scout_section_ids' in vals or 'scout_scope' in vals or 'scout_category' in vals:
            if not chief and not self.env.context.get('scout_allow_group_event') \
                    and scout_events.filtered(lambda e: e.scout_category == 'group'):
                raise AccessError(_('Las actividades de todo el grupo (rojas) las genera el jefe de grupo.'))
            scout_events._scout_check_manage()
        if recheck:
            super(CalendarEvent, recheck).write({'scout_plan_state': 'proposed'})
            for event in recheck:
                event.message_post(body=_('Se cambió la fecha, el tipo o el lugar: la actividad regresa a revisión.'))
        return res

    # ------------------------------------------------------------------ aprobación del programa
    def _scout_can_review(self):
        return self.env.su or self.env.user.has_group('scout_management.group_scout_officer')

    def action_scout_plan_submit(self):
        """El jefe de sección envía su programa al consejo."""
        events = self.filtered(lambda e: e.is_scout_event and e.scout_plan_state in ('draft', 'changes'))
        events.with_context(scout_plan_approving=True).write({'scout_plan_state': 'proposed'})
        sections = events.scout_section_ids
        for section in sections:
            mine = events.filtered(lambda e: section in e.scout_section_ids)
            outings = mine.filtered(lambda e: e.scout_category == 'outing')
            note = _('%(n)s actividad(es) por revisar en consejo%(out)s.', n=len(mine),
                     out=_('; %s salida(s) necesitan autorización del jefe de grupo', len(outings)) if outings else '')
            self.env['scout.approval.mixin']._scout_request_approval_for(
                section.sudo(), _('Programa de %s enviado al consejo', section.display_name), note)
        return events

    def action_scout_plan_review(self):
        if not self._scout_can_review():
            raise AccessError(_('La revisión en consejo la registra secretaría o la jefatura.'))
        self.filtered(lambda e: e.scout_plan_state in ('draft', 'proposed', 'changes')).with_context(
            scout_plan_approving=True).write({'scout_plan_state': 'reviewed'})

    def action_scout_plan_approve(self):
        """Visto bueno: las salidas (amarillas) y las de grupo (rojas) solo las aprueba el jefe de grupo."""
        chief = self._scout_is_group_chief()
        if not chief and not self._scout_can_review():
            raise AccessError(_('El visto bueno lo da el jefe de grupo.'))
        if not chief and self.filtered('scout_needs_manager'):
            raise AccessError(_('Las salidas de sección (amarillas) las autoriza el jefe de grupo.'))
        todo = self.filtered(lambda e: e.scout_plan_state != 'approved')
        todo.with_context(scout_plan_approving=True).write({
            'scout_plan_state': 'approved', 'scout_approved_by_id': self.env.uid,
            'scout_approved_date': fields.Date.context_today(self)})
        for event in todo:
            event.message_post(body=_('Visto bueno de %s.', self.env.user.name))
        sections = todo.scout_section_ids
        if sections and not self.env['calendar.event'].search_count(
                [('scout_section_ids', 'in', sections.ids), ('scout_plan_state', 'in', ('proposed', 'reviewed'))]):
            self.env['scout.approval.mixin']._scout_close_approval_for(sections.sudo(), _('Programa aprobado'))
        return True

    def action_scout_plan_changes(self):
        if not self._scout_can_review():
            raise AccessError(_('Solo el consejo puede pedir cambios.'))
        self.with_context(scout_plan_approving=True).write({'scout_plan_state': 'changes'})
        for event in self:
            event.message_post(body=_('El consejo pidió cambios: %s', event.scout_plan_note or '-'))

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

    def _scout_prune_attendance(self, include_closed=False):
        """Quita de la lista a quien ya no es de la sección (cambió de sección o se corrigió su ficha) y quedó
        marcado como falta. Se respeta a quien asistió y a quien sí estaba en esa sección ese día (pasó después)."""
        removed = self.env['scout.attendance.line']
        for event in self.sudo().filtered(lambda e: e.scout_scope == 'section' and e.scout_section_ids):
            if event.scout_attendance_closed and not include_closed:
                continue
            sections = event.scout_section_ids
            current = event._scout_members_to_call()
            for line in event.scout_attendance_line_ids:
                member = line.member_id
                if member in current or line.status != 'absent':
                    continue
                # ¿Estaba en esa sección el día de la actividad y se cambió después? Entonces la falta es válida.
                moved_later = member.sudo().scout_route_ids.filtered(
                    lambda r: r.section_id in sections and r.date_pass_real
                    and (not event.scout_date or r.date_pass_real > event.scout_date))
                if moved_later:
                    continue
                removed |= line
        if removed:
            _logger.info('Scouts: %s registro(s) de asistencia de miembros de otra sección eliminados', len(removed))
            removed.unlink()
        return removed

    def action_scout_load_members(self):
        self._scout_check_attendance()
        for event in self.sudo():
            if event.scout_attendance_closed:
                raise UserError(_('La lista ya está cerrada; reábrela para modificarla.'))
            event._scout_prune_attendance()
            existing = event.scout_attendance_line_ids.member_id
            status = DEFAULT_STATUS_BY_TYPE.get(event.scout_activity_type, 'absent')
            members = event._scout_members_to_call() - existing
            event.scout_attendance_line_ids = [
                fields.Command.create({'member_id': m.id, 'status': status}) for m in members]
        return True

    def action_scout_mark_all_present(self):
        self._scout_check_attendance()
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
    _order = 'date desc, member_type desc, member_id'

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
    member_type = fields.Selection([('youth', 'Miembro'), ('leader', 'Dirigente')], string='Tipo',
                                   compute='_compute_member_type', store=True, index=True)
    member_role = fields.Char(string='Cargo / equipo', compute='_compute_member_type')
    closed = fields.Boolean(related='event_id.scout_attendance_closed', string='Lista cerrada')

    @api.depends('member_id.scout_type', 'member_id.scout_position_id', 'member_id.scout_patrol_id')
    def _compute_member_type(self):
        for line in self:
            member = line.member_id.sudo()
            is_leader = member.scout_type == 'leader'
            line.member_type = 'leader' if is_leader else 'youth'
            line.member_role = (member.scout_position_id.name or _('Dirigente')) if is_leader \
                else (member.scout_patrol_id.name or '')

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
