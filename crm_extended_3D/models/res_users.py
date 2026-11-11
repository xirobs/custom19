"""Acceso del equipo (usuarios portal o internos) a la app CreArt."""

from odoo import api, fields, models

TEAM_GROUP = "crm_extended_3D.group_creart_portal_team"


class ResUsers(models.Model):
    _inherit = "res.users"

    creart_team_access = fields.Boolean(
        string="Acceso a la app CreArt",
        compute="_compute_creart_team_access",
        inverse="_inverse_creart_team_access",
        search="_search_creart_team_access",
        help="Permite ver y operar todas las oportunidades desde la app del equipo.",
    )

    @api.depends("group_ids")
    def _compute_creart_team_access(self):
        group = self.env.ref(TEAM_GROUP)
        for user in self:
            user.creart_team_access = group in user.sudo().group_ids

    def _inverse_creart_team_access(self):
        group = self.env.ref(TEAM_GROUP)
        for user in self:
            command = 4 if user.creart_team_access else 3
            user.sudo().write({"group_ids": [(command, group.id)]})

    def _search_creart_team_access(self, operator, value):
        group = self.env.ref(TEAM_GROUP)
        positive = (operator in ("=", "in") and bool(value)) or (operator in ("!=", "not in") and not value)
        return [("group_ids", "in" if positive else "not in", group.ids)]
