# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

SALE_TYPES = [
    ('store', 'Tienda scout'),
    ('food', 'Alimentos'),
    ('event', 'Campamento / evento'),
    ('donation', 'Colaboración solicitada'),
]
# Concepto de tesorería que recibe el ingreso de cada tipo de venta.
CATEGORY_BY_TYPE = {
    'store': 'scout_management.category_store',
    'food': 'scout_management.category_food',
    'event': 'scout_management.category_camp_income',
    'donation': 'scout_management.category_donation',
}


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    scout_store_ok = fields.Boolean(string='Se vende en la tienda scout')
    scout_sale_type = fields.Selection(SALE_TYPES, string='Tipo de venta scout', default='store')
    scout_track_qty = fields.Boolean(string='Controlar existencias', default=True,
                                     help='Descuenta piezas en cada venta (desactívalo para alimentos preparados o cuotas).')
    scout_qty_available = fields.Float(string='Existencia', compute='_compute_scout_qty', digits='Product Unit')
    scout_min_qty = fields.Float(string='Existencia mínima', help='Avisa cuando quedan pocas piezas.')
    scout_low_stock = fields.Boolean(compute='_compute_scout_qty', string='Por agotarse')
    scout_stock_move_ids = fields.One2many('scout.store.stock.move', 'product_tmpl_id', string='Movimientos de tienda')

    def _compute_scout_qty(self):
        data = dict(self.env['scout.store.stock.move'].sudo()._read_group(
            [('product_tmpl_id', 'in', self.ids)], ['product_tmpl_id'], ['quantity:sum']))
        for product in self:
            product.scout_qty_available = data.get(product, 0.0)
            product.scout_low_stock = bool(product.scout_track_qty and product.scout_store_ok
                                           and product.scout_qty_available <= product.scout_min_qty)

    def action_scout_stock_in(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Entrada de mercancía'),
            'res_model': 'scout.store.stock.move',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_product_tmpl_id': self.id, 'default_move_kind': 'in',
                        'default_unit_cost': self.standard_price},
        }


class ScoutStoreStockMove(models.Model):
    """Entradas, ajustes y salidas por venta de la tienda scout (existencia = suma de cantidades)."""
    _name = 'scout.store.stock.move'
    _description = 'Movimiento de existencias de la tienda scout'
    _order = 'date desc, id desc'

    date = fields.Date(string='Fecha', required=True, default=fields.Date.context_today)
    product_tmpl_id = fields.Many2one('product.template', string='Producto', required=True, ondelete='cascade',
                                      domain="[('scout_store_ok', '=', True)]", index=True)
    move_kind = fields.Selection([('initial', 'Inventario inicial'), ('in', 'Entrada / compra'),
                                  ('adjust', 'Ajuste'), ('sale', 'Venta'), ('cancel', 'Cancelación de venta')],
                                 string='Tipo', required=True, default='in')
    quantity = fields.Float(string='Cantidad (+ entra / - sale)', required=True, digits='Product Unit')
    unit_cost = fields.Float(string='Costo unitario', digits='Product Price')
    total_cost = fields.Float(string='Costo total', compute='_compute_total_cost', store=True, digits='Product Price')
    sale_line_id = fields.Many2one('scout.store.sale.line', string='Venta', ondelete='set null', readonly=True)
    note = fields.Char(string='Nota')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    @api.depends('quantity', 'unit_cost')
    def _compute_total_cost(self):
        for move in self:
            move.total_cost = abs(move.quantity) * move.unit_cost

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        for move in moves.filtered(lambda m: m.move_kind in ('in', 'initial') and m.unit_cost):
            move.product_tmpl_id.sudo().standard_price = move.unit_cost
        return moves


class ScoutStoreSale(models.Model):
    """Venta de tienda scout, alimentos o campamento/evento con recibo."""
    _name = 'scout.store.sale'
    _description = 'Venta scout'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'portal.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(string='Folio', readonly=True, copy=False, default=lambda self: _('Nuevo'))
    date = fields.Date(string='Fecha', required=True, default=fields.Date.context_today, tracking=True, index=True)
    sale_type = fields.Selection(SALE_TYPES, string='Tipo de venta', required=True, default='store', tracking=True)
    partner_id = fields.Many2one('res.partner', string='Quién compra', required=True, tracking=True,
                                 help='Padre, madre, miembro o persona externa.')
    member_id = fields.Many2one('res.partner', string='Miembro', domain="[('is_scout', '=', True)]",
                                help='Miembro al que corresponde la compra (para su estado de cuenta).')
    event_id = fields.Many2one('calendar.event', string='Campamento / evento', domain="[('is_scout_event', '=', True)]")
    line_ids = fields.One2many('scout.store.sale.line', 'sale_id', string='Productos', copy=True)
    amount_total = fields.Monetary(string='Total', compute='_compute_amount_total', store=True,
                                   currency_field='currency_id')
    payment_mode = fields.Selection([('paid', 'Pagó en el momento'), ('online', 'Paga en línea (QR / liga)'),
                                     ('credit', 'Queda a deber (cargo)')],
                                    string='Pago', required=True, default='paid', tracking=True)
    journal_id = fields.Many2one('account.journal', string='Caja / banco',
                                 domain="[('type', 'in', ('cash', 'bank')), ('company_id', '=', company_id)]")
    payment_method = fields.Selection([('cash', 'Efectivo'), ('transfer', 'Transferencia'), ('card', 'Tarjeta')],
                                      string='Forma de pago', default='cash')
    payment_reference = fields.Char(string='Referencia de pago')
    state = fields.Selection([('draft', 'Borrador'), ('done', 'Vendida'), ('cancel', 'Cancelada')],
                             default='draft', required=True, tracking=True, string='Estado')
    treasury_move_id = fields.Many2one('scout.treasury.move', string='Ingreso en caja', readonly=True, copy=False)
    invoice_id = fields.Many2one('account.move', string='Cargo (por cobrar)', readonly=True, copy=False)
    paid_state = fields.Selection([('paid', 'Pagada'), ('partial', 'Pago parcial'), ('not_paid', 'Pendiente')],
                                  compute='_compute_paid_state', store=True, string='¿Pagó?')
    received_by_id = fields.Many2one('res.users', string='Recibió', default=lambda self: self.env.user,
                                     help='Quién cobró o registró la venta (tesorería o dirigente en la junta).')
    receipt_sent = fields.Boolean(string='Recibo enviado', copy=False, tracking=True)
    note = fields.Text(string='Notas')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')

    @api.depends('line_ids.subtotal')
    def _compute_amount_total(self):
        for sale in self:
            sale.amount_total = sum(sale.line_ids.mapped('subtotal'))

    @api.depends('state', 'payment_mode', 'invoice_id.payment_state', 'treasury_move_id.state')
    def _compute_paid_state(self):
        for sale in self:
            if sale.payment_mode == 'paid':
                sale.paid_state = 'paid' if sale.state == 'done' else 'not_paid'
            else:
                ps = sale.invoice_id.payment_state
                sale.paid_state = 'paid' if ps in ('paid', 'in_payment') else ('partial' if ps == 'partial' else 'not_paid')

    @api.onchange('member_id')
    def _onchange_member_id(self):
        if self.member_id and not self.partner_id:
            self.partner_id = self.member_id.scout_guardian_id or self.member_id

    @api.onchange('sale_type')
    def _onchange_sale_type(self):
        if self.sale_type:
            self.line_ids.filtered(lambda l: l.product_id.scout_sale_type != self.sale_type)

    @api.constrains('payment_mode', 'journal_id')
    def _check_journal(self):
        for sale in self:
            if sale.state == 'done' and sale.payment_mode == 'paid' and not sale.journal_id:
                raise ValidationError(_('Indica la caja o banco donde entró el dinero.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('scout.store.sale') or _('Nuevo')
        return super().create(vals_list)

    def _compute_access_url(self):
        super()._compute_access_url()
        for sale in self:
            sale.access_url = f'/scout/recibo/{sale.id}'

    def _get_report_base_filename(self):
        self.ensure_one()
        return f'Recibo {self.name}'

    def unlink(self):
        if any(s.state == 'done' for s in self):
            raise UserError(_('No se puede eliminar una venta realizada; cancélala.'))
        return super().unlink()

    # ------------------------------------------------------------------
    def action_confirm(self):
        for sale in self.filtered(lambda s: s.state == 'draft'):
            if not sale.line_ids:
                raise UserError(_('Agrega al menos un producto a la venta.'))
            if sale.payment_mode == 'paid' and not sale.journal_id:
                sale.journal_id = self.env['account.journal'].search(
                    [('type', '=', 'cash' if sale.payment_method == 'cash' else 'bank'),
                     ('company_id', '=', sale.company_id.id)], limit=1)
                if not sale.journal_id:
                    raise UserError(_('Indica la caja o banco donde entró el dinero.'))
            sale._check_stock()
            Stock = self.env['scout.store.stock.move'].sudo()
            for line in sale.line_ids.filtered(lambda l: l.product_id.scout_track_qty):
                Stock.create({'date': sale.date, 'product_tmpl_id': line.product_id.product_tmpl_id.id,
                              'move_kind': 'sale', 'quantity': -line.quantity,
                              'unit_cost': line.product_id.standard_price, 'sale_line_id': line.id,
                              'note': sale.name})
            if sale.payment_mode == 'paid':
                sale._create_treasury_income()
            else:
                sale._create_charge()
            sale.state = 'done'
            sale._attach_receipt()
        if len(self) == 1 and self.payment_mode == 'online' and self.invoice_id.scout_can_pay_online:
            return self.action_scout_payment_link()
        return True

    def action_scout_payment_link(self):
        """Muestra el QR y la liga para que el comprador pague en línea el cargo de la venta."""
        self.ensure_one()
        if not self.invoice_id or not self.invoice_id.scout_can_pay_online:
            raise UserError(_('Esta venta no tiene saldo pendiente por cobrar.'))
        return self.invoice_id.action_scout_payment_link()

    def _scout_receipt_pay_qr(self):
        """(liga, QR en data URI) para pagar en línea lo pendiente de la venta; vacío si no aplica."""
        self.ensure_one()
        invoice = self.invoice_id
        if self.state != 'done' or not invoice.scout_can_pay_online:
            return False
        try:
            link = invoice._scout_payment_link()
        except Exception:  # noqa: BLE001 - sin pasarela configurada no se muestra
            return False
        from .scout_online_payment import scout_qr_data_uri
        return {'link': link, 'qr': scout_qr_data_uri(link)}

    def _check_stock(self):
        self.ensure_one()
        needed = {}
        for line in self.line_ids.filtered(lambda l: l.product_id.scout_track_qty):
            tmpl = line.product_id.product_tmpl_id
            needed[tmpl] = needed.get(tmpl, 0.0) + line.quantity
        short = [f'{t.name} (hay {t.scout_qty_available:g}, se piden {q:g})' for t, q in needed.items()
                 if float_compare(q, t.scout_qty_available, precision_digits=3) > 0]
        if short:
            raise UserError(_('No hay existencia suficiente: %s', '; '.join(short)))

    def _sale_category(self):
        xmlid = CATEGORY_BY_TYPE.get(self.sale_type)
        return self.env.ref(xmlid, raise_if_not_found=False) if xmlid else False

    def _sale_description(self):
        detail = ', '.join(f'{l.quantity:g} x {l.product_id.name}' for l in self.line_ids)
        return _('Venta %(folio)s (%(type)s): %(detail)s', folio=self.name,
                 type=dict(self._fields['sale_type'].selection)[self.sale_type], detail=detail)

    def _create_treasury_income(self):
        self.ensure_one()
        category = self._sale_category()
        move = self.env['scout.treasury.move'].sudo().create({
            'move_type': 'income', 'date': self.date, 'journal_id': self.journal_id.id,
            'category_id': category.id if category else False, 'amount': self.amount_total,
            'description': self._sale_description(), 'payment_method': self.payment_method,
            'reference': self.payment_reference, 'member_id': self.member_id.id,
            'partner_id': self.partner_id.id, 'event_id': self.event_id.id, 'store_sale_id': self.id,
            'company_id': self.company_id.id,
        })
        move.action_post()
        self.treasury_move_id = move

    def _create_charge(self):
        """Venta a crédito: cargo (factura de cliente) que aparece en el estado de cuenta del miembro."""
        self.ensure_one()
        invoice = self.env['account.move'].sudo().with_company(self.company_id).create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_id.id,
            'scout_member_id': self.member_id.id,
            'invoice_date': self.date,
            'invoice_origin': self.name,
            'invoice_line_ids': [fields.Command.create({
                'product_id': line.product_id.id, 'name': line.product_id.display_name,
                'quantity': line.quantity, 'price_unit': line.price_unit, 'tax_ids': [fields.Command.clear()],
            }) for line in self.line_ids],
        })
        invoice.action_post()
        self.invoice_id = invoice

    def _attach_receipt(self):
        """Guarda el PDF del recibo como evidencia en el historial de la venta."""
        for sale in self:
            pdf, _type = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
                'scout_management.action_report_store_receipt', sale.ids)
            attachment = self.env['ir.attachment'].sudo().create({
                'name': f'Recibo {sale.name}.pdf', 'type': 'binary', 'raw': pdf,
                'res_model': sale._name, 'res_id': sale.id, 'mimetype': 'application/pdf'})
            sale.message_post(body=_('Recibo generado.'), attachment_ids=attachment.ids)

    def action_cancel(self):
        for sale in self.filtered(lambda s: s.state == 'done'):
            Stock = self.env['scout.store.stock.move'].sudo()
            for line in sale.line_ids.filtered(lambda l: l.product_id.scout_track_qty):
                Stock.create({'date': fields.Date.context_today(sale), 'product_tmpl_id': line.product_id.product_tmpl_id.id,
                              'move_kind': 'cancel', 'quantity': line.quantity, 'sale_line_id': line.id,
                              'note': _('Cancelación %s', sale.name)})
            if sale.treasury_move_id:
                sale.treasury_move_id.sudo().action_cancel()
            if sale.invoice_id and sale.invoice_id.state == 'posted':
                if sale.invoice_id.payment_state not in ('not_paid',):
                    raise UserError(_('El cargo %s ya tiene pagos; cancela primero el pago.', sale.invoice_id.name))
                sale.invoice_id.sudo().button_draft()
                sale.invoice_id.sudo().button_cancel()
        self.write({'state': 'cancel'})

    def action_draft(self):
        self.filtered(lambda s: s.state == 'cancel').write(
            {'state': 'draft', 'treasury_move_id': False, 'invoice_id': False})

    # ------------------------------------------------------------------ envío del recibo
    def _receipt_public_url(self):
        self.ensure_one()
        self._portal_ensure_token()
        base = self.get_base_url()
        return f'{base}/scout/recibo/{self.id}?access_token={self.access_token}'

    def action_send_email(self):
        self.ensure_one()
        template = self.env.ref('scout_management.mail_template_store_receipt')
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_model': self._name, 'default_res_ids': self.ids,
                'default_template_id': template.id, 'default_composition_mode': 'comment',
                'force_email': True, 'mark_receipt_sent': True,
            },
        }

    def action_send_whatsapp(self):
        """Abre WhatsApp con el mensaje y la liga al PDF del recibo; queda registro en el historial."""
        self.ensure_one()
        phone = ''.join(ch for ch in (self.partner_id.phone or '') if ch.isdigit())
        if not phone:
            raise UserError(_('%s no tiene teléfono registrado.', self.partner_id.name))
        if len(phone) == 10:
            phone = '52' + phone  # México
        text = _('Hola %(name)s, te compartimos tu recibo %(folio)s por %(amount)s del %(company)s: %(url)s',
                 name=self.partner_id.name, folio=self.name,
                 amount=f'${self.amount_total:,.2f}', company=self.company_id.name, url=self._receipt_public_url())
        if self.invoice_id.scout_can_pay_online:
            text += '\n' + _('Puedes pagarlo en línea aquí: %s', self.invoice_id._scout_payment_link())
        from urllib.parse import quote
        self.message_post(body=_('Recibo enviado por WhatsApp a %(name)s (%(phone)s).',
                                 name=self.partner_id.name, phone=phone))
        self.receipt_sent = True
        return {'type': 'ir.actions.act_url', 'url': f'https://wa.me/{phone}?text={quote(text)}', 'target': 'new'}

    def _message_post_after_hook(self, message, msg_vals):
        if self.env.context.get('mark_receipt_sent') and message.message_type == 'comment':
            self.receipt_sent = True
        return super()._message_post_after_hook(message, msg_vals)


class ScoutStoreSaleLine(models.Model):
    _name = 'scout.store.sale.line'
    _description = 'Producto vendido'
    _order = 'sale_id, id'

    sale_id = fields.Many2one('scout.store.sale', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', string='Producto', required=True,
                                 domain="[('scout_store_ok', '=', True)]")
    available_qty = fields.Float(related='product_id.scout_qty_available', string='Existencia')
    quantity = fields.Float(string='Cantidad', required=True, default=1.0, digits='Product Unit')
    price_unit = fields.Float(string='Precio', required=True, digits='Product Price')
    subtotal = fields.Monetary(string='Importe', compute='_compute_subtotal', store=True,
                               currency_field='currency_id')
    currency_id = fields.Many2one(related='sale_id.currency_id')
    # Datos de la venta para el "parte de ventas"
    date = fields.Date(related='sale_id.date', store=True, string='Fecha')
    sale_type = fields.Selection(related='sale_id.sale_type', store=True, string='Tipo de venta')
    partner_id = fields.Many2one(related='sale_id.partner_id', store=True, string='Quién compró')
    member_id = fields.Many2one(related='sale_id.member_id', store=True, string='Miembro')
    paid_state = fields.Selection(related='sale_id.paid_state', store=True, string='¿Pagó?')
    sale_state = fields.Selection(related='sale_id.state', store=True, string='Estado')

    @api.depends('quantity', 'price_unit')
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.quantity * line.price_unit

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.price_unit = self.product_id.lst_price

    @api.constrains('quantity', 'price_unit')
    def _check_values(self):
        for line in self:
            if line.quantity <= 0 or line.price_unit < 0:
                raise ValidationError(_('La cantidad debe ser mayor a cero y el precio no puede ser negativo.'))
