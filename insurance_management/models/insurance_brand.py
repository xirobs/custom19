# -*- coding: utf-8 -*-
"""Identidad visual de la agencia: logo y colores para reportes, portal y correos."""

import base64

from odoo import _, api, fields, models
from odoo.tools import file_open

BRAND = {
    "name": "Respaldo Patrimonial",
    "tagline": "Asesores de Seguros",
    "purple": "#8963CA",      # parte alta del degradado
    "indigo": "#4B3FCF",      # parte baja del degradado
    "primary": "#5B47CC",     # color sólido principal (botones, encabezados)
    "deep": "#3B2F96",        # logo y títulos sobre blanco
    "lavender": "#D5E5FA",    # logo sobre morado
    "teal": "#1AD3B1",        # acento (logo en degradado turquesa)
    "blue": "#208CEF",        # acento
    "ink": "#1E1B3A",
    "muted": "#5E5A78",
    "line": "#E1DCF2",
    "soft": "#F5F3FC",
}
LOGO_PATH = "insurance_management/static/img/logo_h_dark.png"
LOGO_LIGHT_PATH = "insurance_management/static/img/logo_h_light.png"


class InsuranceBrand(models.AbstractModel):
    _name = "insurance.brand"
    _description = "Identidad visual de seguros"

    @api.model
    def colors(self):
        return dict(BRAND)

    @api.model
    def logo_b64(self, light=False):
        with file_open(LOGO_LIGHT_PATH if light else LOGO_PATH, "rb") as handle:
            return base64.b64encode(handle.read())

    @api.model
    def logo_data_uri(self, light=False):
        """Logo incrustado: el PDF no depende de que el servidor se vea a sí mismo por URL.
        ``light`` = versión clara para fondos morados."""
        return "data:image/png;base64,%s" % self.logo_b64(light).decode()

    @api.model
    def apply_to_company(self, company):
        company.sudo().write({
            "logo": self.logo_b64(),
            "primary_color": BRAND["indigo"],
            "secondary_color": BRAND["purple"],
        })


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    def action_insurance_apply_brand(self):
        self.env["insurance.brand"].apply_to_company(self.env.company)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Identidad aplicada"),
                "message": _("Se puso el logo y los colores de la agencia en %s.") % self.env.company.name,
                "type": "success",
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }
