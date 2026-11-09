# -*- coding: utf-8 -*-
import base64
from collections import OrderedDict
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import file_open, format_amount, format_date


class ScoutTreasuryReportWizard(models.TransientModel):
    _name = 'scout.treasury.report.wizard'
    _description = 'Reporte de ingresos y egresos'

    date_from = fields.Date(string='Desde', required=True,
                            default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(string='Hasta', required=True, default=fields.Date.context_today)
    include_pending = fields.Boolean(string='Incluir cuentas por cobrar y por pagar', default=True)
    include_detail = fields.Boolean(string='Agregar detalle de movimientos', default=True,
                                    help='Después de la infografía agrega las hojas con cada ingreso y gasto.')
    audience = fields.Char(string='Dirigido a', default='Padres, tutores y jefes')
    trend_months = fields.Selection([('0', 'No incluir'), ('3', 'Últimos 3 meses'), ('6', 'Últimos 6 meses'),
                                     ('12', 'Últimos 12 meses')], string='Comparativo por mes', default='12',
                                    required=True,
                                    help='Agrega una hoja con ingresos y egresos mes por mes hasta el mes del corte. '
                                         'Los meses anteriores a la plataforma salen del Historial anterior.')
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
                                    ('payment_state', 'in', ('not_paid', 'partial'))],
                                   order='invoice_date')._scout_live_charges():
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
            # deudas y adeudos anteriores a la plataforma
            debts = self.env['scout.debt'].sudo().summary(company)
            for debt in debts['receivable']:
                receivables.append({'who': debt._who(), 'desc': debt.name, 'amount': debt.residual})
            for debt in debts['payable']:
                payables.append({'who': debt._who(), 'desc': debt.name, 'amount': debt.residual})
            for tm in self.env['scout.treasury.move'].sudo().search([('company_id', '=', company.id),
                                                                     ('state', '=', 'to_approve')]):
                to_approve.append({'who': tm.partner_id.name or '', 'desc': tm.description, 'amount': tm.amount})

        total_income = st['total_income']
        total_expense = st['total_expense']
        closing_total = sum(r['closing'] for r in account_rows)
        total_receivable = sum(r['amount'] for r in receivables)
        total_payable = sum(r['amount'] for r in payables)
        projected = closing_total + total_receivable - total_payable
        money = lambda value: format_amount(self.env, value or 0.0, currency)

        def pct(value, total):
            return (value / total * 100.0) if total else 0.0

        def bars(rows, total):
            top = max([r['amount'] for r in rows] or [0.0])
            for r in rows:
                r['pct'] = pct(r['amount'], total)
                r['width'] = max(2.0, r['amount'] / top * 100.0) if top and r['amount'] > 0 else 0.0
            return rows

        income_rows = bars(sorted([{'label': g['label'], 'amount': g['total'], 'count': len(g['moves'])}
                                   for g in ordered_groups if g['total']], key=lambda r: -r['amount']), total_income)
        # Con pocos gastos se muestra cada concepto (Stickers, Escudos...); con muchos, por categoría
        labels = {(e.get('label') or '').strip() for e in expenses}
        by_label = len(labels) <= 8 and all(labels)
        exp_map = OrderedDict()
        for e in expenses:
            key = (e.get('label') or '').strip() if by_label else (e['category'] or _('Otros gastos'))
            exp_map[key] = exp_map.get(key, 0.0) + e['amount']
        expense_rows = bars(sorted([{'label': k, 'amount': v} for k, v in exp_map.items() if v],
                                   key=lambda r: -r['amount']), total_expense)

        def grouped(items, limit=8):
            agg = OrderedDict()
            for it in items:
                key = (it['desc'] or '').strip() or _('Sin concepto')
                row = agg.setdefault(key, {'label': key, 'amount': 0.0, 'count': 0, 'who': set()})
                row['amount'] += it['amount']
                row['count'] += 1
                if it.get('who'):
                    row['who'].add(it['who'])
            rows = sorted(agg.values(), key=lambda r: -r['amount'])
            if len(rows) > limit:
                rest = rows[limit - 1:]
                rows = rows[:limit - 1] + [{'label': _('Otros conceptos'), 'amount': sum(r['amount'] for r in rest),
                                            'count': sum(r['count'] for r in rest), 'who': set()}]
            for r in rows:
                r['who'] = ', '.join(sorted(r['who'])) if len(r['who']) == 1 else (
                    _('%s personas', len(r['who'])) if r['who'] else '')
            return rows

        payable_rows = grouped(payables)
        receivable_rows = grouped(receivables)

        # gráfica "panorama": valores absolutos escalados a la columna más alta
        overview = [
            {'label': _('Disponible'), 'amount': closing_total, 'color': '#1b2f5e'},
            {'label': _('Ingresos'), 'amount': total_income, 'color': '#1f8a3b'},
            {'label': _('Pagado'), 'amount': total_expense, 'color': '#d62828'},
        ]
        if wizard.include_pending:
            overview += [
                {'label': _('Por cobrar'), 'amount': total_receivable, 'color': '#e0a800'},
                {'label': _('Por pagar'), 'amount': total_payable, 'color': '#8b1a2b'},
                {'label': _('Proyectado'), 'amount': projected, 'color': '#1f8a3b' if projected >= 0 else '#d62828'},
            ]
        top = max([abs(o['amount']) for o in overview] or [0.0]) or 1.0
        for o in overview:
            o['height'] = int(round(abs(o['amount']) / top * 120)) if o['amount'] else 0

        def listing(rows, limit=6):
            parts = ['%s %s' % (r['label'], money(r['amount'])) for r in rows[:limit]]
            return ', '.join(parts) + (', …' if len(rows) > limit else '')

        findings = []
        findings.append(_('Disponible hoy: %(total)s (%(detail)s).', total=money(closing_total),
                          detail=', '.join('%s %s' % (r['journal'].name, money(r['closing'])) for r in account_rows)))
        if income_rows:
            findings.append(_('Ingresos del periodo: %(total)s — %(detail)s.', total=money(total_income),
                              detail=', '.join('%s %s (%s)' % (r['label'], money(r['amount']), r['count'])
                                               for r in income_rows)))
        else:
            findings.append(_('No hubo ingresos en el periodo.'))
        if expense_rows:
            findings.append(_('Pagos realizados: %(detail)s. Total %(total)s, ya reflejado en el disponible.',
                              detail=listing(expense_rows), total=money(total_expense)))
        if wizard.include_pending:
            if payable_rows:
                findings.append(_('Deudas pendientes: %(detail)s. Total %(total)s.', detail=listing(payable_rows),
                                  total=money(total_payable)))
            if receivable_rows:
                findings.append(_('Cobros pendientes: %(detail)s. Total %(total)s.', detail=listing(receivable_rows),
                                  total=money(total_receivable)))
            if projected < 0:
                findings.append(_('Aun cobrando todo lo pendiente faltarían %s para cubrir las deudas.',
                                  money(-projected)))
            else:
                findings.append(_('Cobrando lo pendiente y pagando las deudas quedarían %s disponibles.',
                                  money(projected)))
        if to_approve:
            findings.append(_('%(n)s gasto(s) por %(amount)s esperan aprobación del jefe de grupo.',
                              n=len(to_approve), amount=money(sum(t['amount'] for t in to_approve))))

        trend = self._trend(company, wizard.date_to, int(wizard.trend_months or 0), money) \
            if int(wizard.trend_months or 0) else False

        logo = company.logo
        if not logo:
            try:
                with file_open('scout_management/static/img/logo_grupo.png', 'rb') as handle:
                    logo = base64.b64encode(handle.read())
            except Exception:
                logo = False

        return {
            'logo': logo,
            'trend': trend,
            'audience': wizard.audience,
            'include_detail': wizard.include_detail,
            'include_pending': wizard.include_pending,
            'income_rows': income_rows,
            'expense_rows': expense_rows,
            'payable_rows': payable_rows,
            'receivable_rows': receivable_rows,
            'overview': overview,
            'projected': projected,
            'findings': findings,
            'cut_date': format_date(self.env, wizard.date_to, date_format='d MMMM y'),
            'doc_ids': wizard.ids,
            'doc_model': wizard._name,
            'docs': wizard,
            'company': company,
            'currency': currency,
            'money': money,
            'date_from': format_date(self.env, wizard.date_from),
            'date_to': format_date(self.env, wizard.date_to),
            'accounts': account_rows,
            'opening_total': sum(r['opening'] for r in account_rows),
            'closing_total': closing_total,
            'income_groups': ordered_groups,
            'expenses': expenses,
            'transfers': transfers,
            'total_income': total_income,
            'total_expense': total_expense,
            'available': total_income - total_expense,
            'receivables': receivables,
            'payables': payables,
            'to_approve': to_approve,
            'total_receivable': total_receivable,
            'total_payable': total_payable,
            'notes': wizard.notes,
        }

    MONTHS = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

    def _trend(self, company, end, months, money):
        """Ingresos y egresos mes por mes (plataforma + historial anterior) de los últimos `months` meses."""
        from ..models.scout_treasury_ledger import GROUP_LABELS
        from ..models.scout_treasury_history import HISTORY_GROUPS
        Ledger = self.env['scout.treasury.ledger']
        History = self.env['scout.treasury.history']
        first = end.replace(day=1) - relativedelta(months=months - 1)
        rows, groups, cats = [], {}, {}
        hist_labels = dict(HISTORY_GROUPS)
        for i in range(months):
            start = first + relativedelta(months=i)
            stop = min(start + relativedelta(months=1, days=-1), end)
            st = Ledger.statement(start, stop, company)
            hist = History.totals(start, stop, company)
            income = st['total_income'] + hist['income']
            expense = st['total_expense'] + hist['expense']
            for g in st['income_groups']:
                if g['total']:
                    groups[g['label']] = groups.get(g['label'], 0.0) + g['total']
            for key, amount in hist['groups'].items():
                label = GROUP_LABELS.get(key) or hist_labels.get(key, key)
                groups[label] = groups.get(label, 0.0) + amount
            for e in st['expenses']:
                key = e.get('category') or _('Otros gastos')
                cats[key] = cats.get(key, 0.0) + e['amount']
            for key, amount in hist['categories'].items():
                cats[key] = cats.get(key, 0.0) + amount
            rows.append({'label': '%s %s' % (self.MONTHS[start.month - 1], start.strftime('%y')),
                         'income': income, 'expense': expense, 'net': income - expense,
                         'historic': bool(hist['count']), 'empty': not income and not expense})
        total_income = sum(r['income'] for r in rows)
        total_expense = sum(r['expense'] for r in rows)
        top = max([max(r['income'], r['expense']) for r in rows] or [0.0]) or 1.0
        for r in rows:
            r['h_in'] = int(round(r['income'] / top * 110)) if r['income'] > 0 else 0
            r['h_out'] = int(round(r['expense'] / top * 110)) if r['expense'] > 0 else 0

        def ranked(data, total):
            out = sorted([{'label': k, 'amount': v} for k, v in data.items() if v], key=lambda r: -r['amount'])
            peak = max([r['amount'] for r in out] or [0.0]) or 1.0
            for r in out:
                r['pct'] = (r['amount'] / total * 100.0) if total else 0.0
                r['width'] = max(2.0, r['amount'] / peak * 100.0)
            return out

        best = max(rows, key=lambda r: r['income']) if rows else None
        notes = []
        if best and best['income']:
            notes.append(_('El mes con más ingresos fue %(m)s (%(a)s).', m=best['label'], a=money(best['income'])))
        empty = [r['label'] for r in rows if r['empty']]
        if empty:
            notes.append(_('Sin movimientos registrados: %s.', ', '.join(empty)))
        if any(r['historic'] for r in rows):
            notes.append(_('Los meses marcados con * incluyen el historial capturado de antes de la plataforma.'))
        return {
            'months': months, 'rows': rows, 'total_income': total_income, 'total_expense': total_expense,
            'net': total_income - total_expense, 'income_by_group': ranked(groups, total_income),
            'expense_by_category': ranked(cats, total_expense), 'notes': notes,
            'from_label': rows[0]['label'] if rows else '', 'to_label': rows[-1]['label'] if rows else '',
        }
