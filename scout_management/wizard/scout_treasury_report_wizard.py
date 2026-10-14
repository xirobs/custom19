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
        journals = self.env['account.journal'].sudo().search([
            ('type', 'in', ('cash', 'bank')), ('company_id', '=', company.id)])
        liquidity_accounts = journals.default_account_id
        MoveLine = self.env['account.move.line'].sudo()
        base = [('parent_state', '=', 'posted'), ('company_id', '=', company.id)]

        def balance(account, domain):
            return sum(MoveLine.search(base + [('account_id', '=', account.id)] + domain).mapped('balance'))

        lines = MoveLine.search(base + [('account_id', 'in', liquidity_accounts.ids),
                                        ('date', '>=', wizard.date_from), ('date', '<=', wizard.date_to)],
                                order='date, id')
        group_labels = dict(self.env['scout.treasury.category']._fields['report_group'].selection)
        group_labels.update({'collections': _('Cobranza de cargos (registros, cuotas, campamentos)'),
                             'other': _('Otros ingresos')})
        income_groups, expenses, transfers = {}, [], []
        totals = {j.id: {'income': 0.0, 'expense': 0.0, 'tin': 0.0, 'tout': 0.0} for j in journals}
        for line in lines:
            journal = journals.filtered(lambda j: j.default_account_id == line.account_id)[:1]
            kind, group, label, who = self._classify_line(line, liquidity_accounts)
            entry = {'date': line.date, 'label': label, 'who': who, 'journal': journal.name,
                     'ref': (tm.reference or tm.name) if (tm := line.move_id.scout_treasury_move_id) else (line.move_id.name or ''),
                     'amount': abs(line.balance)}
            if kind == 'transfer':
                key = 'tin' if line.balance > 0 else 'tout'
                totals[journal.id][key] += abs(line.balance)
                if line.balance < 0:
                    entry['dest'] = ', '.join((line.move_id.line_ids - line).account_id.mapped('name'))
                    transfers.append(entry)
                continue
            if line.balance > 0:
                totals[journal.id]['income'] += line.balance
                grp = income_groups.setdefault(group or 'other', {'label': group_labels.get(group or 'other'),
                                                                  'moves': [], 'total': 0.0})
                grp['moves'].append(entry)
                grp['total'] += line.balance
            else:
                totals[journal.id]['expense'] += -line.balance
                tmove = line.move_id.scout_treasury_move_id
                entry['category'] = tmove.category_id.name if tmove else _('Pago a proveedor')
                expenses.append(entry)

        account_rows = []
        for journal in journals:
            acc = journal.default_account_id
            if not acc:
                continue
            t = totals[journal.id]
            account_rows.append({
                'journal': journal,
                'opening': balance(acc, [('date', '<', wizard.date_from)]),
                'income': t['income'], 'expense': t['expense'],
                'transfer': t['tin'] - t['tout'],
                'closing': balance(acc, [('date', '<=', wizard.date_to)]),
            })

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

        ordered_groups = [income_groups[k] for k in self.GROUP_ORDER if k in income_groups]
        total_income = sum(g['total'] for g in ordered_groups)
        total_expense = sum(e['amount'] for e in expenses)
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
