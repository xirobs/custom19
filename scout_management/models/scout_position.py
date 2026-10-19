# -*- coding: utf-8 -*-
from odoo import api, fields, models

ALL_SECTIONS_KINDS = ('group_chief', 'secretary', 'treasurer')

POSITION_KINDS = [
    ('member', 'Integrante'),
    ('guide', 'Guía (madera / seisenero / guía de patrulla)'),
    ('subguide', 'Subguía (submadera / subseisenero / subguía)'),
    ('scouter', 'Scouter'),
    ('assistant', 'Subjefe'),
    ('chief', 'Jefe'),
    ('candidate', 'Candidato a subjefe'),
    ('secretary', 'Secretaría'),
    ('treasurer', 'Tesorería'),
    ('group_chief', 'Jefe de grupo'),
    ('other', 'Otro'),
]
LEADER_KINDS = ('scouter', 'assistant', 'chief', 'candidate', 'secretary', 'treasurer', 'group_chief')


class ScoutPosition(models.Model):
    """Catálogo de roles y cargos (solo la jefatura de grupo lo modifica)."""
    _name = 'scout.position'
    _description = 'Rol o cargo scout'
    _order = 'sequence, id'

    name = fields.Char(string='Rol / cargo', required=True, translate=True)
    sequence = fields.Integer(default=10)
    kind = fields.Selection(POSITION_KINDS, string='Equivale a', required=True, default='other',
                            help='Sirve para los reportes: guía y subguía de los equipos, jefes que pasan lista, etc.')
    applies_to = fields.Selection([('youth', 'Miembro de sección'), ('leader', 'Dirigente'), ('both', 'Ambos')],
                                  string='Aplica a', required=True, default='both')
    all_sections_attendance = fields.Boolean(
        string='Pasa lista en todas las secciones',
        help='Quien tenga este cargo ve todas las secciones en el portal de dirigentes y puede pasar lista '
             'en cualquiera (jefatura de grupo, secretaría, tesorería).')
    active = fields.Boolean(default=True)

    @api.onchange('kind')
    def _onchange_kind_all_sections(self):
        if self.kind in ALL_SECTIONS_KINDS:
            self.all_sections_attendance = True

    @api.model
    def _scout_v45_migrate(self):
        positions = self.sudo().with_context(active_test=False).search([('kind', 'in', ALL_SECTIONS_KINDS)])
        positions |= self.env.ref('scout_management.position_group_assistant', raise_if_not_found=False) or self.browse()
        positions.write({'all_sections_attendance': True})
