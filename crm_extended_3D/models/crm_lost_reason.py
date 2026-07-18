# -*- coding: utf-8 -*-

from odoo import fields, models


class CrmLostReason(models.Model):
    _inherit = "crm.lost.reason"

    requires_detail_note = fields.Boolean(
        string="Requiere nota interna",
        help="Al marcar la oportunidad como perdida, se exige una nota de cierre.",
    )
