# -*- coding: utf-8 -*-
import logging

from dateutil.relativedelta import relativedelta

from odoo import SUPERUSER_ID, api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.fields import Command

_logger = logging.getLogger(__name__)

GROUP_KEYS = ('group_ids', 'sel_groups_', 'in_group_', 'all_group_ids')


class ResUsers(models.Model):
    _inherit = 'res.users'

    bita_exempt = fields.Boolean(
        'Exento de suscripción',
        help='Soporte o administración: conserva sus permisos sin suscripción.')
    bita_subscription_ids = fields.One2many('bita.subscription', 'user_id',
                                            string='Suscripciones')
    bita_access_summary = fields.Char('Plataformas', compute='_compute_bita_access_summary')

    def _compute_bita_access_summary(self):
        platforms = self.env['bita.platform'].sudo().search([])
        for user in self:
            parts = []
            for platform in platforms:
                if user._bita_is_exempt():
                    parts.append('%s: exento' % platform.name)
                    continue
                current = user.sudo().bita_subscription_ids.filtered(
                    lambda s, p=platform: s.platform_id == p and s.is_current)
                if current:
                    parts.append('%s: hasta %s' % (platform.name,
                                                   max(current.mapped('date_end')).strftime('%d/%m/%Y')))
                else:
                    parts.append('%s: sin suscripción' % platform.name)
            user.bita_access_summary = ' · '.join(parts)

    # ------------------------------------------------------------------
    # Reglas de acceso
    # ------------------------------------------------------------------
    def _bita_is_exempt(self):
        self.ensure_one()
        return self.id == SUPERUSER_ID or self.sudo().bita_exempt

    def _bita_current_subscriptions(self, platform):
        self.ensure_one()
        return self.env['bita.subscription'].sudo().search([
            ('user_id', '=', self.id), ('platform_id', '=', platform.id), ('is_current', '=', True),
        ])

    def _bita_has_access(self, platform):
        """True si el usuario puede usar la plataforma hoy."""
        self.ensure_one()
        if not platform or not platform.enforce or self._bita_is_exempt():
            return True
        return bool(self._bita_current_subscriptions(platform))

    @api.model
    def _bita_platform(self, code):
        return self.env['bita.platform'].sudo().search([('code', '=', code)], limit=1)

    def _bita_sync_access(self):
        """Otorga el rol de las suscripciones vigentes y retira los permisos de las
        plataformas sin suscripción (solo en plataformas con control activo)."""
        platforms = self.env['bita.platform'].sudo().search([('enforce', '=', True)])
        if not platforms:
            return
        employee = self.env.ref('base.group_user')
        for user in self.sudo().with_context(active_test=False).exists():
            if user._bita_is_exempt():
                continue
            commands = []
            was_internal = employee in user.group_ids
            for platform in platforms:
                controlled = platform.group_ids
                subs = user._bita_current_subscriptions(platform)
                if subs and user.share:
                    # Usuario de portal (p. ej. dirigente): la suscripción le abre el portal;
                    # no se le dan roles internos (Odoo no permite mezclar portal e interno).
                    continue
                if subs:
                    roles = subs.group_id or platform.default_group_id
                    commands += [Command.link(g.id) for g in roles if g not in user.group_ids]
                else:
                    commands += [Command.unlink(g.id) for g in controlled if g in user.group_ids]
            if not commands:
                continue
            user.with_context(bita_sync=True).write({'group_ids': commands})
            if was_internal and employee not in user.all_group_ids and not user.share:
                user.with_context(bita_sync=True).write({'group_ids': [Command.link(employee.id)]})
            _logger.info('Suscripciones BitaScout: permisos de %s actualizados.', user.login)

    def _bita_create_trial(self, platform, role=None):
        """Suscripción de prueba (una sola vez por usuario y plataforma)."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        end = today + relativedelta(months=platform.trial_months, days=-1)
        sub = self.env['bita.subscription'].sudo().with_context(bita_sync=True).create({
            'user_id': self.id,
            'platform_id': platform.id,
            'group_id': False if self.share else (role or platform.default_group_id).id,
            'period': 'custom',
            'date_start': today,
            'date_end': end,
            'amount': 0.0,
            'payment_method': 'trial',
            'state': 'active',
            'note': _('Periodo de prueba de %s meses.', platform.trial_months),
        })
        sub.message_post(body=_('Prueba gratuita de %(platform)s hasta el %(date)s. Después se '
                                'requiere pagar la suscripción.',
                                platform=platform.name, date=end.strftime('%d/%m/%Y')))
        return sub

    def _bita_check_manual_groups(self):
        """Impide dar a mano permisos de una plataforma a quien no paga. Si la
        plataforma tiene periodo de prueba y el usuario nunca lo ha usado, se le
        crea la prueba en lugar de bloquearlo."""
        if self.env.context.get('bita_sync') or self.env.context.get('install_mode'):
            return
        platforms = self.env['bita.platform'].sudo().search([('enforce', '=', True)])
        Subscription = self.env['bita.subscription'].sudo()
        for user in self.sudo():
            if user._bita_is_exempt():
                continue
            for platform in platforms:
                held = user.group_ids & platform.group_ids
                if held and not user._bita_current_subscriptions(platform):
                    if platform.trial_months and not Subscription.search_count([
                            ('user_id', '=', user.id), ('platform_id', '=', platform.id)]):
                        user._bita_create_trial(platform, held[:1])
                        continue
                    raise ValidationError(_(
                        '%(user)s no tiene suscripción vigente a %(platform)s, así que no puede '
                        'recibir el permiso «%(group)s». Su periodo de prueba ya se usó: '
                        'registra primero su pago en Suscripciones o márcalo como exento.',
                        user=user.name, platform=platform.name, group=held[0].name))

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        users._bita_check_manual_groups()
        return users

    def write(self, vals):
        res = super().write(vals)
        if any(key.startswith(GROUP_KEYS) for key in vals):
            self._bita_check_manual_groups()
        return res

    def _update_last_login(self):
        # Revisión al iniciar sesión: si la suscripción venció, se retira el acceso
        # aunque el cron diario todavía no haya corrido.
        res = super()._update_last_login()
        try:
            self.filtered(lambda u: u.id)._bita_sync_access()
        except Exception:  # noqa: BLE001 - nunca impedir el inicio de sesión
            _logger.exception('Suscripciones BitaScout: no se pudo sincronizar el acceso')
        return res


class ResGroups(models.Model):
    _inherit = 'res.groups'

    def write(self, vals):
        res = super().write(vals)
        if 'user_ids' in vals and not self.env.context.get('bita_sync'):
            platforms = self.env['bita.platform'].sudo().search([
                ('enforce', '=', True), ('group_ids', 'in', self.ids)])
            if platforms:
                self.user_ids._bita_check_manual_groups()
        return res
