# -*- coding: utf-8 -*-
from odoo import models

PLATFORM_CODE = 'bitascout'


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _bita_scout_state(self):
        """'ok' | 'trial_started' | 'blocked' para BitaScout."""
        self.ensure_one()
        platform = self._bita_platform(PLATFORM_CODE)
        if not platform or not platform.enforce or self._bita_is_exempt():
            return 'ok'
        if self._bita_current_subscriptions(platform):
            return 'ok'
        Subscription = self.env['bita.subscription'].sudo()
        if platform.trial_months and not Subscription.search_count([
                ('user_id', '=', self.id), ('platform_id', '=', platform.id)]):
            self._bita_create_trial(platform)
            return 'trial_started'
        return 'blocked'

    def _bita_scout_blocked(self):
        self.ensure_one()
        platform = self._bita_platform(PLATFORM_CODE)
        return bool(platform and platform.enforce and not self._bita_is_exempt()
                    and not self._bita_current_subscriptions(platform))

    def _scout_leader_sections(self):
        """El portal de dirigentes solo abre con suscripción (o prueba) vigente."""
        sections = super()._scout_leader_sections()
        if sections and self._bita_scout_state() == 'blocked':
            return sections.browse()
        return sections
