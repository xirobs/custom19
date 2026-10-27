# -*- coding: utf-8 -*-
from datetime import date

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class RssPayment(models.Model):
    """Pago anual del seguro hecho por un grupo.

    Flujo: Borrador → Pago recibido (se emite recibo) → Remitido a la
    aseguradora (con folio/acuse). Todo el proceso queda en el chatter.
    """
    _name = 'rss.payment'
    _description = 'Pago de seguro por grupo'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'rss.branding.mixin']
    _order = 'date desc, id desc'

    name = fields.Char('Recibo', default=lambda self: _('Nuevo'), readonly=True,
                       copy=False, index=True)
    group_id = fields.Many2one('rss.scout.group', string='Grupo', required=True,
                               tracking=True, index=True)
    cluster_id = fields.Many2one(related='group_id.cluster_id', store=True,
                                 string='Agrupación')
    policy_id = fields.Many2one('rss.policy', string='Póliza', compute='_compute_policy',
                                store=True, readonly=False, tracking=True)
    company_id = fields.Many2one('res.company', string='Organización',
                                 compute='_compute_policy', store=True, readonly=False)
    currency_id = fields.Many2one(related='company_id.currency_id', string='Moneda')
    year = fields.Integer('Ejercicio', required=True, tracking=True,
                          default=lambda self: fields.Date.context_today(self).year,
                          aggregator=None)
    date = fields.Date('Fecha de pago', required=True, tracking=True,
                       default=fields.Date.context_today)
    payer_name = fields.Char('Entregó el pago', tracking=True,
                             help='Nombre de quien entrega el pago en nombre del grupo.')
    received_by = fields.Many2one('res.users', string='Recibió', tracking=True,
                                  default=lambda self: self.env.user)
    payment_method = fields.Selection([
        ('cash', 'Efectivo'),
        ('transfer', 'Transferencia'),
        ('deposit', 'Depósito bancario'),
        ('card', 'Tarjeta'),
        ('mercadopago', 'Mercado Pago'),
        ('other', 'Otro'),
    ], string='Forma de pago', default='transfer', required=True, tracking=True)
    reference = fields.Char('Referencia / folio bancario', tracking=True)

    member_ids = fields.Many2many('rss.insured', 'rss_payment_insured_rel', 'payment_id',
                                  'insured_id', string='Miembros cubiertos',
                                  domain="[('group_id', '=', group_id)]")
    member_count = fields.Integer('Miembros', compute='_compute_amounts', store=True)
    amount_per_member = fields.Monetary('Cuota por miembro', compute='_compute_policy',
                                        store=True, readonly=False)
    amount_expected = fields.Monetary('Importe esperado', compute='_compute_amounts',
                                      store=True)
    amount = fields.Monetary('Importe recibido', required=True, tracking=True)
    amount_difference = fields.Monetary('Diferencia', compute='_compute_amounts', store=True)

    state = fields.Selection([
        ('draft', 'Borrador'),
        ('confirmed', 'Pago recibido'),
        ('remitted', 'Remitido a aseguradora'),
        ('cancel', 'Cancelado'),
    ], string='Estatus', default='draft', required=True, tracking=True, copy=False)
    date_confirmed = fields.Datetime('Recibo emitido el', readonly=True, copy=False)
    date_remitted = fields.Date('Fecha de remisión', tracking=True, copy=False)
    insurer_folio = fields.Char('Folio / acuse de la aseguradora', tracking=True, copy=False)
    attachment_ids = fields.Many2many('ir.attachment', 'rss_payment_attachment_rel',
                                      'payment_id', 'attachment_id', string='Comprobantes')
    notes = fields.Text('Notas')

    _amount_positive = models.Constraint(
        'CHECK (amount >= 0)', 'El importe no puede ser negativo.',
    )

    @api.depends('group_id')
    def _compute_policy(self):
        for pay in self:
            if pay.group_id:
                pay.policy_id = pay.group_id.policy_id
                pay.company_id = pay.group_id.company_id
            elif not pay.company_id:
                pay.company_id = self.env.company
            pay.amount_per_member = pay.policy_id.premium_per_member

    @api.depends('member_ids', 'amount_per_member', 'amount')
    def _compute_amounts(self):
        for pay in self:
            pay.member_count = len(pay.member_ids)
            pay.amount_expected = pay.member_count * pay.amount_per_member
            pay.amount_difference = pay.amount - pay.amount_expected

    @api.constrains('year')
    def _check_year(self):
        for pay in self:
            if pay.year < 2000 or pay.year > 2100:
                raise ValidationError(_('El ejercicio %s no es válido.', pay.year))

    @api.onchange('group_id')
    def _onchange_group_load_members(self):
        if self.group_id and not self.member_ids:
            self.member_ids = self.group_id.member_ids.filtered(
                lambda m: m.state in ('active', 'draft'))

    @api.onchange('amount_expected')
    def _onchange_amount_expected(self):
        if not self.amount:
            self.amount = self.amount_expected

    @api.depends('name', 'group_id')
    def _compute_display_name(self):
        for pay in self:
            pay.display_name = '%s · %s' % (pay.name, pay.group_id.name or '')

    # ------------------------------------------------------------------
    # Flujo
    # ------------------------------------------------------------------
    def action_load_members(self):
        for pay in self:
            if pay.state != 'draft':
                raise UserError(_('Solo se pueden cargar miembros en borrador.'))
            pay.member_ids = pay.group_id.member_ids.filtered(
                lambda m: m.state in ('active', 'draft'))

    def action_confirm(self):
        for pay in self:
            if pay.state != 'draft':
                continue
            if pay.amount <= 0:
                raise UserError(_('Captura el importe recibido antes de emitir el recibo.'))
            if not pay.member_ids:
                raise UserError(_('Agrega los miembros cubiertos por el pago.'))
            vals = {'state': 'confirmed', 'date_confirmed': fields.Datetime.now()}
            if pay.name in (False, _('Nuevo'), 'Nuevo'):
                vals['name'] = self.env['ir.sequence'].with_company(pay.company_id).next_by_code(
                    'rss.payment', sequence_date=pay.date) or _('Nuevo')
            pay.write(vals)
            # Al pagar, los pre-registros pasan a asegurados.
            pending = pay.member_ids.filtered(lambda m: m.state == 'draft')
            if pending:
                pending.action_activate()
            pay._bita_grant_group_users()
            pay.message_post(body=_('Recibo %(name)s emitido por %(amount)s %(cur)s '
                                    '(%(count)s miembros).',
                                    name=pay.name, amount=pay.amount,
                                    cur=pay.currency_id.name, count=pay.member_count))
        return True

    def action_remit(self):
        for pay in self:
            if pay.state != 'confirmed':
                raise UserError(_('Solo se remiten pagos con recibo emitido.'))
            if not pay.insurer_folio:
                raise UserError(_('Captura el folio o acuse que entregó la aseguradora.'))
            pay.write({'state': 'remitted',
                       'date_remitted': pay.date_remitted or fields.Date.context_today(self)})

    def action_cancel(self):
        if any(p.state == 'remitted' for p in self):
            raise UserError(_('Un pago ya remitido a la aseguradora no se puede cancelar.'))
        self.write({'state': 'cancel'})
        self.env['bita.subscription'].sudo().search([
            ('rss_payment_id', 'in', self.ids), ('state', 'in', ('draft', 'active'))]).action_cancel()

    # ------------------------------------------------------------------
    # Acceso por pago del seguro (Suscripciones BitaScout)
    # ------------------------------------------------------------------
    def _bita_coverage_end(self):
        """Hasta cuándo da acceso el pago: fin de vigencia de la póliza o 31 de diciembre."""
        self.ensure_one()
        end_of_year = date(self.year, 12, 31)
        policy_end = self.policy_id.date_end
        if policy_end and policy_end.year == self.year:
            return policy_end
        return end_of_year

    def _bita_grant_group_users(self):
        """El pago del seguro del grupo da acceso a Red Seguro Scout a los usuarios del grupo."""
        platform = self.env.ref('red_seguro_scout.bita_platform_red_seguro_scout',
                                raise_if_not_found=False)
        if not platform:
            return
        Subscription = self.env['bita.subscription'].sudo()
        for pay in self:
            start = min(pay.date, fields.Date.context_today(self))
            end = pay._bita_coverage_end()
            if end < start:
                continue
            for user in pay.group_id.user_ids:
                previous = Subscription.search([
                    ('user_id', '=', user.id), ('platform_id', '=', platform.id)],
                    order='date_end desc', limit=1)
                Subscription.create({
                    'user_id': user.id,
                    'platform_id': platform.id,
                    'group_id': (previous.group_id or platform.default_group_id).id,
                    'period': 'custom',
                    'date_start': start,
                    'date_end': end,
                    'amount': 0.0,
                    'payment_method': 'group',
                    'payment_date': pay.date,
                    'payment_reference': pay.name,
                    'origin': _('Pago de seguro %(name)s · %(group)s', name=pay.name,
                                group=pay.group_id.name),
                    'rss_payment_id': pay.id,
                    'state': 'active',
                })

    def action_draft(self):
        self.filtered(lambda p: p.state == 'cancel').write({'state': 'draft'})

    def action_print_receipt(self):
        if any(p.state not in ('confirmed', 'remitted') for p in self):
            raise UserError(_('El recibo se imprime una vez registrado el pago.'))
        return self.env.ref('red_seguro_scout.action_report_recibo_pago').report_action(self)

    def unlink(self):
        if any(p.state in ('confirmed', 'remitted') for p in self):
            raise UserError(_('No se puede eliminar un pago con recibo emitido; cancélalo.'))
        return super().unlink()

    def _rss_brand(self):
        """El recibo sale con la identidad del grupo que paga."""
        self.ensure_one()
        return self._rss_brand_from(group=self.group_id, company=self.company_id)

    def _amount_in_words(self):
        """Importe con letra al estilo mexicano: (MIL PESOS 00/100 M.N.)."""
        self.ensure_one()
        try:
            from num2words import num2words
        except ImportError:
            return ''
        integer = int(self.amount)
        cents = int(round((self.amount - integer) * 100))
        words = num2words(integer, lang='es').upper()
        if self.currency_id.name == 'MXN':
            return '(%s PESOS %02d/100 M.N.)' % (words, cents)
        return '(%s %s %02d/100)' % (words, (self.currency_id.name or '').upper(), cents)
