# -*- coding: utf-8 -*-

from odoo import fields, models


class CrmTag(models.Model):
    _inherit = "crm.tag"

    creart_tag_group = fields.Selection(
        [
            ("sector", "Por sector"),
            ("urgency", "Por urgencia"),
        ],
        string="Grupo CreArt",
        index=True,
    )
