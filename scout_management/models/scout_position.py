# -*- coding: utf-8 -*-
from odoo import fields, models

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
    active = fields.Boolean(default=True)
