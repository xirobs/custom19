# -*- coding: utf-8 -*-
from odoo import models


class BitaPlatform(models.Model):
    _inherit = 'bita.platform'

    def _users_holding_groups(self):
        """En BitaScout también cuentan los dirigentes de portal (sin grupos internos)."""
        users = super()._users_holding_groups()
        if self.code == 'bitascout':
            Section = self.env['scout.section'].sudo().with_context(active_test=False)
            users |= Section.search([]).leader_ids
            leaders = self.env['res.partner'].sudo().search([('scout_leading_section_ids', '!=', False)])
            users |= leaders.with_context(active_test=False).user_ids
        return users
