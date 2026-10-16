# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ScoutSection(models.Model):
    _name = 'scout.section'
    _description = 'Sección scout'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, id'

    name = fields.Char(string='Sección', required=True, translate=True)
    unit_name = fields.Char(string='Nombre de la unidad', help='Ej. Colonia Ágape, Manada Seeonee')
    code = fields.Char(string='Código')
    sequence = fields.Integer(default=10)
    is_leadership = fields.Boolean(string='Es Jefatura', help='Sección que agrupa a la jefatura del grupo.')
    age_min = fields.Integer(string='Edad mínima')
    age_max = fields.Integer(string='Edad máxima',
                             help='Al cumplir esta edad el miembro se marca como "próximo a pasar".')
    transition_age = fields.Integer(string='Edad de paso',
                                    help='Edad a la que se pasa a la sección siguiente. Vacío = edad máxima.')
    transition_notice_months = fields.Integer(string='Avisar con (meses)', default=6,
                                              help='Marca como "próximo a pasar" a quien cumpla la edad de paso '
                                                   'dentro de este número de meses.')
    next_section_id = fields.Many2one('scout.section', string='Sección siguiente')
    leader_ids = fields.Many2many('res.users', 'scout_section_leader_rel', 'section_id', 'user_id',
                                  string='Dirigentes con acceso', tracking=True,
                                  help='Usuarios que pueden pasar lista y ver a los miembros de esta sección.')
    color = fields.Integer(string='Color')
    report_color = fields.Char(string='Color del informe', default='#0f3049',
                               help='Color hexadecimal para encabezados de programas e informes.')
    patrol_label = fields.Char(string='Nombre de los equipos', default='Equipo',
                               help='Ej. Madriguera, Seisena, Patrulla, Equipo.')
    patrol_role_label = fields.Char(string='Nombre del guía', default='Guía',
                                    help='Ej. Madera, Seisenero(a), Guía de patrulla.')
    patrol_subrole_label = fields.Char(string='Nombre del subguía', default='Subguía',
                                       help='Ej. Submadera, Subseisenero(a), Subguía.')
    member_ids = fields.One2many('res.partner', 'scout_section_id', string='Miembros')
    member_count = fields.Integer(compute='_compute_member_count', string='Miembros activos')
    patrol_ids = fields.One2many('scout.patrol', 'section_id', string='Equipos')
    company_id = fields.Many2one('res.company', string='Grupo', required=True,
                                 default=lambda self: self.env.company)
    active = fields.Boolean(default=True)

    @api.depends('name', 'unit_name')
    def _compute_display_name(self):
        for section in self:
            section.display_name = section.unit_name or section.name

    @api.depends('member_ids.scout_state')
    def _compute_member_count(self):
        data = dict(self.env['res.partner'].sudo()._read_group(
            [('scout_section_id', 'in', self.ids), ('is_scout', '=', True),
             ('scout_state', 'in', ('trial', 'active'))],
            ['scout_section_id'], ['__count']))
        for section in self:
            section.member_count = data.get(section, 0)

    @api.constrains('age_min', 'age_max')
    def _check_ages(self):
        for section in self:
            if section.age_max and section.age_min > section.age_max:
                raise ValidationError(_('La edad mínima no puede ser mayor que la máxima.'))

    def action_view_members(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('scout_management.action_scout_member')
        action['domain'] = [('is_scout', '=', True), ('scout_section_id', '=', self.id)]
        action['context'] = {'default_is_scout': True, 'default_scout_section_id': self.id,
                             'search_default_current': 1}
        return action


class ScoutPatrol(models.Model):
    _name = 'scout.patrol'
    _description = 'Equipo (seisena / patrulla)'
    _order = 'section_id, sequence, id'

    name = fields.Char(string='Equipo', required=True)
    sequence = fields.Integer(default=10)
    section_id = fields.Many2one('scout.section', string='Sección', required=True, ondelete='cascade')
    color_name = fields.Selection([
        ('yellow', 'Amarillo'), ('blue', 'Azul'), ('red', 'Rojo'), ('green', 'Verde'),
        ('orange', 'Naranja'), ('purple', 'Morado'), ('brown', 'Café'), ('white', 'Blanco'),
    ], string='Color del equipo')
    member_ids = fields.One2many('res.partner', 'scout_patrol_id', string='Integrantes')
    follow_leader_id = fields.Many2one('res.partner', string='Dirigente que da seguimiento',
                                       domain="[('is_scout', '=', True), ('scout_type', '=', 'leader')]")
    company_id = fields.Many2one(related='section_id.company_id', store=True)
