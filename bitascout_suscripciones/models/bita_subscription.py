# -*- coding: utf-8 -*-
import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class BitaSubscription(models.Model):
    _name = 'bita.subscription'
    _description = 'Suscripción a plataforma'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_end desc, id desc'

    name = fields.Char('Folio', default=lambda self: _('Nueva'), readonly=True, copy=False)
    user_id = fields.Many2one('res.users', string='Usuario', required=True, tracking=True,
                              index=True, ondelete='cascade',
                              context={'active_test': False})
    partner_id = fields.Many2one(related='user_id.partner_id', string='Contacto', store=True)
    platform_id = fields.Many2one('bita.platform', string='Plataforma', required=True,
                                  tracking=True, index=True, ondelete='restrict')
    platform_group_ids = fields.Many2many(related='platform_id.group_ids')
    group_id = fields.Many2one(
        'res.groups', string='Rol', tracking=True,
        domain="[('id', 'in', platform_group_ids)]",
        help='Permiso que se otorga mientras la suscripción esté vigente. '
             'Vacío: el rol por defecto de la plataforma.')
    company_id = fields.Many2one(related='platform_id.company_id', store=True, string='Empresa')
    currency_id = fields.Many2one(related='platform_id.currency_id', string='Moneda')
    period = fields.Selection([
        ('month', 'Mensual'),
        ('year', 'Anual'),
        ('custom', 'Personalizado'),
    ], string='Periodo', default='month', required=True, tracking=True)
    date_start = fields.Date('Desde', required=True, tracking=True,
                             default=fields.Date.context_today)
    date_end = fields.Date('Hasta', compute='_compute_date_end', store=True, readonly=False, precompute=True,
                           required=True, tracking=True)
    amount = fields.Monetary('Importe pagado', tracking=True)
    payment_date = fields.Date('Fecha de pago', tracking=True)
    payment_method = fields.Selection([
        ('cash', 'Efectivo'),
        ('transfer', 'Transferencia'),
        ('card', 'Tarjeta'),
        ('mercadopago', 'Mercado Pago'),
        ('group', 'Pagado por el grupo'),
        ('trial', 'Periodo de prueba'),
        ('courtesy', 'Cortesía'),
        ('other', 'Otro'),
    ], string='Forma de pago', default='transfer', tracking=True)
    payment_reference = fields.Char('Referencia de pago', tracking=True)
    origin = fields.Char('Origen', help='Documento que generó la suscripción.')
    is_trial = fields.Boolean('Prueba', compute='_compute_is_trial', store=True)
    note = fields.Text('Notas')
    state = fields.Selection([
        ('draft', 'Pendiente de pago'),
        ('active', 'Vigente'),
        ('expired', 'Vencida'),
        ('cancel', 'Cancelada'),
    ], string='Estatus', default='draft', required=True, tracking=True, index=True, copy=False)
    is_current = fields.Boolean('Da acceso hoy', compute='_compute_is_current',
                                search='_search_is_current')
    days_left = fields.Integer('Días restantes', compute='_compute_is_current')
    reminder_sent = fields.Boolean(copy=False)

    @api.depends('date_start', 'period')
    def _compute_date_end(self):
        for sub in self:
            if not sub.date_start or sub.period == 'custom':
                if not sub.date_end:
                    sub.date_end = sub.date_start
                continue
            delta = relativedelta(months=1) if sub.period == 'month' else relativedelta(years=1)
            sub.date_end = sub.date_start + delta - relativedelta(days=1)

    @api.depends('payment_method')
    def _compute_is_trial(self):
        for sub in self:
            sub.is_trial = sub.payment_method == 'trial'

    @api.depends('state', 'date_start', 'date_end', 'platform_id.grace_days')
    def _compute_is_current(self):
        today = fields.Date.context_today(self)
        for sub in self:
            limit = (sub.date_end + relativedelta(days=sub.platform_id.grace_days)
                     if sub.date_end else False)
            sub.is_current = bool(sub.state == 'active' and sub.date_start
                                  and sub.date_start <= today and limit and today <= limit)
            sub.days_left = (sub.date_end - today).days if sub.date_end else 0

    def _search_is_current(self, operator, value):
        if operator not in ('=', '!='):
            return NotImplemented
        today = fields.Date.context_today(self)
        ids = []
        for sub in self.search([('state', '=', 'active'), ('date_start', '<=', today)]):
            if sub.date_end + relativedelta(days=sub.platform_id.grace_days) >= today:
                ids.append(sub.id)
        positive = (operator == '=') == bool(value)
        return [('id', 'in' if positive else 'not in', ids)]

    @api.depends('name', 'platform_id', 'user_id')
    def _compute_display_name(self):
        for sub in self:
            sub.display_name = '%s · %s · %s' % (sub.name, sub.platform_id.name or '',
                                                sub.user_id.name or '')

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for sub in self:
            if sub.date_start and sub.date_end and sub.date_end < sub.date_start:
                raise ValidationError(_('La vigencia final no puede ser anterior a la inicial.'))

    @api.constrains('group_id', 'platform_id')
    def _check_group(self):
        for sub in self:
            if sub.group_id and sub.group_id not in sub.platform_id.group_ids:
                raise ValidationError(_('El rol %(role)s no pertenece a %(platform)s.',
                                        role=sub.group_id.name, platform=sub.platform_id.name))

    @api.onchange('platform_id', 'period')
    def _onchange_price(self):
        if self.platform_id and not self.amount:
            if self.period == 'month':
                self.amount = self.platform_id.price_month
            elif self.period == 'year':
                self.amount = self.platform_id.price_year
        if self.platform_id and not self.group_id and not self.user_id.share:
            self.group_id = self.platform_id.default_group_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nueva')) in (_('Nueva'), 'Nueva', False):
                vals['name'] = self.env['ir.sequence'].next_by_code('bita.subscription') or _('Nueva')
        subs = super().create(vals_list)
        subs.filtered(lambda s: s.state != 'draft').user_id._bita_sync_access()
        return subs

    def write(self, vals):
        users_before = self.user_id
        res = super().write(vals)
        if {'state', 'date_start', 'date_end', 'group_id', 'user_id', 'platform_id'} & set(vals):
            (users_before | self.user_id)._bita_sync_access()
        return res

    def unlink(self):
        users = self.user_id
        if any(s.state == 'active' and s.amount for s in self):
            raise UserError(_('No se puede eliminar una suscripción pagada; cancélala.'))
        res = super().unlink()
        users.exists()._bita_sync_access()
        return res

    # ------------------------------------------------------------------
    # Flujo
    # ------------------------------------------------------------------
    def action_activate(self):
        for sub in self:
            vals = {'state': 'active', 'reminder_sent': False}
            if not sub.payment_date and sub.payment_method not in ('courtesy', 'trial'):
                vals['payment_date'] = fields.Date.context_today(self)
            if not sub.group_id and not sub.user_id.share:
                vals['group_id'] = sub.platform_id.default_group_id.id
            sub.write(vals)
            sub.message_post(body=_('Suscripción vigente del %(start)s al %(end)s.',
                                    start=sub.date_start.strftime('%d/%m/%Y'),
                                    end=sub.date_end.strftime('%d/%m/%Y')))
        return True

    def action_cancel(self):
        self.write({'state': 'cancel'})

    def action_draft(self):
        self.write({'state': 'draft'})

    def action_renew(self):
        """Crea la siguiente suscripción a partir del día siguiente al vencimiento."""
        self.ensure_one()
        start = max(self.date_end + relativedelta(days=1), fields.Date.context_today(self))
        new = self.copy({
            'date_start': start,
            'period': 'month' if self.period == 'custom' or self.is_trial else self.period,
            'payment_date': False,
            'payment_reference': False,
            'payment_method': 'transfer' if self.is_trial else self.payment_method,
            'amount': (self.platform_id.price_month if self.is_trial else self.amount),
            'origin': self.name,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'bita.subscription',
            'res_id': new.id,
            'view_mode': 'form',
        }

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------
    @api.model
    def _cron_check_subscriptions(self):
        today = fields.Date.context_today(self)
        actives = self.search([('state', '=', 'active')])
        expired = actives.filtered(
            lambda s: s.date_end + relativedelta(days=s.platform_id.grace_days) < today)
        if expired:
            expired.write({'state': 'expired'})
            for sub in expired:
                sub.message_post(body=_('Suscripción vencida: se retiró el acceso a %s.',
                                        sub.platform_id.name))
        template = self.env.ref('bitascout_suscripciones.mail_template_bita_expiring',
                                raise_if_not_found=False)
        for sub in (actives - expired).filtered(lambda s: not s.reminder_sent):
            if sub.date_end <= today + relativedelta(days=sub.platform_id.notice_days):
                renewed = self.search_count([
                    ('user_id', '=', sub.user_id.id), ('platform_id', '=', sub.platform_id.id),
                    ('state', 'in', ('active', 'draft')), ('date_start', '>', sub.date_end),
                ])
                if not renewed and template and sub.user_id.email:
                    template.send_mail(sub.id)
                sub.reminder_sent = True
        platforms = self.env['bita.platform'].search([('enforce', '=', True)])
        platforms._sync_all_users()
        _logger.info('Suscripciones BitaScout: %s vencidas, acceso sincronizado.', len(expired))
        return True
