# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_amount, format_date


class ScoutDailySalesWizard(models.TransientModel):
    _name = 'scout.daily.sales.wizard'
    _description = 'Parte de ventas'

    date_from = fields.Date(string='Desde', required=True, default=fields.Date.context_today)
    date_to = fields.Date(string='Hasta', required=True, default=fields.Date.context_today)
    sale_type = fields.Selection([('store', 'Tienda scout'), ('food', 'Alimentos'),
                                  ('event', 'Campamento / evento'), ('donation', 'Colaboración solicitada')],
                                 string='Tipo de venta', help='Vacío = todas.')

    def action_print(self):
        self.ensure_one()
        if self.date_to < self.date_from:
            raise UserError(_('La fecha final debe ser posterior a la inicial.'))
        return self.env.ref('scout_management.action_report_daily_sales').report_action(
            self, data={'wizard_id': self.id})


class ReportScoutDailySales(models.AbstractModel):
    _name = 'report.scout_management.report_daily_sales'
    _description = 'Reporte: parte de ventas'

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        wizard = self.env['scout.daily.sales.wizard'].browse(data.get('wizard_id') or docids)
        domain = [('sale_state', '=', 'done'), ('date', '>=', wizard.date_from), ('date', '<=', wizard.date_to)]
        if wizard.sale_type:
            domain.append(('sale_type', '=', wizard.sale_type))
        lines = self.env['scout.store.sale.line'].sudo().search(domain, order='date, sale_id, id')
        days = {}
        for line in lines:
            day = days.setdefault(line.date, {'lines': [], 'total': 0.0, 'paid': 0.0, 'credit': 0.0})
            day['lines'].append(line)
            day['total'] += line.subtotal
            if line.paid_state == 'paid':
                day['paid'] += line.subtotal
            else:
                day['credit'] += line.subtotal
        products = {}
        for line in lines:
            p = products.setdefault(line.product_id, {'qty': 0.0, 'amount': 0.0})
            p['qty'] += line.quantity
            p['amount'] += line.subtotal
        currency = self.env.company.currency_id
        return {
            'doc_ids': wizard.ids, 'doc_model': wizard._name, 'docs': wizard,
            'company': self.env.company,
            'days': sorted(days.items()),
            'products': sorted(products.items(), key=lambda kv: -kv[1]['amount']),
            'total': sum(l.subtotal for l in lines),
            'total_paid': sum(d['paid'] for d in days.values()),
            'total_credit': sum(d['credit'] for d in days.values()),
            'money': lambda v: format_amount(self.env, v or 0.0, currency),
            'fdate': lambda d: format_date(self.env, d),
            'type_labels': dict(self.env['scout.store.sale']._fields['sale_type'].selection),
        }
