# -*- coding: utf-8 -*-
from odoo import fields, models


class BitaSubscription(models.Model):
    _inherit = 'bita.subscription'

    rss_payment_id = fields.Many2one('rss.payment', string='Pago de seguro', index=True,
                                     ondelete='set null', readonly=True)
