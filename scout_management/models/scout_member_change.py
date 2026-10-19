# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ScoutMemberChange(models.Model):
    """Solicitud de baja o cambio de sección; la aprueba el jefe de grupo."""
    _name = 'scout.member.change'
    _description = 'Solicitud de baja o cambio de sección'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'scout.approval.mixin']
    _order = 'create_date desc'

    name = fields.Char(compute='_compute_name', store=True, string='Solicitud')
    member_id = fields.Many2one('res.partner', string='Miembro', required=True, domain="[('is_scout', '=', True)]",
                                index=True)
    change_type = fields.Selection([
        ('leave', 'Baja'), ('section', 'Cambio de sección'), ('return', 'Reingreso'),
    ], string='Tipo', required=True, default='section', tracking=True)
    current_section_id = fields.Many2one('scout.section', string='Sección actual', readonly=True,
                                         compute='_compute_current', store=True)
    new_section_id = fields.Many2one('scout.section', string='Nueva sección')
    effective_date = fields.Date(string='A partir de', default=fields.Date.context_today, required=True)
    reason = fields.Text(string='Motivo', required=True)
    state = fields.Selection([
        ('draft', 'Borrador'), ('to_approve', 'Por aprobar'), ('approved', 'Aprobada'), ('rejected', 'Rechazada'),
    ], default='draft', required=True, tracking=True, string='Estado')
    approved_by_id = fields.Many2one('res.users', string='Aprobó', readonly=True, copy=False)
    rejection_reason = fields.Char(string='Motivo de rechazo')

    @api.depends('member_id', 'change_type')
    def _compute_name(self):
        labels = dict(self._fields['change_type'].selection)
        for change in self:
            change.name = f'{labels.get(change.change_type, "")} - {change.member_id.name or ""}'

    @api.depends('member_id')
    def _compute_current(self):
        for change in self:
            if change.state in (False, 'draft'):
                change.current_section_id = change.member_id.scout_section_id

    @api.model_create_multi
    def create(self, vals_list):
        changes = super().create(vals_list)
        if self.env.context.get('scout_submit_on_create', True):
            changes.action_submit()
        return changes

    def action_submit(self):
        for change in self.filtered(lambda c: c.state == 'draft'):
            if change.change_type == 'section' and not change.new_section_id:
                raise UserError(_('Indica la nueva sección.'))
            change.state = 'to_approve'
            change._scout_request_approval(change.name, change.reason or '')

    def action_approve(self):
        self._scout_check_manager(_('aprobar bajas y cambios de sección'))
        for change in self.filtered(lambda c: c.state == 'to_approve'):
            member = change.member_id.with_context(scout_change_approved=True)
            if change.change_type == 'leave':
                member.write({'scout_state': 'inactive', 'scout_leave_date': change.effective_date})
            elif change.change_type == 'return':
                member.write({'scout_state': 'active', 'scout_leave_date': False,
                              'scout_section_id': (change.new_section_id or member.scout_section_id).id})
            else:
                member.write({'scout_section_id': change.new_section_id.id, 'scout_patrol_id': False,
                              'scout_transition_date': False})
            member.message_post(body=_('%(change)s aprobada por %(user)s. Motivo: %(reason)s',
                                       change=change.name, user=self.env.user.name, reason=change.reason))
            change.write({'state': 'approved', 'approved_by_id': self.env.user.id})
            change._scout_close_approval_activities(_('Aprobada'))

    def action_reject(self):
        self._scout_check_manager(_('rechazar solicitudes'))
        self.filtered(lambda c: c.state == 'to_approve').write({'state': 'rejected'})
        self._scout_close_approval_activities(_('Rechazada'))
