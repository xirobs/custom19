# Copyright (C) 2026 - TODAY, jamie.escalante7@gmail.com
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
# -*- coding: utf-8 -*-
from datetime import datetime
from dateutil.relativedelta import relativedelta

from odoo import models, fields

class ExpirateDate(models.Model):
    _name = 'expirate.date'

    def check(self):
        obj_conf = self.env['ir.config_parameter'].sudo()
        expiration_date = datetime.strptime(obj_conf.get_param('database.expiration_date'), '%Y-%m-%d %H:%M:%S')
        
        if expiration_date.date() <= datetime.now().date():
            obj = self.env['ir.config_parameter'].search([('key', '=', 'database.expiration_date')])
            new_expiration_date = datetime.now() + relativedelta(months=1)
            obj.value = new_expiration_date.strftime('%Y-%m-%d %H:%M:%S')
            cron_id = self.env['ir.cron'].sudo().search([('id', '=', self.env.ref('date_expired.cron_update_date').id)], limit=1)
            cron_id.nextcall = new_expiration_date.strftime('%Y-%m-%d %H:%M:%S')