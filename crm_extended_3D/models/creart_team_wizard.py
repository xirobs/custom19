"""Asistente para agregar personas al equipo de la app CreArt."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

TEAM_GROUP = "crm_extended_3D.group_creart_portal_team"


class CreartTeamAddWizard(models.TransientModel):
    _name = "creart.team.add.wizard"
    _description = "Agregar al equipo de la app CreArt"

    mode = fields.Selection(
        [("existing", "Usuario que ya existe"), ("new", "Persona nueva")],
        string="¿A quién agregas?", default="existing", required=True,
    )
    user_ids = fields.Many2many(
        "res.users", string="Usuarios",
        domain="[('active', '=', True)]",
        help="Usuarios que ya tienen acceso a Odoo (internos o portal).",
    )
    name = fields.Char(string="Nombre")
    email = fields.Char(string="Correo (será su usuario)")
    phone = fields.Char(string="Teléfono")
    access_type = fields.Selection(
        [("portal", "Solo la app (portal)"), ("internal", "App + Odoo completo (interno)")],
        string="Tipo de acceso", default="portal", required=True,
        help="Portal: solo ve la app CreArt y no consume licencia de usuario interno.",
    )
    send_invitation = fields.Boolean(
        string="Enviar invitación por correo", default=True,
        help="Envía un correo para que la persona cree su contraseña.",
    )

    @api.onchange("email")
    def _onchange_email(self):
        if self.email:
            self.email = self.email.strip().lower()

    def action_add(self):
        self.ensure_one()
        group = self.env.ref(TEAM_GROUP)
        if self.mode == "existing":
            if not self.user_ids:
                raise UserError(_("Elige al menos un usuario."))
            users = self.user_ids
            users.sudo().write({"group_ids": [(4, group.id)]})
        else:
            users = self._create_user(group)
        names = ", ".join(users.mapped("name"))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Equipo actualizado"),
                "message": _("%s ya puede entrar a la app CreArt.", names),
                "type": "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _create_user(self, group):
        name = (self.name or "").strip()
        email = (self.email or "").strip().lower()
        if not name or not email:
            raise UserError(_("Escribe el nombre y el correo de la persona."))
        Users = self.env["res.users"].sudo().with_context(active_test=False)
        existing = Users.search([("login", "=ilike", email)], limit=1)
        if existing:
            if not existing.active:
                existing.active = True
            existing.write({"group_ids": [(4, group.id)]})
            return existing
        base_group = self.env.ref("base.group_portal" if self.access_type == "portal" else "base.group_user")
        partner = self.env["res.partner"].sudo().search([("email", "=ilike", email), ("user_ids", "=", False)], limit=1)
        vals = {
            "name": name,
            "login": email,
            "email": email,
            "group_ids": [(6, 0, [base_group.id, group.id])],
            "tz": self.env.user.tz or "America/Mexico_City",
        }
        if partner:
            vals["partner_id"] = partner.id
        user = Users.with_context(no_reset_password=True).create(vals)
        if self.phone:
            user.partner_id.phone = self.phone
        if self.send_invitation:
            user.action_reset_password()
        return user
