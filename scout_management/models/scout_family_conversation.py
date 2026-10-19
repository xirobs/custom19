# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class ScoutFamilyConversation(models.Model):
    """Chat entre la familia (portal) y los dirigentes de la sección de un miembro.

    Los mensajes del padre llegan a la bandeja de Conversaciones de los dirigentes que siguen el hilo,
    y las respuestas internas llegan al padre por correo y en su portal.
    """
    _name = 'scout.family.conversation'
    _description = 'Chat con la familia'
    _inherit = ['mail.thread', 'portal.mixin']
    _order = 'write_date desc'

    name = fields.Char(compute='_compute_name', store=True, string='Conversación')
    member_id = fields.Many2one('res.partner', string='Miembro', required=True, ondelete='cascade', index=True,
                                domain="[('is_scout', '=', True)]")
    section_id = fields.Many2one(related='member_id.scout_section_id', store=True, string='Sección')
    guardian_id = fields.Many2one(related='member_id.scout_guardian_id', store=True, string='Tutor')
    last_message_date = fields.Datetime(compute='_compute_last_message', string='Último mensaje')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    _member_uniq = models.Constraint('UNIQUE (member_id)', 'El miembro ya tiene un chat con su familia.')

    @api.depends('member_id.name')
    def _compute_name(self):
        for conv in self:
            conv.name = _('Familia de %s', conv.member_id.name or '')

    def _compute_last_message(self):
        for conv in self:
            conv.last_message_date = conv.message_ids[:1].date

    def _compute_access_url(self):
        super()._compute_access_url()
        for conv in self:
            conv.access_url = f'/my/scouts/{conv.member_id.id}'

    @api.model
    def _get_or_create_for_member(self, member):
        conv = self.search([('member_id', '=', member.id)], limit=1)
        if not conv:
            conv = self.create({'member_id': member.id})
            member.sudo().write({'scout_conversation_id': conv.id})
        conv._sync_followers()
        return conv

    def _sync_followers(self):
        for conv in self:
            partners = conv.member_id.scout_section_id.leader_ids.partner_id
            partners |= conv.member_id.scout_guardian_id
            partners |= conv.member_id.scout_guardian2_id.filtered('user_ids')
            if partners:
                conv.message_subscribe(partner_ids=partners.ids)
