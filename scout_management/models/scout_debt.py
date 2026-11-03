# -*- coding: utf-8 -*-
"""Deudas y adeudos que vienen de antes de la plataforma (o que no nacen de una factura).

- "El grupo debe": insignias, seguros, uniformes, préstamos... que el grupo tiene pendiente de pagar.
- "Nos deben": lo que personas o secciones le deben al grupo.

Cada pago o cobro se registra como un movimiento de tesorería (egreso o ingreso) ligado a la deuda,
así el dinero sale/entra de la caja o banco real, aparece en el balance y la deuda baja sola hasta
quedar en cero. Lo abonado antes de la plataforma se captura en "Ya abonado antes".
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero

DEBT_KINDS = [
    ('badges', 'Insignias'), ('insurance', 'Seguros'), ('uniforms', 'Uniformes'),
    ('registration', 'Registro / afiliación'), ('fees', 'Cuotas'), ('camp', 'Campamento / actividad'),
    ('loan', 'Préstamo'), ('store', 'Tienda'), ('other', 'Otro'),
]


class ScoutDebt(models.Model):
    _name = 'scout.debt'
    _description = 'Deuda o adeudo del grupo'
    _inherit = ['mail.thread']
    _order = 'state_order, due_date, date, id'

    name = fields.Char(string='Concepto', required=True, tracking=True,
                       help='Ej. "Insignias de aniversario 2026" o "Seguro anual de la manada".')
    direction = fields.Selection([('payable', 'El grupo debe'), ('receivable', 'Nos deben')],
                                 string='Tipo', required=True, default='payable', tracking=True)
    kind = fields.Selection(DEBT_KINDS, string='Rubro', required=True, default='other', tracking=True)
    party_name = fields.Char(string='A quién / quién', tracking=True,
                             help='Proveedor, asociación, persona o sección. Si es un miembro, elígelo abajo.')
    partner_id = fields.Many2one('res.partner', string='Contacto')
    member_id = fields.Many2one('res.partner', string='Miembro', domain="[('is_scout', '=', True)]")
    section_id = fields.Many2one('scout.section', string='Sección')
    date = fields.Date(string='Fecha de la deuda', required=True, default=fields.Date.context_today)
    due_date = fields.Date(string='Fecha límite')
    amount = fields.Monetary(string='Importe total', required=True, currency_field='currency_id', tracking=True)
    paid_before = fields.Monetary(string='Ya abonado antes de la plataforma', currency_field='currency_id',
                                  tracking=True, help='Lo que ya se había pagado/cobrado antes de registrar la '
                                                      'deuda aquí (no mueve caja ni bancos).')
    payment_ids = fields.One2many('scout.treasury.move', 'debt_id', string='Pagos / cobros')
    paid_amount = fields.Monetary(string='Pagado en la plataforma', compute='_compute_amounts', store=True,
                                  currency_field='currency_id')
    in_approval = fields.Monetary(string='Pagos por aprobar', compute='_compute_amounts', store=True,
                                  currency_field='currency_id')
    residual = fields.Monetary(string='Pendiente', compute='_compute_amounts', store=True,
                               currency_field='currency_id')
    state = fields.Selection([('open', 'Pendiente'), ('partial', 'Abonada'), ('paid', 'Liquidada'),
                              ('cancel', 'Cancelada')], compute='_compute_amounts', store=True, string='Estado')
    state_order = fields.Integer(compute='_compute_amounts', store=True)
    cancelled = fields.Boolean(string='Cancelada', tracking=True)
    notes = fields.Text(string='Notas')
    company_id = fields.Many2one('res.company', string='Grupo', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')

    @api.depends('amount', 'paid_before', 'cancelled', 'payment_ids.state', 'payment_ids.amount')
    def _compute_amounts(self):
        for debt in self:
            posted = debt.payment_ids.filtered(lambda m: m.state == 'posted')
            waiting = debt.payment_ids.filtered(lambda m: m.state == 'to_approve')
            debt.paid_amount = sum(posted.mapped('amount'))
            debt.in_approval = sum(waiting.mapped('amount'))
            residual = (debt.amount or 0.0) - (debt.paid_before or 0.0) - debt.paid_amount
            rounding = debt.currency_id.rounding or 0.01
            debt.residual = 0.0 if float_is_zero(residual, precision_rounding=rounding) else residual
            if debt.cancelled:
                debt.state, debt.state_order = 'cancel', 3
            elif float_compare(debt.residual, 0.0, precision_rounding=rounding) <= 0:
                debt.state, debt.state_order = 'paid', 2
            elif debt.paid_amount or debt.paid_before:
                debt.state, debt.state_order = 'partial', 1
            else:
                debt.state, debt.state_order = 'open', 0

    @api.constrains('amount', 'paid_before')
    def _check_amounts(self):
        for debt in self:
            if float_compare(debt.amount, 0.0, precision_rounding=debt.currency_id.rounding or 0.01) <= 0:
                raise ValidationError(_('El importe de la deuda debe ser mayor a cero.'))
            if debt.paid_before and (debt.paid_before < 0 or debt.paid_before > debt.amount):
                raise ValidationError(_('Lo ya abonado no puede ser mayor que la deuda.'))

    @api.depends('name', 'party_name', 'member_id')
    def _compute_display_name(self):
        for debt in self:
            who = debt.member_id.name or debt.party_name or debt.partner_id.name
            debt.display_name = f"{debt.name} ({who})" if who else debt.name

    def _who(self):
        self.ensure_one()
        return self.member_id.name or self.party_name or self.partner_id.name or self.section_id.name or ''

    # ------------------------------------------------------------------ pagos
    def _payment_category(self):
        self.ensure_one()
        xmlid = 'scout_management.category_debt_payment' if self.direction == 'payable' \
            else 'scout_management.category_debt_collection'
        return self.env.ref(xmlid, raise_if_not_found=False)

    def action_register_payment(self, amount, journal, date=None, payment_method=None, reference=None,
                                attachment=None, attachment_name=None, post=True):
        """Registra un abono: egreso (El grupo debe) o ingreso (Nos deben) en la caja/banco elegido."""
        self.ensure_one()
        if self.state in ('paid', 'cancel'):
            raise UserError(_('Esta deuda ya está %s.', dict(self._fields['state'].selection)[self.state].lower()))
        rounding = self.currency_id.rounding or 0.01
        if float_compare(amount or 0.0, 0.0, precision_rounding=rounding) <= 0:
            raise UserError(_('Escribe el importe del abono.'))
        available = self.residual - self.in_approval
        if float_compare(amount, available, precision_rounding=rounding) > 0:
            raise UserError(_('El abono (%(amount)s) es mayor a lo pendiente (%(pending)s).',
                              amount='$ {:,.2f}'.format(amount), pending='$ {:,.2f}'.format(available)))
        if not journal:
            raise UserError(_('Elige la caja o banco.'))
        payable = self.direction == 'payable'
        label = (_('Pago de deuda: %s') if payable else _('Cobro de adeudo: %s')) % self.display_name
        move = self.env['scout.treasury.move'].create({
            'move_type': 'expense' if payable else 'income',
            'date': date or fields.Date.context_today(self),
            'journal_id': journal.id,
            'category_id': self._payment_category().id,
            'description': label,
            'amount': amount,
            'payment_method': payment_method or ('cash' if journal.type == 'cash' else 'transfer'),
            'reference': reference or False,
            'member_id': self.member_id.id or False,
            'section_id': self.section_id.id or False,
            'partner_id': self.partner_id.id or False,
            'attachment': attachment or False,
            'attachment_name': attachment_name or False,
            'debt_id': self.id,
            'company_id': self.company_id.id,
        })
        if post:
            move.action_post()
        self.message_post(body=_('%(what)s de %(amount)s desde %(journal)s (%(folio)s, %(state)s).',
                                 what=_('Pago') if payable else _('Cobro'), amount='$ {:,.2f}'.format(amount),
                                 journal=journal.name, folio=move.name,
                                 state=dict(move._fields['state'].selection)[move.state].lower()))
        return move

    def action_cancel_debt(self):
        for debt in self:
            if debt.payment_ids.filtered(lambda m: m.state in ('posted', 'to_approve')):
                raise UserError(_('La deuda ya tiene pagos registrados; cancela primero esos movimientos.'))
        self.write({'cancelled': True})

    def action_reopen(self):
        self.write({'cancelled': False})

    def action_open_payment_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'res_model': 'scout.debt.payment.wizard', 'view_mode': 'form',
            'target': 'new', 'context': {'default_debt_id': self.id, 'default_amount': self.residual - self.in_approval},
        }

    @api.model
    def summary(self, company=None):
        company = company or self.env.company
        debts = self.search([('company_id', '=', company.id), ('state', 'in', ('open', 'partial'))])
        pay = debts.filtered(lambda d: d.direction == 'payable')
        rec = debts.filtered(lambda d: d.direction == 'receivable')
        return {'payable': pay, 'receivable': rec, 'total_payable': sum(pay.mapped('residual')),
                'total_receivable': sum(rec.mapped('residual'))}


class ScoutTreasuryMoveDebt(models.Model):
    _inherit = 'scout.treasury.move'

    debt_id = fields.Many2one('scout.debt', string='Deuda / adeudo', index=True, ondelete='restrict',
                              help='Deuda anterior que se paga o cobra con este movimiento.')


class ScoutDebtPaymentWizard(models.TransientModel):
    _name = 'scout.debt.payment.wizard'
    _description = 'Registrar abono a deuda'

    debt_id = fields.Many2one('scout.debt', required=True)
    direction = fields.Selection(related='debt_id.direction')
    amount = fields.Monetary(string='Importe', required=True, currency_field='currency_id')
    journal_id = fields.Many2one('account.journal', string='Caja / banco', required=True,
                                 domain="[('type', 'in', ('cash', 'bank')), ('company_id', '=', company_id)]")
    date = fields.Date(string='Fecha', required=True, default=fields.Date.context_today)
    payment_method = fields.Selection([('cash', 'Efectivo'), ('transfer', 'Transferencia'), ('card', 'Tarjeta'),
                                       ('other', 'Otro')], string='Forma de pago', default='transfer')
    reference = fields.Char(string='Referencia')
    attachment = fields.Binary(string='Comprobante')
    attachment_name = fields.Char()
    company_id = fields.Many2one(related='debt_id.company_id')
    currency_id = fields.Many2one(related='debt_id.currency_id')

    def action_confirm(self):
        self.ensure_one()
        move = self.debt_id.action_register_payment(
            self.amount, self.journal_id, self.date, self.payment_method, self.reference,
            self.attachment, self.attachment_name)
        return {'type': 'ir.actions.act_window', 'res_model': 'scout.debt', 'res_id': self.debt_id.id,
                'view_mode': 'form', 'target': 'current'} if move else True
