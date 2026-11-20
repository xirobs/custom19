# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

STATE_SELECTION = [
    ('pending', 'Pendiente'),
    ('on_time', 'A tiempo'),
    ('late', 'Retardo'),
    ('substitute', 'Sustituto'),
    ('excused', 'Justificada'),
    ('absent', 'Falta'),
]


class BniAttendance(models.Model):
    _name = 'bni.attendance'
    _description = 'Asistencia BNI'
    _order = 'event_date desc, event_id, attendee_type, name'

    event_id = fields.Many2one('bni.event', 'Evento', required=True, ondelete='cascade', index=True)
    event_date = fields.Date(related='event_id.event_date', store=True, string='Fecha')
    modality = fields.Selection(related='event_id.modality', store=True, string='Modalidad')
    event_state = fields.Selection(related='event_id.state', string='Estado del evento')
    company_id = fields.Many2one(related='event_id.company_id', store=True)

    attendee_type = fields.Selection([
        ('member', 'Miembro'),
        ('guest', 'Invitado'),
    ], string='Tipo', required=True, default='member', index=True)
    member_id = fields.Many2one('bni.member', 'Miembro', index=True, ondelete='restrict',
                                domain="[('state', '=', 'active')]")
    name = fields.Char('Nombre', compute='_compute_from_member', store=True, readonly=False,
                       required=True, precompute=True)
    company_name = fields.Char('Empresa', compute='_compute_from_member', store=True, readonly=False,
                               precompute=True)
    invited_by_id = fields.Many2one('bni.member', 'Invitado por')

    # Sustituto (va en la línea del miembro al que reemplaza)
    is_substitute = fields.Boolean('Asistió sustituto')
    substitute_name = fields.Char('Nombre del sustituto')
    substitute_company = fields.Char('Empresa del sustituto')

    check_in = fields.Datetime('Hora de llegada')
    check_in_local = fields.Char('Llegada', compute='_compute_check_in_local')
    minutes_late = fields.Integer('Min. de retardo', compute='_compute_state', store=True)
    state = fields.Selection(
        STATE_SELECTION, string='Estatus', compute='_compute_state', store=True,
        readonly=False, index=True)
    checkin_method = fields.Selection([
        ('qr', 'QR'),
        ('manual', 'Manual'),
    ], string='Medio de registro')
    observation = fields.Text('Observación')

    _member_event_uniq = models.Constraint(
        'UNIQUE(event_id, member_id)', 'El miembro ya está en la lista de este evento.')

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('member_id')
    def _compute_from_member(self):
        for line in self:
            if line.member_id:
                line.name = line.member_id.name
                line.company_name = line.member_id.company_name
            else:
                line.name = line.name
                line.company_name = line.company_name

    @api.depends('check_in', 'event_id.tz')
    def _compute_check_in_local(self):
        for line in self:
            line.check_in_local = line.event_id.format_local(line.check_in) if line.check_in else ''

    @api.depends('check_in', 'is_substitute', 'event_id.checkin_limit', 'event_id.state')
    def _compute_state(self):
        for line in self:
            event = line.event_id
            line.minutes_late = 0
            if line.check_in and event.checkin_limit and line.check_in > event.checkin_limit:
                line.minutes_late = int((line.check_in - event.checkin_limit).total_seconds() // 60)
            if line.is_substitute:
                line.state = 'substitute'
            elif line.check_in:
                line.state = 'late' if line.minutes_late > 0 else 'on_time'
            elif line.state == 'excused':
                line.state = 'excused'  # estatus manual: se respeta
            else:
                line.state = 'absent' if event.state == 'closed' else 'pending'

    # ------------------------------------------------------------------
    # Validaciones
    # ------------------------------------------------------------------
    @api.constrains('attendee_type', 'member_id', 'is_substitute', 'substitute_name')
    def _check_consistency(self):
        for line in self:
            if line.attendee_type == 'member' and not line.member_id:
                raise ValidationError(_('Las líneas de miembro requieren seleccionar al miembro.'))
            if line.attendee_type == 'guest' and (line.member_id or line.is_substitute):
                raise ValidationError(_('Un invitado no se vincula a un miembro ni puede ser sustituto.'))
            if line.is_substitute and not line.substitute_name:
                raise ValidationError(_('Indica el nombre del sustituto de %s.', line.name))

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def action_check_in_now(self):
        for line in self:
            if line.event_id.state == 'closed':
                raise UserError(_('El evento ya está cerrado.'))
            if not line.check_in:
                line.write({'check_in': fields.Datetime.now(), 'checkin_method': 'manual'})
        return True

    def action_clear_check_in(self):
        self.write({'check_in': False, 'checkin_method': False, 'is_substitute': False,
                    'substitute_name': False, 'substitute_company': False})
        return True

    def action_mark_excused(self):
        self.filtered(lambda l: not l.check_in and not l.is_substitute).write({'state': 'excused'})
        return True

    def action_add_member_note(self):
        """Abre la bitácora del miembro para registrar una observación ligada al evento."""
        self.ensure_one()
        if not self.member_id:
            raise UserError(_('Solo aplica a miembros.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Nueva observación'),
            'res_model': 'bni.member.note',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_member_id': self.member_id.id,
                'default_event_id': self.event_id.id,
                'default_note_type': 'attendance',
            },
        }

    def add_member_observation(self, text):
        """Observación capturada por el propio miembro desde la página del QR."""
        self.ensure_one()
        text = (text or '').strip()[:1000]
        if not text or not self.member_id:
            return False
        self.observation = '%s\n%s' % (self.observation, text) if self.observation else text
        self.env['bni.member.note'].create({
            'member_id': self.member_id.id,
            'event_id': self.event_id.id,
            'date': self.event_id.event_date or fields.Date.context_today(self),
            'note_type': 'member',
            'note': text,
        })
        return True
