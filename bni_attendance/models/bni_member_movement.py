# -*- coding: utf-8 -*-
from odoo import fields, models

from .bni_member import LEAVE_REASONS


class BniMemberMovement(models.Model):
    _name = 'bni.member.movement'
    _description = 'Alta / baja de miembro BNI'
    _order = 'date desc, id desc'

    member_id = fields.Many2one('bni.member', 'Miembro', required=True, ondelete='cascade', index=True)
    movement_type = fields.Selection([
        ('join', 'Alta'),
        ('leave', 'Baja'),
        ('reentry', 'Reingreso'),
    ], string='Movimiento', required=True)
    date = fields.Date('Fecha', required=True, default=fields.Date.context_today)
    leave_reason = fields.Selection(LEAVE_REASONS, string='Motivo de baja')
    notes = fields.Text('Información')
    user_id = fields.Many2one('res.users', 'Registró', default=lambda self: self.env.user, readonly=True)
    company_name = fields.Char(related='member_id.company_name', string='Empresa')
    company_id = fields.Many2one(related='member_id.company_id', store=True)
