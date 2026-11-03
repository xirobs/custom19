# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .rss_portal_access import grant_portal_user, sync_portal_login


class RssAdvisor(models.Model):
    """Asesor de seguros con acceso al portal /seguro (usuario de portal, sin licencia).

    Puede revisar asegurados, relaciones mensuales y pagos, y dar seguimiento a los
    siniestros. Si se indican agrupaciones o grupos, solo ve esos grupos.
    """
    _name = 'rss.advisor'
    _description = 'Asesor de seguros (portal)'
    _inherit = ['mail.thread']
    _order = 'name'

    partner_id = fields.Many2one('res.partner', string='Contacto', required=True, tracking=True,
                                 ondelete='restrict')
    name = fields.Char(related='partner_id.name', store=True, readonly=False, string='Nombre')
    email = fields.Char(related='partner_id.email', readonly=False, string='Correo')
    portal_login = fields.Char(related='user_id.login', string='Usuario para entrar')
    phone = fields.Char(related='partner_id.phone', readonly=False, string='Teléfono')
    insurer_id = fields.Many2one('res.partner', string='Aseguradora / despacho', tracking=True)
    company_id = fields.Many2one('res.company', string='Organización', required=True,
                                 default=lambda self: self.env.company)
    cluster_ids = fields.Many2many('rss.group.cluster', 'rss_advisor_cluster_rel', 'advisor_id',
                                   'cluster_id', string='Solo estas agrupaciones')
    group_ids = fields.Many2many('rss.scout.group', 'rss_advisor_group_rel', 'advisor_id',
                                 'group_id', string='Solo estos grupos',
                                 help='Vacío junto con agrupaciones: ve todos los grupos.')
    can_update_claims = fields.Boolean('Actualiza siniestros', default=True, tracking=True)
    can_ack_reports = fields.Boolean('Registra acuses de relaciones', default=True, tracking=True)
    can_upload_certificates = fields.Boolean('Sube certificados de la póliza', default=True, tracking=True)
    can_register_insured = fields.Boolean(
        'Da de alta asegurados', default=True, tracking=True,
        help='Puede registrar asegurados de sus grupos desde el portal (quedan en preinscripción, '
             'igual que al darlos de alta en la plataforma).')
    user_id = fields.Many2one('res.users', string='Usuario de portal', compute='_compute_user',
                              store=True)
    has_portal = fields.Boolean('Con acceso al portal', compute='_compute_user', store=True)
    active = fields.Boolean(default=True, tracking=True)
    notes = fields.Text('Notas')

    _partner_company_uniq = models.Constraint(
        'UNIQUE (partner_id, company_id)', 'Ese contacto ya está registrado como asesor.')

    @api.depends('partner_id.user_ids', 'partner_id.user_ids.active')
    def _compute_user(self):
        for advisor in self:
            user = advisor.partner_id.sudo().with_context(active_test=False).user_ids[:1]
            advisor.user_id = user
            advisor.has_portal = bool(user and user.active)

    def _is_restricted(self):
        self.ensure_one()
        return bool(self.cluster_ids or self.group_ids)

    def _allowed_groups(self):
        """Grupos que el asesor puede ver."""
        self.ensure_one()
        Group = self.env['rss.scout.group'].sudo()
        if not self._is_restricted():
            return Group.search([('company_id', '=', self.company_id.id)])
        return (self.group_ids | self.cluster_ids.group_ids).sudo()

    # ------------------------------------------------------------------
    # Acceso
    # ------------------------------------------------------------------
    def action_grant_portal(self):
        """Crea (o reactiva) su usuario de portal y le envía la invitación."""
        for advisor in self:
            user = grant_portal_user(self.env, advisor.partner_id, advisor.company_id)
            advisor._compute_user()
            advisor.message_post(body=_('Acceso al portal del asesor otorgado (%s).', user.login))
        if len(self) == 1:
            return self.action_set_password()
        return True

    def write(self, vals):
        res = super().write(vals)
        if 'email' in vals or 'partner_id' in vals:
            for rec in self.filtered('user_id'):
                sync_portal_login(self.env, rec.partner_id)
        return res

    def action_set_password(self):
        """Abre el asistente para que la administración asigne la contraseña del portal."""
        self.ensure_one()
        if not self.user_id:
            grant_portal_user(self.env, self.partner_id, self.company_id)
            self._compute_user()
        sync_portal_login(self.env, self.partner_id)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Contraseña del portal'),
            'res_model': 'rss.portal.password',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_user_id': self.user_id.id, 'default_source_model': self._name,
                        'default_source_id': self.id},
        }

    def action_revoke_portal(self):
        for advisor in self:
            user = advisor.user_id.sudo()
            others = self.sudo().search([('user_id', '=', user.id), ('id', '!=', advisor.id),
                                         ('active', '=', True)])
            if user and user.share and not others:
                user.active = False
            advisor._compute_user()
            advisor.message_post(body=_('Acceso al portal retirado.'))
        return True

    def action_open_portal(self):
        return {'type': 'ir.actions.act_url', 'url': '/seguro', 'target': 'new'}

    @api.model
    def _for_user(self, user):
        """Registro de asesor activo del usuario (vacío si no es asesor)."""
        if not user or user._is_public():
            return self.browse()
        return self.sudo().search([('user_id', '=', user.id), ('active', '=', True)], limit=1)
