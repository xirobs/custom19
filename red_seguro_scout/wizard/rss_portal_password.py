# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import clean_context


class RssPortalPassword(models.TransientModel):
    """La administración central asigna la contraseña de un representante o asesor.

    Sirve cuando el servidor no envía correos o la persona no encuentra la invitación.
    """
    _name = 'rss.portal.password'
    _description = 'Contraseña del portal (Red Seguro Scout)'

    user_id = fields.Many2one('res.users', required=True, readonly=True)
    source_model = fields.Char()
    source_id = fields.Integer()
    login = fields.Char(related='user_id.login', string='Usuario para entrar')
    portal_url = fields.Char(compute='_compute_portal_url', string='Liga de entrada')
    password = fields.Char('Contraseña nueva', required=True)
    confirm = fields.Char('Confírmala', required=True)

    @api.depends('user_id')
    def _compute_portal_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for wiz in self:
            wiz.portal_url = base.rstrip('/') + '/seguro-app'

    def action_apply(self):
        self.ensure_one()
        if len(self.password or '') < 8:
            raise UserError(_('La contraseña debe tener al menos 8 caracteres.'))
        if self.password != self.confirm:
            raise UserError(_('Las dos contraseñas no coinciden.'))
        user = self.user_id.sudo().with_context(clean_context(self.env.context))
        if not user.share:
            raise UserError(_('Solo se puede asignar aquí la contraseña de usuarios de portal.'))
        user.write({'password': self.password, 'active': True})
        if self.source_model in ('rss.group.member', 'rss.advisor') and self.source_id:
            rec = self.env[self.source_model].browse(self.source_id).exists()
            if rec:
                rec.message_post(body=_('Contraseña del portal asignada por %(by)s. Entra con %(login)s.',
                                        by=self.env.user.name, login=user.login))
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'success', 'sticky': True,
                       'title': _('Contraseña asignada'),
                       'message': _('Ya puede entrar en %(url)s con el usuario %(login)s y la contraseña que pusiste.',
                                    url=self.portal_url, login=user.login),
                       'next': {'type': 'ir.actions.act_window_close'}},
        }
