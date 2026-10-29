# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

from .rss_portal_access import grant_portal_user

ROLE_SELECTION = [
    ('leader', 'Jefe de grupo'),
    ('secretary', 'Secretario(a)'),
    ('treasurer', 'Tesorero(a)'),
]


class RssGroupMember(models.Model):
    """Representante de un grupo en el portal /mi-grupo.

    Cada grupo tiene como máximo un jefe, un secretario y un tesorero con acceso.
    Cada cuenta pertenece a un solo grupo, y solo uno de ellos es el capturista:
    la persona que importa la lista de asegurados del grupo.
    """
    _name = 'rss.group.member'
    _description = 'Representante de grupo (portal)'
    _inherit = ['mail.thread']
    _order = 'group_id, role'

    group_id = fields.Many2one('rss.scout.group', string='Grupo', required=True, tracking=True,
                               ondelete='cascade', index=True)
    company_id = fields.Many2one(related='group_id.company_id', store=True, string='Organización')
    partner_id = fields.Many2one('res.partner', string='Persona', required=True, tracking=True,
                                 ondelete='restrict')
    name = fields.Char(related='partner_id.name', store=True, readonly=False, string='Nombre')
    email = fields.Char(related='partner_id.email', readonly=False, string='Correo')
    phone = fields.Char(related='partner_id.phone', readonly=False, string='WhatsApp')
    role = fields.Selection(ROLE_SELECTION, string='Cargo', required=True, tracking=True)
    is_capturer = fields.Boolean(
        'Capturista del grupo', tracking=True,
        help='Única persona del grupo que puede importar o capturar asegurados.')
    user_id = fields.Many2one('res.users', string='Usuario de portal', compute='_compute_user',
                              store=True)
    has_portal = fields.Boolean('Con acceso al portal', compute='_compute_user', store=True)
    active = fields.Boolean(default=True, tracking=True)

    _group_role_uniq = models.Constraint(
        'UNIQUE (group_id, role)', 'Ese cargo ya está asignado en el grupo.')
    _partner_uniq = models.Constraint(
        'UNIQUE (partner_id)', 'Esa persona ya representa a un grupo: cada cuenta es de un solo grupo.')

    @api.depends('partner_id.user_ids', 'partner_id.user_ids.active')
    def _compute_user(self):
        for member in self:
            user = member.partner_id.sudo().with_context(active_test=False).user_ids[:1]
            member.user_id = user
            member.has_portal = bool(user and user.active)

    @api.constrains('is_capturer', 'group_id', 'active')
    def _check_single_capturer(self):
        for member in self.filtered(lambda m: m.is_capturer and m.active):
            others = self.search_count([('group_id', '=', member.group_id.id), ('is_capturer', '=', True),
                                        ('active', '=', True), ('id', '!=', member.id)])
            if others:
                raise ValidationError(_(
                    '%s ya tiene capturista. Solo una persona por grupo puede cargar asegurados; '
                    'quítale la marca a la anterior antes de asignar otra.', member.group_id.name))

    @api.depends('name', 'role', 'group_id')
    def _compute_display_name(self):
        roles = dict(ROLE_SELECTION)
        for member in self:
            member.display_name = '%s · %s · %s' % (member.name or '', roles.get(member.role, ''),
                                                   member.group_id.name or '')

    # ------------------------------------------------------------------
    # Acceso
    # ------------------------------------------------------------------
    def action_grant_portal(self):
        for member in self:
            user = grant_portal_user(self.env, member.partner_id, member.company_id)
            member._compute_user()
            member.message_post(body=_('Acceso al portal del grupo otorgado (%s).', user.login))
        return True

    def action_revoke_portal(self):
        for member in self:
            user = member.user_id.sudo()
            still_needed = (self.env['rss.advisor'].sudo().search_count(
                [('user_id', '=', user.id), ('active', '=', True)]))
            if user and user.share and not still_needed:
                user.active = False
            member._compute_user()
            member.message_post(body=_('Acceso al portal retirado.'))
        return True

    def action_make_capturer(self):
        """Reasigna el capturista del grupo a esta persona (solo administración central)."""
        self.ensure_one()
        self.search([('group_id', '=', self.group_id.id), ('id', '!=', self.id)]).write(
            {'is_capturer': False})
        self.is_capturer = True
        self.group_id.message_post(body=_('Capturista del grupo: %s.', self.name))
        return True

    def action_open_portal(self):
        return {'type': 'ir.actions.act_url', 'url': '/mi-grupo', 'target': 'new'}

    @api.model
    def _for_user(self, user):
        """Representante activo del usuario (vacío si no representa a ningún grupo)."""
        if not user or user._is_public():
            return self.browse()
        return self.sudo().search([('user_id', '=', user.id), ('active', '=', True)], limit=1)


class RssScoutGroupMembers(models.Model):
    _inherit = 'rss.scout.group'

    portal_member_ids = fields.One2many('rss.group.member', 'group_id', string='Representantes del portal')
    capturer_id = fields.Many2one('rss.group.member', string='Capturista', compute='_compute_capturer')

    def _compute_capturer(self):
        for group in self:
            group.capturer_id = group.portal_member_ids.filtered('is_capturer')[:1]
