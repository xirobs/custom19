# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class BitaPlatform(models.Model):
    """Plataforma que se cobra por suscripción (BitaScout, Red Seguro Scout...).

    Cada módulo registra su plataforma con los grupos de permisos que controla.
    Mientras ``enforce`` esté apagado no se quita ningún permiso: sirve para
    registrar primero las suscripciones de los usuarios actuales.
    """
    _name = 'bita.platform'
    _description = 'Plataforma por suscripción'
    _inherit = ['mail.thread']
    _order = 'sequence, name'

    sequence = fields.Integer(default=10)
    name = fields.Char('Plataforma', required=True, tracking=True)
    code = fields.Char('Código', required=True, help='Identificador técnico, p. ej. bitascout.')
    tagline = fields.Char('Lema')
    image = fields.Image('Emblema', max_width=256, max_height=256)
    group_ids = fields.Many2many(
        'res.groups', 'bita_platform_group_rel', 'platform_id', 'group_id',
        string='Permisos que controla',
        help='Grupos de usuario de la plataforma. Solo quien tenga suscripción vigente '
             'puede pertenecer a ellos.')
    default_group_id = fields.Many2one(
        'res.groups', string='Rol por defecto', domain="[('id', 'in', group_ids)]",
        help='Rol que se otorga cuando la suscripción no indica uno.')
    enforce = fields.Boolean(
        'Control de acceso activo', default=False, tracking=True,
        help='Encendido: quien no tenga suscripción vigente pierde los permisos de la '
             'plataforma. Apagado: solo se registran suscripciones, sin quitar permisos.')
    grace_days = fields.Integer(
        'Días de gracia', default=3, tracking=True,
        help='Días después del vencimiento en que el usuario conserva el acceso.')
    notice_days = fields.Integer('Avisar antes de vencer (días)', default=7)
    trial_months = fields.Integer(
        'Meses de prueba', default=0, tracking=True,
        help='Al dar por primera vez un rol de la plataforma a un usuario se le crea una '
             'suscripción de prueba por estos meses (una sola vez por usuario). 0 = sin prueba.')
    company_id = fields.Many2one('res.company', string='Empresa',
                                 default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id', string='Moneda')
    price_month = fields.Monetary('Precio mensual')
    price_year = fields.Monetary('Precio anual')
    courtesy_until = fields.Date(
        'Cortesía hasta', default=lambda self: fields.Date.context_today(self) + relativedelta(days=30),
        help='Vigencia de las suscripciones de cortesía para los usuarios actuales.')
    subscription_ids = fields.One2many('bita.subscription', 'platform_id', string='Suscripciones')
    active_count = fields.Integer('Suscriptores vigentes', compute='_compute_counts')
    expiring_count = fields.Integer('Por vencer', compute='_compute_counts')
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('UNIQUE (code)', 'Ya existe una plataforma con ese código.')

    def _compute_counts(self):
        today = fields.Date.context_today(self)
        for platform in self:
            current = platform.subscription_ids.filtered('is_current')
            platform.active_count = len(current.user_id)
            platform.expiring_count = len(current.filtered(
                lambda s: s.date_end and s.date_end <= today + relativedelta(days=platform.notice_days)))

    def _get_role_groups(self):
        """Todos los grupos controlados más los que éstos implican dentro de la plataforma."""
        self.ensure_one()
        return self.group_ids

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def _users_holding_groups(self):
        self.ensure_one()
        return self.env['res.users'].with_context(active_test=False).search([
            ('group_ids', 'in', self.group_ids.ids), ('share', 'in', (False, True)),
        ])

    def action_grant_courtesy(self):
        """Crea suscripciones de cortesía para quienes ya tienen permisos y no tienen
        suscripción vigente; así al encender el control nadie se queda fuera."""
        Subscription = self.env['bita.subscription']
        today = fields.Date.context_today(self)
        for platform in self:
            if not platform.courtesy_until or platform.courtesy_until < today:
                raise UserError(_('Indica una fecha de cortesía futura para %s.', platform.name))
            created = Subscription
            for user in platform._users_holding_groups():
                if user._bita_is_exempt() or user._bita_current_subscriptions(platform):
                    continue
                role = (user.group_ids & platform.group_ids)[:1] or (
                    platform.default_group_id if not user.share else self.env['res.groups'])
                if platform.trial_months and not Subscription.search_count([
                        ('user_id', '=', user.id), ('platform_id', '=', platform.id)]):
                    created |= user._bita_create_trial(platform, role)
                    continue
                created |= Subscription.create({
                    'user_id': user.id,
                    'platform_id': platform.id,
                    'group_id': role.id or False,
                    'period': 'custom',
                    'date_start': today,
                    'date_end': platform.courtesy_until,
                    'amount': 0.0,
                    'payment_method': 'courtesy',
                    'note': _('Cortesía al activar el control por suscripción.'),
                })
            created.filtered(lambda s: s.state == 'draft').action_activate()
            platform.message_post(body=_('%(count)s suscripciones de cortesía hasta el %(date)s.',
                                         count=len(created),
                                         date=platform.courtesy_until.strftime('%d/%m/%Y')))
        return True

    def action_enforce(self):
        self.write({'enforce': True})
        self._sync_all_users()

    def action_stop_enforce(self):
        self.write({'enforce': False})

    def _sync_all_users(self):
        users = self.env['res.users']
        for platform in self:
            users |= platform._users_holding_groups()
            users |= platform.subscription_ids.user_id
        users._bita_sync_access()

    def action_view_subscriptions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Suscripciones · %s', self.name),
            'res_model': 'bita.subscription',
            'view_mode': 'list,form,pivot',
            'domain': [('platform_id', '=', self.id)],
            'context': {'default_platform_id': self.id},
        }
