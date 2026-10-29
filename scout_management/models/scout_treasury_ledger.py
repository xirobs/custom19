# -*- coding: utf-8 -*-
"""Libro de caja y bancos: UNA sola forma de calcular saldos y movimientos para el portal, la plataforma y el PDF.

Saldo de una caja / banco = todo lo registrado en su cuenta contable
                          + lo que esté en sus cuentas puente (pagos aún no conciliados) de ese mismo diario.
Así cuadran los cobros de registros, ventas de tienda, pagos con Mercado Pago, capturas de ingresos y egresos,
traspasos y pagos a proveedores, se hayan registrado desde donde se hayan registrado.

Además:
* Comisión de Mercado Pago: al confirmarse un pago en línea se registra sola como egreso de la cuenta de
  Mercado Pago, para que su saldo sea lo que Mercado Pago realmente tiene (neto).
* Arqueo / conciliación: se captura lo que hay físicamente en la caja o lo que dice Mercado Pago o el banco; la
  diferencia queda registrada y, si se confirma, se hace el ajuste (sobrante o faltante).
"""
import logging
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_is_zero

_logger = logging.getLogger(__name__)

ORIGIN_GROUP = {'registration': 'registration', 'store': 'store', 'food': 'food', 'event': 'activity',
                'fee': 'fees', 'donation': 'donation', 'other': 'collections'}
GROUP_LABELS = {
    'sales': 'Ventas del día', 'store': 'Tienda scout', 'food': 'Venta de alimentos', 'registration': 'Registros',
    'fees': 'Cuotas', 'activity': 'Campamentos y actividades', 'donation': 'Donativos y colaboraciones',
    'collections': 'Cobranza de otros cargos', 'adjust': 'Ajustes de arqueo (sobrantes)', 'other': 'Otros ingresos',
}
GROUP_ORDER = ['registration', 'fees', 'activity', 'store', 'food', 'sales', 'donation', 'collections', 'other',
               'adjust']


class ScoutTreasuryLedger(models.AbstractModel):
    _name = 'scout.treasury.ledger'
    _description = 'Libro de caja y bancos (cálculo único)'

    # ------------------------------------------------------------------ cuentas de cada caja / banco
    @api.model
    def _journals(self, company=None):
        company = company or self.env.company
        return self.env['account.journal'].sudo().search(
            [('type', 'in', ('cash', 'bank')), ('company_id', '=', company.id)], order='type, sequence, id')

    @api.model
    def _accounts(self, journal):
        """(cuenta propia, cuentas puente del diario)."""
        own = journal.default_account_id
        lines = journal.inbound_payment_method_line_ids | journal.outbound_payment_method_line_ids
        bridge = lines.payment_account_id - own
        company = journal.company_id
        for fname in ('account_journal_payment_debit_account_id', 'account_journal_payment_credit_account_id'):
            if fname in company._fields and company[fname]:
                bridge |= company[fname]
        if 'suspense_account_id' in journal._fields and journal.suspense_account_id:
            bridge |= journal.suspense_account_id
        return own, bridge - own

    @api.model
    def _journal_domain(self, journal):
        own, bridge = self._accounts(journal)
        domain = [('account_id', '=', own.id)] if own else [('id', '=', 0)]
        if bridge:
            domain = ['|'] + domain + ['&', ('account_id', 'in', bridge.ids), ('journal_id', '=', journal.id)]
        return domain

    @api.model
    def _base(self, company):
        return [('parent_state', '=', 'posted'), ('company_id', '=', company.id)]

    @api.model
    def _balance(self, journal, date_to=None, before=None):
        Line = self.env['account.move.line'].sudo()
        domain = self._base(journal.company_id) + self._journal_domain(journal)
        if date_to:
            domain.append(('date', '<=', date_to))
        if before:
            domain.append(('date', '<', before))
        data = Line._read_group(domain, [], ['balance:sum'])
        return (data[0][0] or 0.0) if data else 0.0

    @api.model
    def balances(self, company=None, date_to=None):
        """[{'journal', 'balance', 'last_count'}] y el total."""
        rows = []
        Count = self.env['scout.treasury.count'].sudo()
        for journal in self._journals(company):
            rows.append({'journal': journal, 'balance': self._balance(journal, date_to=date_to),
                         'kind': self._journal_kind(journal),
                         'last_count': Count.search([('journal_id', '=', journal.id), ('state', '!=', 'cancel')],
                                                    order='date desc, id desc', limit=1)})
        return rows

    @api.model
    def _journal_kind(self, journal):
        if journal.type == 'cash':
            return 'cash'
        providers = self.env['payment.provider'].sudo().search([('company_id', '=', journal.company_id.id)])
        return 'online' if providers.filtered(lambda p: p.journal_id == journal) else 'bank'

    # ------------------------------------------------------------------ clasificación de cada movimiento
    @api.model
    def _classify(self, line, liquidity):
        """{'kind': income|expense|transfer, 'group', 'label', 'who', 'source', 'ref'}"""
        move = line.move_id
        others = move.line_ids - line
        original = move.reversed_entry_id if 'reversed_entry_id' in move._fields else False
        tmove = move.scout_treasury_move_id or (original.scout_treasury_move_id if original else False)
        res = {'who': '', 'ref': move.name or '', 'source': _('Contabilidad'), 'reversal': bool(original)}
        if tmove:
            res.update(ref=tmove.name, who=tmove.member_id.name or tmove.partner_id.name or '',
                       label=(tmove.description or '').splitlines()[0] if tmove.description else tmove.name,
                       source=_('Venta %s', tmove.store_sale_id.name) if tmove.store_sale_id else _('Captura'))
            if tmove.move_type == 'transfer':
                res.update(kind='transfer', group=None)
            elif tmove.move_type == 'income':
                res.update(kind='income', group=tmove.category_id.report_group or 'other')
                if tmove.category_id == self.env.ref('scout_management.category_adjust_in', raise_if_not_found=False):
                    res['group'] = 'adjust'
            else:
                res.update(kind='expense', group=None, category=tmove.category_id.name or _('Otros gastos'))
            return res
        payment = move.origin_payment_id if 'origin_payment_id' in move._fields else False
        if payment:
            invoices = payment.reconciled_invoice_ids | payment.reconciled_bill_ids
            if not invoices and 'invoice_ids' in payment._fields:
                invoices = payment.invoice_ids
            tx = payment.payment_transaction_id if 'payment_transaction_id' in payment._fields else False
            res['ref'] = payment.name
            res['source'] = _('Mercado Pago') if tx and tx.provider_code == 'mercado_pago' else (
                _('Pago en línea') if tx else _('Cobro registrado'))
            if payment.payment_type == 'inbound' and not (others and all(o.account_id in liquidity for o in others)):
                inv = invoices.filtered(lambda i: i.move_type == 'out_invoice')[:1]
                origin = (inv.scout_charge_origin or inv._scout_guess_origin()[0]) if inv else 'other'
                members = invoices.scout_member_id
                payer = tx.scout_payer_name if tx and tx.scout_payer_name else ''
                res.update(kind='income', group=ORIGIN_GROUP.get(origin, 'collections'),
                           label=', '.join(i._scout_label(with_ref=False) for i in invoices[:3]) or payment.memo
                           or payment.name,
                           who=', '.join(members.mapped('name')) or payment.partner_id.name or '')
                if payer:
                    res['who'] = '%s · pagó %s' % (res['who'], payer) if res['who'] else payer
                return res
            if others and all(o.account_id in liquidity for o in others):
                res.update(kind='transfer', group=None, label=payment.memo or payment.name)
                return res
            res.update(kind='expense', group=None, category=_('Pago a proveedor'),
                       label=', '.join(invoices.mapped(lambda b: b.ref or b.name)) or payment.memo or payment.name,
                       who=payment.partner_id.name or '')
            return res
        if others and all(o.account_id in liquidity for o in others):
            res.update(kind='transfer', group=None, label=move.ref or move.name)
            return res
        st = move.statement_line_id if 'statement_line_id' in move._fields else False
        label = (st.payment_ref if st else '') or move.ref or ', '.join(others.account_id.mapped('name'))
        if line.balance > 0:
            res.update(kind='income', group='other', label=label, who=move.partner_id.name or '')
        else:
            res.update(kind='expense', group=None, category=_('Otros gastos'), label=label,
                       who=move.partner_id.name or '')
        return res

    # ------------------------------------------------------------------ estado de cuenta del periodo
    @api.model
    def statement(self, date_from, date_to, company=None, journal_ids=None):
        company = company or self.env.company
        journals = self._journals(company)
        if journal_ids:
            journals = journals.filtered(lambda j: j.id in journal_ids)
        Line = self.env['account.move.line'].sudo()
        liquidity = self.env['account.account']
        for j in self._journals(company):
            own, bridge = self._accounts(j)
            liquidity |= own | bridge
        rows, movements = [], []
        groups, expenses, transfers = {}, [], []
        seen = set()
        for journal in journals:
            domain = self._base(company) + self._journal_domain(journal) + [
                ('date', '>=', date_from), ('date', '<=', date_to)]
            lines = Line.search(domain, order='date, id')
            t = {'income': 0.0, 'expense': 0.0, 'tin': 0.0, 'tout': 0.0}
            for line in lines:
                if float_is_zero(line.balance, precision_rounding=company.currency_id.rounding):
                    continue
                others = line.move_id.line_ids - line
                # Paso de la cuenta puente a la cuenta propia dentro del mismo diario: no es dinero que entra o sale
                if others and all(self._in_journal(o, journal) for o in others):
                    continue
                info = self._classify(line, liquidity)
                amount = line.balance
                entry = dict(info, date=line.date, journal=journal, amount=abs(amount), signed=amount,
                             line_id=line.id)
                if info['kind'] == 'transfer':
                    key = 'tin' if amount > 0 else 'tout'
                    t[key] += abs(amount)
                    if amount < 0 and line.move_id.id not in seen:
                        seen.add(line.move_id.id)
                        dest = self._journal_of(line.move_id.line_ids - line, self._journals(company))
                        entry['dest'] = dest.name if dest else ', '.join(
                            (line.move_id.line_ids - line).account_id.mapped('name'))
                        transfers.append(entry)
                elif info.get('reversal') and info['kind'] in ('income', 'expense'):
                    # cancelación: resta del concepto original (no es un egreso o ingreso nuevo)
                    entry['label'] = _('Cancelación: %s', info['label'])
                    entry['amount'] = amount if info['kind'] == 'income' else -amount
                    if info['kind'] == 'income':
                        t['income'] += amount
                        grp = groups.setdefault(info['group'] or 'other', {
                            'key': info['group'] or 'other', 'label': GROUP_LABELS.get(info['group'] or 'other'),
                            'moves': [], 'total': 0.0})
                        grp['moves'].append(entry)
                        grp['total'] += amount
                    else:
                        t['expense'] -= amount
                        expenses.append(entry)
                elif amount > 0:
                    t['income'] += amount
                    grp = groups.setdefault(info['group'] or 'other', {
                        'key': info['group'] or 'other', 'label': GROUP_LABELS.get(info['group'] or 'other'),
                        'moves': [], 'total': 0.0})
                    grp['moves'].append(entry)
                    grp['total'] += amount
                else:
                    t['expense'] += -amount
                    expenses.append(entry)
                movements.append(entry)
            opening = self._balance(journal, before=date_from)
            closing = self._balance(journal, date_to=date_to)
            rows.append({'journal': journal, 'kind': self._journal_kind(journal), 'opening': opening,
                         'income': t['income'], 'expense': t['expense'], 'tin': t['tin'], 'tout': t['tout'],
                         'transfer': t['tin'] - t['tout'], 'closing': closing,
                         'check': round(opening + t['income'] - t['expense'] + t['tin'] - t['tout'] - closing, 2)})
        ordered = [groups[k] for k in GROUP_ORDER if k in groups] + [g for k, g in groups.items()
                                                                      if k not in GROUP_ORDER]
        movements.sort(key=lambda e: (e['date'], e['line_id']), reverse=True)
        total_income = sum(r['income'] for r in rows)
        total_expense = sum(r['expense'] for r in rows)
        return {
            'date_from': date_from, 'date_to': date_to, 'company': company, 'rows': rows,
            'opening_total': sum(r['opening'] for r in rows), 'closing_total': sum(r['closing'] for r in rows),
            'income_groups': ordered, 'expenses': sorted(expenses, key=lambda e: (e['date'], e['line_id'])),
            'transfers': transfers, 'movements': movements, 'total_income': total_income,
            'total_expense': total_expense, 'net': total_income - total_expense,
            'squared': all(abs(r['check']) < 0.01 for r in rows),
        }

    @api.model
    def _in_journal(self, line, journal):
        own, bridge = self._accounts(journal)
        return line.account_id == own or (line.account_id in bridge and line.journal_id == journal)

    @api.model
    def _journal_of(self, lines, journals):
        for line in lines:
            for journal in journals:
                if self._in_journal(line, journal):
                    return journal
        return False

    @api.model
    def pending(self, company=None):
        """Lo que todavía no es dinero: por cobrar, por pagar, por aprobar."""
        company = company or self.env.company
        Move = self.env['account.move'].sudo()
        receivable = Move.search([('company_id', '=', company.id), ('state', '=', 'posted'),
                                  ('move_type', '=', 'out_invoice'), ('payment_state', 'in', ('not_paid', 'partial'))])
        payable = Move.search([('company_id', '=', company.id), ('state', '=', 'posted'),
                               ('move_type', '=', 'in_invoice'), ('payment_state', 'in', ('not_paid', 'partial'))])
        to_approve = self.env['scout.treasury.move'].sudo().search([('company_id', '=', company.id),
                                                                     ('state', 'in', ('draft', 'to_approve'))])
        return {'receivable': sum(receivable.mapped('amount_residual')), 'receivable_count': len(receivable),
                'payable': sum(payable.mapped('amount_residual')), 'payable_count': len(payable),
                'captures': to_approve}


class ScoutTreasuryLedgerMP(models.AbstractModel):
    _inherit = 'scout.treasury.ledger'

    @api.model
    def _scout_mp_backfill(self, company=None):
        """Trae de Mercado Pago la comisión (y quién pagó) de los pagos en línea que ya entraron sin ese dato, y
        la registra como egreso de la cuenta de Mercado Pago con la fecha del pago. Así el saldo queda igual al
        de la app de Mercado Pago."""
        company = company or self.env.company
        txs = self.env['payment.transaction'].sudo().search([
            ('provider_code', '=', 'mercado_pago'), ('state', '=', 'done'), ('company_id', '=', company.id),
            ('scout_fee_move_id', '=', False), ('provider_reference', '!=', False)])
        done, total, errors = 0, 0.0, []
        for tx in txs:
            try:
                data = tx._send_api_request('GET', '/v1/payments/%s' % tx.provider_reference)
                if isinstance(data, dict):
                    tx._scout_store_payer(data)
                if tx.payment_id and tx.scout_fee_amount:
                    tx._scout_register_fee(tx.payment_id)
                if tx.scout_fee_move_id:
                    done += 1
                    total += tx.scout_fee_amount
            except Exception as e:  # noqa: BLE001
                _logger.info('No se pudo consultar el pago %s en Mercado Pago', tx.reference, exc_info=True)
                errors.append('%s: %s' % (tx.reference, e))
        return {'checked': len(txs), 'count': done, 'fee': total, 'errors': errors}


class ScoutTreasuryCount(models.Model):
    """Arqueo de caja / conciliación con Mercado Pago o el banco."""
    _name = 'scout.treasury.count'
    _description = 'Arqueo de caja / conciliación'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    name = fields.Char(string='Folio', compute='_compute_name', store=True)
    date = fields.Date(string='Fecha', required=True, default=fields.Date.context_today)
    journal_id = fields.Many2one('account.journal', string='Caja / banco', required=True,
                                 domain="[('type', 'in', ('cash', 'bank'))]")
    system_amount = fields.Monetary(string='Saldo en la plataforma', currency_field='currency_id', readonly=True)
    counted_amount = fields.Monetary(string='Lo que hay realmente', currency_field='currency_id', required=True,
                                     help='Caja: el efectivo contado. Mercado Pago / banco: el saldo que muestra '
                                          'la app o el estado de cuenta.')
    difference = fields.Monetary(string='Diferencia', compute='_compute_difference', store=True,
                                 currency_field='currency_id')
    detail = fields.Text(string='Conteo / notas', help='Ej. 3 billetes de 500, 4 de 200… o la explicación.')
    state = fields.Selection([('done', 'Cuadra'), ('diff', 'Con diferencia'), ('adjusted', 'Ajustado'),
                              ('cancel', 'Cancelado')], string='Estado', default='done', required=True, tracking=True)
    adjustment_id = fields.Many2one('scout.treasury.move', string='Ajuste', readonly=True, copy=False)
    user_id = fields.Many2one('res.users', string='Hizo el arqueo', default=lambda self: self.env.user, readonly=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')

    @api.depends('journal_id', 'date')
    def _compute_name(self):
        for rec in self:
            rec.name = _('Arqueo %(j)s %(d)s', j=rec.journal_id.name or '',
                         d=rec.date.strftime('%d/%m/%Y') if rec.date else '')

    @api.depends('counted_amount', 'system_amount')
    def _compute_difference(self):
        for rec in self:
            rec.difference = (rec.counted_amount or 0.0) - (rec.system_amount or 0.0)

    @api.model_create_multi
    def create(self, vals_list):
        Ledger = self.env['scout.treasury.ledger']
        for vals in vals_list:
            journal = self.env['account.journal'].sudo().browse(vals.get('journal_id'))
            vals['system_amount'] = Ledger._balance(journal, date_to=vals.get('date') or fields.Date.context_today(self))
            diff = (vals.get('counted_amount') or 0.0) - vals['system_amount']
            vals['state'] = 'done' if abs(diff) < 0.005 else 'diff'
        return super().create(vals_list)

    def action_adjust(self):
        """Registra el sobrante (ingreso) o faltante (egreso) para que el saldo quede igual a lo real.
        El faltante, como todo egreso, lo aprueba el jefe de grupo."""
        for rec in self.filtered(lambda r: r.state == 'diff' and not r.adjustment_id):
            positive = rec.difference > 0
            online = self.env['scout.treasury.ledger']._journal_kind(rec.journal_id) == 'online'
            category = self.env.ref('scout_management.category_adjust_in' if positive else (
                'scout_management.category_bank_fee' if online else 'scout_management.category_adjust_out'))
            move = self.env['scout.treasury.move'].create({
                'move_type': 'income' if positive else 'expense', 'date': rec.date, 'journal_id': rec.journal_id.id,
                'category_id': category.id, 'amount': abs(rec.difference),
                'payment_method': 'cash' if rec.journal_id.type == 'cash' else 'transfer',
                'description': _('%(kind)s del %(name)s (plataforma %(sys)s, real %(real)s)%(note)s',
                                 kind=_('Sobrante') if positive else _('Faltante'), name=rec.name,
                                 sys='$%.2f' % rec.system_amount, real='$%.2f' % rec.counted_amount,
                                 note=('\n' + rec.detail) if rec.detail else ''),
                'company_id': rec.company_id.id,
            })
            move.action_post()
            rec.write({'adjustment_id': move.id, 'state': 'adjusted'})
        return True

    def action_cancel(self):
        self.filtered(lambda r: not r.adjustment_id or r.adjustment_id.state == 'cancel').write({'state': 'cancel'})


class PaymentTransactionFee(models.Model):
    _inherit = 'payment.transaction'

    scout_fee_move_id = fields.Many2one('scout.treasury.move', string='Comisión registrada', readonly=True,
                                        copy=False)

    def _create_payment(self, **extra_create_values):
        payment = super()._create_payment(**extra_create_values)
        try:
            self._scout_register_fee(payment)
        except Exception:  # noqa: BLE001 - la comisión nunca debe frenar el registro del pago
            _logger.exception('No se pudo registrar la comisión de Mercado Pago')
        return payment

    def _scout_register_fee(self, payment):
        """La comisión que cobra Mercado Pago sale de su saldo: egreso automático (no requiere aprobación,
        Mercado Pago ya la descontó)."""
        self.ensure_one()
        if self.provider_code != 'mercado_pago' or not self.scout_fee_amount or self.scout_fee_move_id:
            return
        journal = payment.journal_id or self.provider_id.journal_id
        category = self.env.ref('scout_management.category_bank_fee', raise_if_not_found=False)
        if not journal or not category:
            return
        move = self.env['scout.treasury.move'].sudo().create({
            'move_type': 'expense', 'date': payment.date or fields.Date.context_today(self),
            'journal_id': journal.id, 'category_id': category.id, 'amount': self.scout_fee_amount,
            'payment_method': 'other', 'reference': self.provider_reference or False,
            'member_id': self.scout_member_id.id or False,
            'description': _('Comisión Mercado Pago del pago %(ref)s (%(label)s)', ref=self.reference,
                             label=self.scout_label or self.partner_name or ''),
            'company_id': self.company_id.id,
        })
        move._post_accounting()
        move.approved_by_id = self.env.ref('base.user_root')
        self.scout_fee_move_id = move


class ScoutTreasuryStatementWizard(models.TransientModel):
    """Pantalla de la plataforma con exactamente lo mismo que el portal y el PDF."""
    _name = 'scout.treasury.statement.wizard'
    _description = 'Caja y bancos: saldos y estado de cuenta'

    date_from = fields.Date(string='Desde', required=True,
                            default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(string='Hasta', required=True, default=fields.Date.context_today)
    journal_ids = fields.Many2many('account.journal', string='Cajas / bancos',
                                   domain="[('type', 'in', ('cash', 'bank'))]",
                                   help='Vacío = todas.')
    html = fields.Html(string='Estado de cuenta', compute='_compute_html', sanitize=False)

    def _compute_display_name(self):
        for wiz in self:
            wiz.display_name = _('Caja y bancos')

    @api.depends('date_from', 'date_to', 'journal_ids')
    def _compute_html(self):
        Ledger = self.env['scout.treasury.ledger']
        for wiz in self:
            if not wiz.date_from or not wiz.date_to or wiz.date_to < wiz.date_from:
                wiz.html = False
                continue
            values = Ledger._statement_values(wiz.date_from, wiz.date_to, journal_ids=wiz.journal_ids.ids or None)
            wiz.html = self.env['ir.qweb']._render('scout_management.treasury_statement_body', values)

    def action_print(self):
        self.ensure_one()
        report = self.env['scout.treasury.report.wizard'].create({'date_from': self.date_from,
                                                                  'date_to': self.date_to})
        return report.action_print()

    def action_mp_fees(self):
        res = self.env['scout.treasury.ledger']._scout_mp_backfill()
        msg = _('Comisiones registradas: %(n)s pagos, %(fee)s.', n=res['count'], fee='$ {:,.2f}'.format(res['fee'])) \
            if res['count'] else _('No hay comisiones pendientes de traer (revisados: %s).', res['checked'])
        if res['errors']:
            msg += ' ' + _('Sin respuesta de Mercado Pago en %s pago(s).', len(res['errors']))
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Mercado Pago'), 'message': msg, 'sticky': bool(res['errors']),
                           'type': 'warning' if res['errors'] else 'success',
                           'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    def action_count(self):
        return {'type': 'ir.actions.act_window', 'res_model': 'scout.treasury.count', 'view_mode': 'form',
                'target': 'new', 'name': _('Arqueo / conciliación')}


class ScoutTreasuryLedgerValues(models.AbstractModel):
    _inherit = 'scout.treasury.ledger'

    @api.model
    def _statement_values(self, date_from, date_to, journal_ids=None, link=None):
        """Valores para la plantilla compartida (portal y plataforma)."""
        from odoo.tools import format_amount
        company = self.env.company
        st = self.statement(date_from, date_to, company, journal_ids=journal_ids)
        st.update({
            'money': lambda v: format_amount(self.env, v or 0.0, company.currency_id),
            'fmt_date': lambda d: d.strftime('%d/%m/%Y') if d else '',
            'balances': self.balances(company),
            'pending': self.pending(company),
            'kind_labels': {'cash': _('Efectivo'), 'online': _('Pago en línea'), 'bank': _('Banco')},
            'link': link,
        })
        return st
