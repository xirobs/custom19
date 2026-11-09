# -*- coding: utf-8 -*-
"""Ingresos y egresos de meses anteriores a la plataforma (tomados de los reportes en Excel/Drive).

Solo sirven para el comparativo de 3, 6 o 12 meses del informe de tesorería: NO mueven cajas ni
bancos (el dinero de antes de la plataforma ya está en el "Saldo inicial" de cada cuenta)."""
from odoo import api, fields, models

HISTORY_GROUPS = [
    ('registration', 'Registros'), ('fees', 'Cuotas'), ('activity', 'Campamentos y actividades'),
    ('store', 'Tienda scout'), ('food', 'Venta de alimentos'), ('sales', 'Ventas del día'),
    ('donation', 'Donativos y colaboraciones'), ('other', 'Otros ingresos'),
]


class ScoutTreasuryHistory(models.Model):
    _name = 'scout.treasury.history'
    _description = 'Movimiento histórico de tesorería (antes de la plataforma)'
    _order = 'date desc, id desc'

    date = fields.Date(string='Fecha', required=True, default=fields.Date.context_today)
    move_type = fields.Selection([('income', 'Ingreso'), ('expense', 'Egreso')], string='Tipo', required=True,
                                 default='income')
    name = fields.Char(string='Concepto', required=True)
    report_group = fields.Selection(HISTORY_GROUPS, string='Rubro del ingreso', default='store')
    expense_category = fields.Char(string='Categoría del gasto', default='Otros gastos')
    amount = fields.Monetary(string='Importe', required=True, currency_field='currency_id')
    account = fields.Selection([('cash', 'Efectivo'), ('bank', 'Banco'), ('online', 'Mercado Pago')],
                               string='Cuenta', default='cash')
    source = fields.Char(string='Fuente', help='Hoja o documento de donde se tomó (p. ej. "Reporte Julio 2026").')
    notes = fields.Text(string='Notas')
    company_id = fields.Many2one('res.company', string='Grupo', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')

    @api.model
    def totals(self, date_from, date_to, company=None):
        """{'income', 'expense', 'groups': {key: amount}, 'categories': {name: amount}, 'count'}"""
        company = company or self.env.company
        recs = self.sudo().search([('company_id', '=', company.id), ('date', '>=', date_from),
                                   ('date', '<=', date_to)])
        res = {'income': 0.0, 'expense': 0.0, 'groups': {}, 'categories': {}, 'count': len(recs)}
        for r in recs:
            if r.move_type == 'income':
                res['income'] += r.amount
                key = r.report_group or 'other'
                res['groups'][key] = res['groups'].get(key, 0.0) + r.amount
            else:
                res['expense'] += r.amount
                key = (r.expense_category or 'Otros gastos').strip()
                res['categories'][key] = res['categories'].get(key, 0.0) + r.amount
        return res
