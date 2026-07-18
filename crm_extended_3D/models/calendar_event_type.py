# -*- coding: utf-8 -*-

from odoo import fields, models


class CalendarEventTypeCreart(models.Model):
    _inherit = "calendar.event.type"

    creart_usage = fields.Text(
        string="Uso CreArt",
        help="Indica cuándo usar esta categoría al crear eventos en el calendario.",
    )
