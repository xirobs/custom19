# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ScoutMeeting(models.Model):
    """Acta de consejo de grupo o junta. La redacta la secretaría y la aprueba el jefe de grupo."""
    _name = 'scout.meeting'
    _description = 'Acta de consejo / junta'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'scout.approval.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(string='Folio', readonly=True, copy=False, default=lambda self: _('Nuevo'))
    title = fields.Char(string='Título', required=True, default=lambda self: _('Consejo de Grupo'))
    meeting_type = fields.Selection([
        ('council', 'Consejo de grupo'), ('council_special', 'Consejo extraordinario'),
        ('section', 'Junta de sección'), ('parents', 'Junta con padres de familia'),
        ('committee', 'Comité'),
    ], string='Tipo', required=True, default='council', tracking=True)
    event_id = fields.Many2one('calendar.event', string='Evento en calendario',
                               domain="[('is_scout_event', '=', True)]")
    date = fields.Datetime(string='Fecha y hora', required=True, default=fields.Datetime.now, tracking=True)
    mode = fields.Selection([('onsite', 'Presencial'), ('online', 'En línea'), ('hybrid', 'Mixta')],
                            string='Modalidad', default='onsite')
    location = fields.Char(string='Lugar / enlace')
    section_id = fields.Many2one('scout.section', string='Sección', help='Solo para juntas de sección.')
    chair_id = fields.Many2one('res.partner', string='Preside', domain="[('is_scout', '=', True)]")
    secretary_id = fields.Many2one('res.partner', string='Secretaría', domain="[('is_scout', '=', True)]",
                                   default=lambda self: self.env.user.partner_id)
    attendee_ids = fields.Many2many('res.partner', 'scout_meeting_attendee_rel', 'meeting_id', 'partner_id',
                                    string='Asistentes')
    absent_ids = fields.Many2many('res.partner', 'scout_meeting_absent_rel', 'meeting_id', 'partner_id',
                                  string='Ausentes / justificados')
    attendee_count = fields.Integer(compute='_compute_attendee_count', string='Núm. de asistentes')
    quorum = fields.Boolean(string='Hubo quórum', default=True)
    agenda = fields.Html(string='Orden del día')
    minutes = fields.Html(string='Desarrollo de la sesión')
    agreement_ids = fields.One2many('scout.meeting.agreement', 'meeting_id', string='Acuerdos')
    next_meeting = fields.Datetime(string='Próxima reunión')
    state = fields.Selection([
        ('draft', 'Borrador'), ('to_approve', 'Por aprobar'), ('approved', 'Aprobada'), ('cancel', 'Cancelada'),
    ], default='draft', required=True, tracking=True, string='Estado')
    approved_by_id = fields.Many2one('res.users', string='Aprobó', readonly=True, copy=False)
    approved_date = fields.Datetime(string='Fecha de aprobación', readonly=True, copy=False)
    company_id = fields.Many2one('res.company', string='Grupo', required=True,
                                 default=lambda self: self.env.company)

    @api.depends('attendee_ids')
    def _compute_attendee_count(self):
        for meeting in self:
            meeting.attendee_count = len(meeting.attendee_ids)

    @api.onchange('event_id')
    def _onchange_event_id(self):
        event = self.event_id
        if event:
            self.date = event.start
            self.location = event.location or self.location
            self.title = event.name
            self.mode = 'online' if (event.videocall_location if 'videocall_location' in event._fields else False) \
                else self.mode
            if not self.attendee_ids:
                self.attendee_ids = event.partner_ids | event.scout_responsible_ids

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('scout.meeting') or _('Nuevo')
        return super().create(vals_list)

    def write(self, vals):
        locked = {'agenda', 'minutes', 'agreement_ids', 'attendee_ids', 'date', 'title'}
        if locked & set(vals) and not self.env.context.get('scout_meeting_force') \
                and any(m.state == 'approved' for m in self):
            raise UserError(_('El acta ya está aprobada; no se puede modificar.'))
        return super().write(vals)

    def action_submit(self):
        for meeting in self.filtered(lambda m: m.state == 'draft'):
            if not meeting.minutes and not meeting.agreement_ids:
                raise UserError(_('Captura el desarrollo de la sesión o los acuerdos antes de enviar el acta.'))
            meeting.state = 'to_approve'
            meeting._scout_request_approval(_('Aprobar acta %(folio)s - %(title)s', folio=meeting.name,
                                              title=meeting.title))

    def action_approve(self):
        self._scout_check_manager(_('aprobar actas'))
        for meeting in self.filtered(lambda m: m.state == 'to_approve'):
            meeting.write({'state': 'approved', 'approved_by_id': self.env.user.id,
                           'approved_date': fields.Datetime.now()})
            meeting._scout_close_approval_activities(_('Aprobada'))
            # Notifica a los asistentes con usuario
            partners = meeting.attendee_ids.filtered(lambda p: p.user_ids)
            if partners:
                meeting.message_subscribe(partner_ids=partners.ids)
                meeting.message_post(body=_('Acta aprobada. Ya puedes consultarla e imprimirla.'),
                                     subtype_xmlid='mail.mt_comment', partner_ids=partners.ids)

    def action_reject(self):
        self._scout_check_manager(_('regresar actas'))
        self.filtered(lambda m: m.state == 'to_approve').write({'state': 'draft'})
        self._scout_close_approval_activities(_('Regresada para correcciones'))

    def action_cancel(self):
        self.filtered(lambda m: m.state != 'approved').write({'state': 'cancel'})

    def action_draft(self):
        self.filtered(lambda m: m.state == 'cancel').write({'state': 'draft'})


class ScoutMeetingAgreement(models.Model):
    _name = 'scout.meeting.agreement'
    _description = 'Acuerdo del acta'
    _order = 'meeting_id, sequence, id'

    meeting_id = fields.Many2one('scout.meeting', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Text(string='Acuerdo', required=True)
    responsible_id = fields.Many2one('res.partner', string='Responsable')
    due_date = fields.Date(string='Fecha compromiso')
    state = fields.Selection([('pending', 'Pendiente'), ('done', 'Cumplido'), ('cancel', 'Cancelado')],
                             default='pending', string='Estado')
    meeting_state = fields.Selection(related='meeting_id.state', string='Estado del acta')
