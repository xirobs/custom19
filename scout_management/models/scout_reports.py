# -*- coding: utf-8 -*-
import pytz

from odoo import api, models
from odoo.tools import format_amount


class ReportScoutMember(models.AbstractModel):
    _name = 'report.scout_management.report_member_document'
    _description = 'Reporte: expediente del miembro'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['res.partner'].browse(docids)
        currency = self.env.company.currency_id
        pending = {doc.id: doc.sudo().scout_invoice_ids.filtered(
            lambda m: m.state == 'posted' and m.move_type == 'out_invoice'
            and m.payment_state in ('not_paid', 'partial')) for doc in docs}
        return {
            'doc_ids': docids,
            'doc_model': 'res.partner',
            'docs': docs,
            'can_see_medical': self.env.user.has_group('scout_management.group_scout_leader'),
            'pending_by_member': pending,
            'money': lambda value: format_amount(self.env, value or 0.0, currency),
        }


class ReportScoutMeeting(models.AbstractModel):
    _name = 'report.scout_management.report_meeting_document'
    _description = 'Reporte: acta de consejo'

    @api.model
    def _get_report_values(self, docids, data=None):
        tz = pytz.timezone(self.env.user.tz or 'America/Mexico_City')

        def local_dt(value):
            if not value:
                return ''
            return pytz.utc.localize(value).astimezone(tz).strftime('%d/%m/%Y %H:%M')

        return {
            'doc_ids': docids,
            'doc_model': 'scout.meeting',
            'docs': self.env['scout.meeting'].browse(docids),
            'local_dt': local_dt,
        }
