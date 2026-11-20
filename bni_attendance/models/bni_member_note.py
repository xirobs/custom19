# -*- coding: utf-8 -*-
from odoo import fields, models


class BniMemberNote(models.Model):
    _name = 'bni.member.note'
    _description = 'Observación de miembro BNI'
    _order = 'date desc, id desc'

    member_id = fields.Many2one('bni.member', 'Miembro', required=True, ondelete='cascade', index=True)
    date = fields.Date('Fecha', required=True, default=fields.Date.context_today)
    event_id = fields.Many2one('bni.event', 'Evento relacionado', ondelete='set null')
    note_type = fields.Selection([
        ('member', 'Registrada por el miembro'),
        ('general', 'General'),
        ('attendance', 'Asistencia'),
        ('business', 'Negocio / Referencias'),
        ('alert', 'Alerta'),
    ], string='Tipo', default='general', required=True)
    note = fields.Text('Observación', required=True)
    user_id = fields.Many2one('res.users', 'Registró', default=lambda self: self.env.user, readonly=True)
    company_id = fields.Many2one(related='member_id.company_id', store=True)
