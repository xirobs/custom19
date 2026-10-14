# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ScoutProgressionStage(models.Model):
    _name = 'scout.progression.stage'
    _description = 'Adelanto, recorrido o especialidad'
    _order = 'stage_type, sequence, id'

    name = fields.Char(string='Nombre', required=True, translate=True)
    sequence = fields.Integer(default=10)
    stage_type = fields.Selection([
        ('promise', 'Promesa'),
        ('stage', 'Adelanto / recorrido'),
        ('terminal', 'Insignia terminal'),
        ('specialty', 'Especialidad'),
    ], string='Tipo', required=True, default='stage')
    section_ids = fields.Many2many('scout.section', string='Secciones',
                                   help='Vacío = aplica a todas las secciones.')
    description = fields.Text(string='Requisitos / descripción')
    image_128 = fields.Image(string='Insignia', max_width=128, max_height=128)
    active = fields.Boolean(default=True)


class ScoutMemberProgress(models.Model):
    _name = 'scout.member.progress'
    _description = 'Avance del miembro'
    _order = 'date_achieved desc, id desc'

    member_id = fields.Many2one('res.partner', string='Miembro', required=True, ondelete='cascade', index=True,
                                domain="[('is_scout', '=', True)]")
    stage_id = fields.Many2one('scout.progression.stage', string='Adelanto', required=True, ondelete='restrict')
    stage_type = fields.Selection(related='stage_id.stage_type', store=True)
    state = fields.Selection([('in_progress', 'En curso'), ('achieved', 'Logrado')], string='Estado',
                             default='in_progress', required=True)
    date_start = fields.Date(string='Inicio', default=fields.Date.context_today)
    date_achieved = fields.Date(string='Fecha de logro')
    leader_id = fields.Many2one('res.partner', string='Dirigente que da seguimiento',
                                domain="[('is_scout', '=', True), ('scout_type', '=', 'leader')]")
    note = fields.Char(string='Nota')
    section_id = fields.Many2one(related='member_id.scout_section_id', store=True, string='Sección')

    _member_stage_uniq = models.Constraint(
        'UNIQUE (member_id, stage_id)', 'El miembro ya tiene registrado ese adelanto.')

    @api.constrains('state', 'date_achieved')
    def _check_date(self):
        for progress in self:
            if progress.state == 'achieved' and not progress.date_achieved:
                raise ValidationError(_('Indica la fecha en que se logró el adelanto.'))

    @api.onchange('state')
    def _onchange_state(self):
        if self.state == 'achieved' and not self.date_achieved:
            self.date_achieved = fields.Date.context_today(self)

    @api.depends('member_id', 'stage_id')
    def _compute_display_name(self):
        for progress in self:
            progress.display_name = f'{progress.member_id.name or ""} - {progress.stage_id.name or ""}'
