# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import format_amount, format_date


class ScoutTreasuryReportWizard(models.TransientModel):
    _name = 'scout.treasury.report.wizard'
    _description = 'Reporte de ingresos y egresos'

    date_from = fields.Date(string='Desde', required=True,
                            default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(string='Hasta', required=True, default=fields.Date.context_today)
    include_pending = fields.Boolean(string='Incluir cuentas por cobrar y por pagar', default=True)
    notes = fields.Text(string='Notas para la junta')

    def action_print(self):
        self.ensure_one()
        if self.date_to < self.date_from:
            raise UserError(_('La fecha final debe ser posterior a la inicial.'))
        return self.env.ref('scout_management.action_report_treasury').report_action(
            self, data={'wizard_id': self.id})


class ReportScoutTreasury(models.AbstractModel):
    """Balance de tesorería para el Consejo, leído de Contabilidad (cajas y bancos)."""
    _name = 'report.scout_management.report_treasury_document'
    _description = 'Reporte: ingresos y egresos'

    GROUP_ORDER = ['sales', 'store', 'food', 'registration', 'fees', 'activity', 'donation', 'collections', 'other']

    @api.model
    def _classify_line(self, line, liquidity_accounts):
        """Regresa (tipo, grupo, etiqueta, quién) para una línea de caja/banco."""
        move = line.move_id
        others = move.line_ids - line
        who = ''
        tmove = move.scout_treasury_move_id
        if tmove:
            who = tmove.member_id.name or tmove.partner_id.name or ''
            if tmove.move_type == 'transfer':
                return 'transfer', None, (tmove.description or '').splitlines()[0], who
            group = tmove.category_id.report_group if tmove.move_type == 'income' else None
            return tmove.move_type, group or 'other', (tmove.description or '').splitlines()[0], who
        if others and all(o.account_id in liquidity_accounts for o in others):
            return 'transfer', None, move.ref or move.name, ''
        payment = move.origin_payment_id
        if payment:
            invoices = payment.reconciled_invoice_ids | payment.reconciled_bill_ids
            members = invoices.scout_member_id
            who = ', '.join(members.mapped('name')) or payment.partner_id.name or ''
            label = ', '.join(i.invoice_line_ids[:1].name or i.name for i in invoices) or payment.memo or payment.name
            if payment.payment_type == 'inbound':
                return 'income', 'collections', label, who
            return 'expense', None, label, who
        kind = 'income' if line.balance > 0 else 'expense'
        label = move.ref or ', '.join(others.account_id.mapped('name'))
        return kind, 'other', label, move.partner_id.name or ''

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        wizard = self.env['scout.treasury.report.wizard'].browse(data.get('wizard_id') or docids)
        if not wizard.exists():
            raise UserError(_('Vuelve a generar el reporte desde el asistente.'))
        company = self.env.company
        currency = company.currency_id
        # Mismo cálculo que el portal y la pantalla de la plataforma (scout.treasury.ledger)
        st = self.env['scout.treasury.ledger'].statement(wizard.date_from, wizard.date_to, company)

        def flat(entry):
            return dict(entry, journal=entry['journal'].name, category=entry.get('category') or '',
                        who=entry.get('who') or '', ref=entry.get('ref') or '')
        account_rows = st['rows']
        ordered_groups = [{'label': g['label'], 'total': g['total'], 'moves': [flat(e) for e in g['moves']]}
                          for g in st['income_groups']]
        expenses = [flat(e) for e in st['expenses']]
        transfers = [flat(e) for e in st['transfers']]

        receivables, payables, to_approve = [], [], []
        if wizard.include_pending:
            Move = self.env['account.move'].sudo()
            for inv in Move.search([('company_id', '=', company.id), ('state', '=', 'posted'),
                                    ('move_type', '=', 'out_invoice'),
                                    ('payment_state', 'in', ('not_paid', 'partial'))], order='invoice_date'):
                receivables.append({'who': inv.scout_member_id.name or inv.partner_id.name,
                                    'desc': inv.invoice_line_ids[:1].name or inv.name,
                                    'amount': inv.amount_residual})
            for bill in Move.search([('company_id', '=', company.id), ('state', '=', 'posted'),
                                     ('move_type', '=', 'in_invoice'),
                                     ('payment_state', 'in', ('not_paid', 'partial'))], order='invoice_date_due'):
                payables.append({'who': bill.partner_id.name, 'desc': bill.ref or bill.name,
                                 'amount': bill.amount_residual})
            for bill in Move.search([('company_id', '=', company.id), ('state', '=', 'draft'),
                                     ('move_type', '=', 'in_invoice'), ('scout_approval_state', '=', 'to_approve')]):
                to_approve.append({'who': bill.partner_id.name, 'desc': bill.ref or _('Factura de proveedor'),
                                   'amount': bill.amount_total})
            for tm in self.env['scout.treasury.move'].sudo().search([('company_id', '=', company.id),
                                                                     ('state', '=', 'to_approve')]):
                to_approve.append({'who': tm.partner_id.name or '', 'desc': tm.description, 'amount': tm.amount})

        total_income = st['total_income']
        total_expense = st['total_expense']
        return {
            'doc_ids': wizard.ids,
            'doc_model': wizard._name,
            'docs': wizard,
            'company': company,
            'currency': currency,
            'money': lambda value: format_amount(self.env, value or 0.0, currency),
            'date_from': format_date(self.env, wizard.date_from),
            'date_to': format_date(self.env, wizard.date_to),
            'accounts': account_rows,
            'opening_total': sum(r['opening'] for r in account_rows),
            'closing_total': sum(r['closing'] for r in account_rows),
            'income_groups': ordered_groups,
            'expenses': expenses,
            'transfers': transfers,
            'total_income': total_income,
            'total_expense': total_expense,
            'available': total_income - total_expense,
            'receivables': receivables,
            'payables': payables,
            'to_approve': to_approve,
            'total_receivable': sum(r['amount'] for r in receivables),
            'total_payable': sum(r['amount'] for r in payables),
            'notes': wizard.notes,
        }
