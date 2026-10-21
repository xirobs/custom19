# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError

APPROVAL_STATES = [
    ('none', 'No requiere'), ('to_approve', 'Por aprobar'), ('approved', 'Aprobado'), ('rejected', 'Rechazado'),
]


class ResCompany(models.Model):
    _inherit = 'res.company'

    scout_emblem = fields.Binary(string='Flor de lis / emblema (reportes)', attachment=True,
                                 help='Imagen que aparece a la derecha del encabezado de los PDF del grupo.')
    scout_expense_approval = fields.Boolean(
        string='Gastos y pagos requieren aprobación del jefe de grupo', default=True,
        help='Facturas de proveedor, pagos salientes, egresos y traspasos de tesorería quedan '
             '"por aprobar" hasta que el jefe de grupo los autoriza.')


class ResCompanyTransfer(models.Model):
    _inherit = 'res.company'

    scout_bank_name = fields.Char(string='Banco', help='Ej. Banorte')
    scout_bank_clabe = fields.Char(string='CLABE', help='CLABE interbancaria de 18 dígitos para transferencias (SPEI).')
    scout_bank_holder = fields.Char(string='Nombre del titular')
    scout_bank_phone = fields.Char(string='Teléfono para enviar el comprobante')
    scout_bank_concept = fields.Char(string='Concepto / comentario', default='Nombre del niño y sección',
                                     help='Qué deben escribir en el concepto de la transferencia.')

    def _scout_transfer_info(self, member_name=None, section=None):
        """Datos para pagar por transferencia (o False si no están configurados)."""
        company = self[:1].sudo()
        if not company or not company.scout_bank_clabe:
            return False
        section_name = section.display_name if section else ''
        concept = ' - '.join(p for p in ((member_name or '').strip(), section_name) if p) or company.scout_bank_concept
        lines = [_('Banco: %s', company.scout_bank_name or '-'), _('CLABE: %s', company.scout_bank_clabe),
                 _('Nombre: %s', company.scout_bank_holder or company.name)]
        if company.scout_bank_phone:
            lines.append(_('Teléfono: %s', company.scout_bank_phone))
        lines.append(_('Comentario: %s', concept))
        return {
            'bank': company.scout_bank_name or '', 'clabe': company.scout_bank_clabe,
            'holder': company.scout_bank_holder or company.name, 'phone': company.scout_bank_phone or '',
            'concept': concept, 'concept_hint': company.scout_bank_concept or '',
            'text': '\n'.join(lines),
        }


class ResCompanyBranding(models.Model):
    _inherit = 'res.company'

    @api.model
    def _scout_load_branding(self):
        from odoo.addons.scout_management.hooks import scout_load_branding
        scout_load_branding(self.env)


class AccountMove(models.Model):
    _inherit = 'account.move'

    # Atajos al mixin de aprobaciones (sin heredar el modelo abstracto)
    def _scout_is_manager(self):
        return self.env['scout.approval.mixin']._scout_is_manager()

    def _scout_check_manager(self, label):
        return self.env['scout.approval.mixin']._scout_check_manager(label)

    def _scout_request_approval(self, summary, note=''):
        return self.env['scout.approval.mixin']._scout_request_approval_for(self, summary, note)

    def _scout_close_approval_activities(self, feedback):
        return self.env['scout.approval.mixin']._scout_close_approval_for(self, feedback)


    scout_member_id = fields.Many2one('res.partner', string='Miembro scout', index=True,
                                      domain="[('is_scout', '=', True)]",
                                      help='Miembro al que corresponde el cargo (registro, cuota, campamento…).')
    scout_section_id = fields.Many2one(related='scout_member_id.scout_section_id', store=True,
                                       string='Sección')
    scout_approval_state = fields.Selection(APPROVAL_STATES, string='Aprobación', default='none',
                                            copy=False, tracking=True)
    scout_needs_approval = fields.Boolean(compute='_compute_scout_needs_approval')
    scout_treasury_move_id = fields.Many2one('scout.treasury.move', string='Movimiento de tesorería',
                                             readonly=True, copy=False, index='btree_not_null')

    @api.depends('move_type', 'company_id.scout_expense_approval', 'scout_approval_state')
    def _compute_scout_needs_approval(self):
        for move in self:
            move.scout_needs_approval = bool(
                move.move_type in ('in_invoice', 'in_refund')
                and move.company_id.scout_expense_approval
                and move.scout_approval_state != 'approved')

    def action_post(self):
        if not self.env.context.get('scout_approved') and not self._scout_is_manager():
            pending = self.filtered('scout_needs_approval')
            if pending:
                raise UserError(_(
                    'Las facturas de proveedor necesitan la aprobación del jefe de grupo antes de confirmarse. '
                    'Usa "Solicitar aprobación" en: %s', ', '.join(pending.mapped('display_name'))))
        return super().action_post()

    def action_scout_request_approval(self):
        for move in self.filtered(lambda m: m.scout_needs_approval and m.state == 'draft'):
            move.scout_approval_state = 'to_approve'
            move._scout_request_approval(
                _('Aprobar gasto %(ref)s por %(amount)s', ref=move.ref or move.partner_id.name or '',
                  amount=move.amount_total))

    def action_scout_approve(self):
        self._scout_check_manager(_('aprobar gastos'))
        self.write({'scout_approval_state': 'approved'})
        self._scout_close_approval_activities(_('Aprobado'))
        self.filtered(lambda m: m.state == 'draft').with_context(scout_approved=True).action_post()

    def action_scout_reject(self):
        self._scout_check_manager(_('rechazar gastos'))
        self.write({'scout_approval_state': 'rejected'})
        self._scout_close_approval_activities(_('Rechazado'))


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    scout_member_id = fields.Many2one(related='move_id.scout_member_id', store=True, index='btree_not_null',
                                      string='Miembro scout')
    scout_move_payment_state = fields.Selection(related='move_id.payment_state', string='Estado de pago')
    scout_move_residual = fields.Monetary(related='move_id.amount_residual', string='Saldo del cargo',
                                          currency_field='currency_id')


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    # Atajos al mixin de aprobaciones (sin heredar el modelo abstracto)
    def _scout_is_manager(self):
        return self.env['scout.approval.mixin']._scout_is_manager()

    def _scout_check_manager(self, label):
        return self.env['scout.approval.mixin']._scout_check_manager(label)

    def _scout_request_approval(self, summary, note=''):
        return self.env['scout.approval.mixin']._scout_request_approval_for(self, summary, note)

    def _scout_close_approval_activities(self, feedback):
        return self.env['scout.approval.mixin']._scout_close_approval_for(self, feedback)


    scout_approval_state = fields.Selection(APPROVAL_STATES, string='Aprobación', default='none',
                                            copy=False, tracking=True)
    scout_member_id = fields.Many2one('res.partner', string='Miembro scout', domain="[('is_scout', '=', True)]")

    def _scout_payment_needs_approval(self):
        self.ensure_one()
        if self.payment_type != 'outbound' or not self.company_id.scout_expense_approval:
            return False
        if self.scout_approval_state == 'approved':
            return False
        # El pago de facturas de proveedor ya aprobadas no requiere una segunda aprobación.
        bills = self.invoice_ids | self.reconciled_bill_ids
        return not bills or any(b.scout_approval_state != 'approved' for b in bills)

    def action_post(self):
        if not self.env.context.get('scout_approved') and not self._scout_is_manager():
            pending = self.filtered(lambda p: p._scout_payment_needs_approval())
            if pending:
                raise UserError(_('Los pagos salientes necesitan la aprobación del jefe de grupo: %s',
                                  ', '.join(pending.mapped('display_name'))))
        return super().action_post()

    def action_scout_request_approval(self):
        for payment in self.filtered(lambda p: p.state == 'draft' and p._scout_payment_needs_approval()):
            payment.scout_approval_state = 'to_approve'
            payment._scout_request_approval(_('Aprobar pago a %(partner)s por %(amount)s',
                                              partner=payment.partner_id.name or '', amount=payment.amount))

    def action_scout_approve(self):
        self._scout_check_manager(_('aprobar pagos'))
        self.write({'scout_approval_state': 'approved'})
        self._scout_close_approval_activities(_('Aprobado'))
        self.filtered(lambda p: p.state == 'draft').with_context(scout_approved=True).action_post()


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    def _post_payments(self, to_process, edit_mode=False):
        moves = self.line_ids.move_id
        bills = moves.filtered(lambda m: m.move_type in ('in_invoice', 'in_refund'))
        if bills and all(b.scout_approval_state == 'approved' for b in bills):
            self = self.with_context(scout_approved=True)
        return super()._post_payments(to_process, edit_mode=edit_mode)
