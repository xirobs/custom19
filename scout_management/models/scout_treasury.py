# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare


class ScoutTreasuryCategory(models.Model):
    _name = 'scout.treasury.category'
    _description = 'Concepto de ingreso o egreso'
    _order = 'category_type, sequence, name'

    name = fields.Char(string='Concepto', required=True, translate=True)
    sequence = fields.Integer(default=10)
    category_type = fields.Selection([('income', 'Ingreso'), ('expense', 'Egreso')], string='Tipo',
                                     required=True)
    report_group = fields.Selection([
        ('sales', 'Ventas del día'), ('store', 'Tienda scout'), ('food', 'Venta de alimentos'),
        ('registration', 'Registro'), ('fees', 'Cuotas'), ('donation', 'Donativos y colaboraciones'),
        ('activity', 'Actividades y campamentos'), ('other', 'Otros'),
    ], string='Agrupar en reporte como', default='other')
    account_id = fields.Many2one('account.account', string='Cuenta contable', company_dependent=True,
                                 help='Cuenta de ingreso o gasto donde se registra el movimiento. '
                                      'Si se deja vacía se usa la primera cuenta de ingreso/gasto del plan.')
    active = fields.Boolean(default=True)

    def _get_account(self, company):
        self.ensure_one()
        account = self.with_company(company).account_id
        if account:
            return account
        account_type = 'income' if self.category_type == 'income' else 'expense'
        account = self.env['account.account'].search([
            *self.env['account.account']._check_company_domain(company),
            ('account_type', '=', account_type)], limit=1)
        if not account:
            raise UserError(_('Configura la cuenta contable del concepto "%s".', self.name))
        return account


class ScoutTreasuryMove(models.Model):
    """Captura simple de caja y bancos que genera su asiento contable.

    Ingresos se registran directo; egresos y traspasos requieren aprobación del jefe de grupo.
    Las cuentas por cobrar y por pagar se llevan con facturas de cliente y de proveedor de Contabilidad.
    """
    _name = 'scout.treasury.move'
    _description = 'Movimiento de tesorería'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'scout.approval.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(string='Folio', readonly=True, copy=False, default=lambda self: _('Nuevo'))
    date = fields.Date(string='Fecha', required=True, default=fields.Date.context_today, tracking=True, index=True)
    move_type = fields.Selection([
        ('income', 'Ingreso'), ('expense', 'Egreso / gasto'), ('transfer', 'Traspaso entre cuentas'),
    ], string='Tipo', required=True, default='income', tracking=True)
    journal_id = fields.Many2one('account.journal', string='Caja / banco', required=True, tracking=True,
                                 domain="[('type', 'in', ('cash', 'bank')), ('company_id', '=', company_id)]",
                                 help='Ingreso: dónde entra el dinero. Egreso y traspaso: de dónde sale.')
    dest_journal_id = fields.Many2one('account.journal', string='Caja / banco destino',
                                      domain="[('type', 'in', ('cash', 'bank')), ('company_id', '=', company_id), "
                                             "('id', '!=', journal_id)]")
    category_id = fields.Many2one('scout.treasury.category', string='Tipo de concepto', tracking=True,
                                  domain="[('category_type', '=', move_type)]")
    description = fields.Text(string='Concepto', required=True,
                              help='Describe con detalle de qué se trata el ingreso o egreso.')
    amount = fields.Monetary(string='Importe', required=True, currency_field='currency_id', tracking=True)
    payment_method = fields.Selection([
        ('cash', 'Efectivo'), ('transfer', 'Transferencia'), ('card', 'Tarjeta'), ('other', 'Otro'),
    ], string='Forma de pago', default='cash')
    reference = fields.Char(string='Referencia / No. de transferencia')
    member_id = fields.Many2one('res.partner', string='Miembro', domain="[('is_scout', '=', True)]")
    section_id = fields.Many2one('scout.section', string='Sección')
    partner_id = fields.Many2one('res.partner', string='Proveedor / quien paga')
    event_id = fields.Many2one('calendar.event', string='Actividad', domain="[('is_scout_event', '=', True)]")
    attachment = fields.Binary(string='Comprobante', attachment=True)
    attachment_name = fields.Char()
    state = fields.Selection([
        ('draft', 'Borrador'), ('to_approve', 'Por aprobar'), ('posted', 'Registrado'), ('cancel', 'Cancelado'),
    ], default='draft', required=True, tracking=True, string='Estado')
    account_move_id = fields.Many2one('account.move', string='Asiento contable', readonly=True, copy=False)
    approved_by_id = fields.Many2one('res.users', string='Autorizado por', readonly=True, copy=False)
    requested_by_id = fields.Many2one('res.users', string='Registrado por', readonly=True, copy=False,
                                      default=lambda self: self.env.user)
    store_sale_id = fields.Many2one('scout.store.sale', string='Venta de tienda', readonly=True, copy=False)
    signed_amount = fields.Monetary(compute='_compute_signed_amount', store=True,
                                    string='Importe (+/-)', currency_field='currency_id')
    company_id = fields.Many2one('res.company', string='Grupo', required=True,
                                 default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')

    @api.depends('amount', 'move_type')
    def _compute_signed_amount(self):
        for move in self:
            move.signed_amount = {'income': 1, 'expense': -1}.get(move.move_type, 0) * move.amount

    @api.constrains('amount', 'move_type', 'journal_id', 'dest_journal_id', 'category_id')
    def _check_move(self):
        for move in self:
            if float_compare(move.amount, 0.0, precision_rounding=move.currency_id.rounding or 0.01) <= 0:
                raise ValidationError(_('El importe debe ser mayor a cero.'))
            if move.move_type == 'transfer':
                if not move.dest_journal_id or move.dest_journal_id == move.journal_id:
                    raise ValidationError(_('Un traspaso necesita una caja o banco destino distinto.'))
            elif not move.category_id:
                raise ValidationError(_('Indica el concepto del ingreso o egreso.'))

    @api.onchange('move_type')
    def _onchange_move_type(self):
        if self.category_id and self.category_id.category_type != self.move_type:
            self.category_id = False
        if self.move_type != 'transfer':
            self.dest_journal_id = False

    @api.onchange('journal_id')
    def _onchange_journal_id(self):
        if self.journal_id:
            self.payment_method = 'cash' if self.journal_id.type == 'cash' else 'transfer'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('scout.treasury.move') or _('Nuevo')
        return super().create(vals_list)

    def write(self, vals):
        protected = {'amount', 'journal_id', 'dest_journal_id', 'move_type', 'date', 'category_id'}
        if protected & set(vals) and any(m.state in ('posted', 'to_approve') for m in self):
            raise UserError(_('No se puede modificar un movimiento registrado o en aprobación. '
                              'Cancélalo y vuelve a capturarlo.'))
        return super().write(vals)

    def unlink(self):
        if any(m.state == 'posted' for m in self):
            raise UserError(_('No se pueden eliminar movimientos registrados; cancélalos.'))
        return super().unlink()

    # ------------------------------------------------------------------
    def _needs_approval(self):
        self.ensure_one()
        return self.move_type in ('expense', 'transfer') and self.company_id.scout_expense_approval

    def action_post(self):
        """Ingresos: se registran directo. Egresos/traspasos: se envían al jefe de grupo."""
        for move in self.filtered(lambda m: m.state == 'draft'):
            if move._needs_approval() and not self._scout_is_manager():
                move.state = 'to_approve'
                move._scout_request_approval(
                    _('Aprobar %(type)s %(folio)s: %(desc)s por %(amount)s',
                      type=dict(move._fields['move_type'].selection)[move.move_type].lower(),
                      folio=move.name, desc=move.description, amount=move.amount))
            else:
                move._post_accounting()
        return True

    def action_approve(self):
        self._scout_check_manager(_('aprobar egresos y traspasos'))
        for move in self.filtered(lambda m: m.state == 'to_approve'):
            move._post_accounting()
            move.approved_by_id = self.env.user
            move._scout_close_approval_activities(_('Aprobado'))

    def action_reject(self):
        self._scout_check_manager(_('rechazar egresos'))
        self.filtered(lambda m: m.state == 'to_approve').write({'state': 'draft'})
        self._scout_close_approval_activities(_('Rechazado'))
        for move in self:
            move.message_post(body=_('Rechazado por %s; corrige y vuelve a enviar.', self.env.user.name))

    def _liquidity_account(self, journal):
        account = journal.default_account_id
        if not account:
            raise UserError(_('La caja o banco %s no tiene cuenta contable configurada.', journal.name))
        return account

    def _prepare_account_move_vals(self):
        self.ensure_one()
        company = self.company_id
        src = self._liquidity_account(self.journal_id)
        partner = self.partner_id or self.member_id.scout_guardian_id or self.member_id
        label = f'{self.name} - {(self.description or "").splitlines()[0][:90] if self.description else ""}'
        if self.move_type == 'income':
            debit_account, credit_account = src, self.category_id._get_account(company)
        elif self.move_type == 'expense':
            debit_account, credit_account = self.category_id._get_account(company), src
        else:
            debit_account, credit_account = self._liquidity_account(self.dest_journal_id), src
        return {
            'move_type': 'entry',
            'journal_id': self.journal_id.id,
            'date': self.date,
            'ref': label,
            'scout_member_id': self.member_id.id,
            'scout_treasury_move_id': self.id,
            'line_ids': [
                fields.Command.create({'account_id': debit_account.id, 'name': label, 'debit': self.amount,
                                       'credit': 0.0, 'partner_id': partner.id}),
                fields.Command.create({'account_id': credit_account.id, 'name': label, 'debit': 0.0,
                                       'credit': self.amount, 'partner_id': partner.id}),
            ],
        }

    def _post_accounting(self):
        for move in self:
            account_move = self.env['account.move'].sudo().with_company(move.company_id).create(
                move._prepare_account_move_vals())
            account_move.with_context(scout_approved=True).action_post()
            move.write({'account_move_id': account_move.id, 'state': 'posted'})

    def action_cancel(self):
        for move in self:
            if move.account_move_id and move.account_move_id.state == 'posted':
                move.account_move_id.sudo()._reverse_moves(
                    default_values_list=[{'date': fields.Date.context_today(move),
                                          'ref': _('Cancelación de %s', move.name)}], cancel=True)
            move.state = 'cancel'
        self._scout_close_approval_activities(_('Cancelado'))

    def action_draft(self):
        self.filtered(lambda m: m.state == 'cancel').write({'state': 'draft', 'account_move_id': False})

    def action_open_account_move(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'res_id': self.account_move_id.id,
                'view_mode': 'form'}
