# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


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
            partner = advisor.partner_id.sudo()
            if not partner.email:
                raise UserError(_('%s necesita un correo para recibir su acceso.', partner.name))
            user = partner.with_context(active_test=False).user_ids[:1]
            if user and not user.share:
                raise UserError(_('%s ya es usuario interno; el portal es para usuarios externos.',
                                  partner.name))
            if not user:
                same_login = self.env['res.users'].sudo().with_context(active_test=False).search(
                    [('login', '=ilike', partner.email.strip())], limit=1)
                if same_login:
                    raise UserError(_('El correo %(email)s ya lo usa el usuario %(user)s.',
                                      email=partner.email, user=same_login.name))
                wizard = self.env['portal.wizard'].sudo().with_context(
                    active_model='res.partner', active_ids=partner.ids).create({})
                wizard.user_ids.action_grant_access()
                user = partner.with_context(active_test=False).user_ids[:1]
            elif not user.active:
                user.active = True
            if advisor.company_id not in user.company_ids:
                user.write({'company_ids': [fields.Command.link(advisor.company_id.id)]})
            advisor._compute_user()
            advisor.message_post(body=_('Acceso al portal del asesor otorgado (%s).', user.login))
        return True

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
