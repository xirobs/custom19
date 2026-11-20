# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError

from ..models.bni_member import LEAVE_REASONS


class BniMemberStatusWizard(models.TransientModel):
    _name = 'bni.member.status.wizard'
    _description = 'Baja / reingreso de miembro BNI'

    member_id = fields.Many2one('bni.member', 'Miembro', required=True)
    action = fields.Selection([('leave', 'Baja'), ('reentry', 'Reingreso')], required=True)
    date = fields.Date('Fecha', required=True, default=fields.Date.context_today)
    leave_reason = fields.Selection(LEAVE_REASONS, string='Motivo de baja')
    notes = fields.Text('Información / detalle')

    def action_confirm(self):
        self.ensure_one()
        member = self.member_id
        Movement = self.env['bni.member.movement']
        if self.action == 'leave':
            if member.state != 'active':
                raise UserError(_('%s ya está dado de baja.', member.name))
            if not self.leave_reason:
                raise UserError(_('Indica el motivo de la baja.'))
            member.write({
                'state': 'inactive',
                'leave_date': self.date,
                'leave_reason': self.leave_reason,
                'leave_notes': self.notes,
            })
            Movement.create({
                'member_id': member.id, 'movement_type': 'leave', 'date': self.date,
                'leave_reason': self.leave_reason, 'notes': self.notes,
            })
            # Se quita de las listas abiertas donde aún no registró asistencia
            pending = self.env['bni.attendance'].search([
                ('member_id', '=', member.id),
                ('event_id.state', 'in', ('draft', 'open')),
                ('check_in', '=', False),
                ('is_substitute', '=', False),
            ])
            pending.unlink()
        else:
            if member.state == 'active':
                raise UserError(_('%s ya está activo.', member.name))
            member.write({'state': 'active', 'leave_date': False, 'leave_reason': False, 'leave_notes': False})
            Movement.create({
                'member_id': member.id, 'movement_type': 'reentry', 'date': self.date, 'notes': self.notes,
            })
            open_events = self.env['bni.event'].search([
                ('state', '=', 'open'), ('company_id', '=', member.company_id.id)])
            open_events.action_load_members()
        return {'type': 'ir.actions.act_window_close'}
