# -*- coding: utf-8 -*-
import hmac
import hashlib
import secrets
import time
import uuid
from datetime import timedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class Transaccion(models.Model):
    _name = 'cargas.transaccion'
    _description = 'Transacción de Carga'
    _inherit = ['mail.thread']
    _order = 'create_date desc'
    _rec_name = 'referencia'

    # Identificación
    referencia = fields.Char(
        string='Referencia',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('Nuevo'),
    )
    estacion_id = fields.Many2one(
        'cargas.estacion',
        string='Estación',
        required=True,
        index=True,
        ondelete='restrict',
        tracking=True,
    )
    puerto = fields.Integer(
        string='Puerto',
        required=True,
        tracking=True,
    )

    # Tipo y duración
    tipo_carga = fields.Selection(
        [('estandar', 'Estándar (60 min)'),
         ('rapida', 'Rápida (30 min)')],
        string='Tipo de carga',
        required=True,
        default='estandar',
        tracking=True,
    )
    minutos = fields.Integer(string='Duración (min)', required=True)

    # Pago
    metodo_pago = fields.Selection(
        [('pago_movil', 'Pago Móvil'),
         ('binance_pay', 'Binance Pay'),
         ('usdt_trc20', 'USDT TRC20'),
         ('zelle', 'Zelle'),
         ('efectivo', 'Efectivo')],
        string='Método de pago',
        required=True,
        tracking=True,
    )
    monto_base = fields.Monetary(
        string='Monto base (USD)',
        currency_field='currency_id',
        required=True,
    )
    surge_aplicado = fields.Boolean(string='Surge pricing aplicado', readonly=True)
    monto_total = fields.Monetary(
        string='Monto total (USD)',
        currency_field='currency_id',
        compute='_compute_monto_total',
        store=True,
    )
    monto_bs = fields.Float(
        string='Monto en bolívares',
        digits=(12, 2),
        help='Monto equivalente al momento de la transacción',
    )
    tasa_cambio = fields.Float(string='Tasa Bs/USD', digits=(12, 4))
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.ref('base.USD'),
    )

    # Estado
    estado = fields.Selection(
        [('pendiente', 'Pendiente de pago'),
         ('pagado', 'Pagado'),
         ('aplicado', 'Aplicado (puerto activado)'),
         ('completado', 'Completado'),
         ('expirado', 'Expirado'),
         ('reembolsado', 'Reembolsado')],
        string='Estado',
        default='pendiente',
        tracking=True,
        index=True,
    )
    referencia_pago = fields.Char(string='Referencia de pago')
    token_activacion = fields.Char(string='Token de activación', readonly=True, copy=False)
    telefono_cliente = fields.Char(string='Teléfono del cliente')

    # Timestamps
    pagada_en = fields.Datetime(string='Pagada en', readonly=True)
    aplicada_en = fields.Datetime(string='Aplicada en', readonly=True)
    expira_en = fields.Datetime(string='Expira en')

    # Facturación
    invoice_id = fields.Many2one(
        'account.move',
        string='Factura',
        readonly=True,
        copy=False,
    )

    # Índices
    _sql_constraints = [
        ('puerto_valido', 'CHECK(puerto > 0 AND puerto <= 16)',
         'El puerto debe estar entre 1 y 16.'),
    ]

    @api.depends('monto_base', 'surge_aplicado')
    def _compute_monto_total(self):
        surge_mult = float(self.env['ir.config_parameter'].sudo().get_param(
            'cargas.surge_multiplier', '1.5'
        ))
        for record in self:
            if record.surge_aplicado:
                record.monto_total = record.monto_base * surge_mult
            else:
                record.monto_total = record.monto_base

    @api.constrains('puerto', 'estacion_id')
    def _check_puerto_valido(self):
        for record in self:
            if record.puerto > record.estacion_id.num_puertos:
                raise ValidationError(_(
                    'El puerto %s no existe en la estación %s (tiene %s puertos).'
                ) % (record.puerto, record.estacion_id.name, record.estacion_id.num_puertos))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('referencia', _('Nuevo')) == _('Nuevo'):
                vals['referencia'] = self.env['ir.sequence'].next_by_code(
                    'cargas.transaccion'
                ) or _('Nuevo')
            if not vals.get('expira_en'):
                vals['expira_en'] = fields.Datetime.now() + timedelta(minutes=15)
        return super().create(vals_list)

    def _generar_token(self):
        """Genera token HMAC firmado para el ESP32"""
        self.ensure_one()
        if not self.estacion_id.hmac_secret:
            raise UserError(_('La estación no tiene HMAC secret configurado.'))

        random_part = secrets.token_hex(8)
        timestamp = int(time.time())
        payload = f"{random_part}.{self.puerto}.{self.minutos}.{timestamp}.{self.id}"

        firma = hmac.new(
            self.estacion_id.hmac_secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()[:16]

        return f"{payload}.{firma}"

    def action_confirmar_pago(self, referencia_pago=None):
        """Marca la transacción como pagada y genera el token"""
        self.ensure_one()

        if self.estado != 'pendiente':
            raise UserError(_('Solo transacciones pendientes pueden ser confirmadas.'))

        if self.expira_en and fields.Datetime.now() > self.expira_en:
            self.estado = 'expirado'
            raise UserError(_('Esta transacción ya expiró.'))

        self.write({
            'estado': 'pagado',
            'pagada_en': fields.Datetime.now(),
            'referencia_pago': referencia_pago or self.referencia_pago,
            'token_activacion': self._generar_token(),
        })

        # Generar factura automáticamente (configurable)
        crear_factura = self.env['ir.config_parameter'].sudo().get_param(
            'cargas.crear_factura_por_transaccion', 'True'
        ) == 'True'
        if crear_factura:
            self._crear_factura()

        return True

    def _crear_factura(self):
        """Crea la factura asociada a la transacción"""
        self.ensure_one()

        if self.invoice_id:
            return self.invoice_id

        if not self.estacion_id.journal_id:
            raise UserError(_('Configura un diario contable en la estación %s.') % self.estacion_id.name)

        producto = (
            self.estacion_id.product_carga_rapida_id
            if self.tipo_carga == 'rapida'
            else self.estacion_id.product_carga_estandar_id
        )
        if not producto:
            producto = self.env.ref(
                'estaciones_carga.product_carga_estandar', raise_if_not_found=False
            )
            if not producto:
                raise UserError(_('Configura los productos de carga en la estación.'))

        # Cliente: si tiene teléfono usamos un partner genérico
        partner = self.env.ref('estaciones_carga.partner_consumidor_final', raise_if_not_found=False)
        if not partner:
            partner = self.env['res.partner'].search([('name', '=', 'Consumidor Final')], limit=1)
            if not partner:
                partner = self.env['res.partner'].create({
                    'name': 'Consumidor Final',
                    'company_type': 'person',
                })

        invoice_vals = {
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'journal_id': self.estacion_id.journal_id.id,
            'invoice_date': fields.Date.today(),
            'currency_id': self.currency_id.id,
            'ref': self.referencia,
            'narration': _('Carga puerto %s - %s') % (self.puerto, self.estacion_id.name),
            'invoice_line_ids': [(0, 0, {
                'product_id': producto.id,
                'name': _('Carga %s - %s min - Puerto %s') % (
                    dict(self._fields['tipo_carga'].selection).get(self.tipo_carga),
                    self.minutos,
                    self.puerto,
                ),
                'quantity': 1,
                'price_unit': self.monto_total,
            })],
        }

        invoice = self.env['account.move'].create(invoice_vals)
        invoice.action_post()
        self.invoice_id = invoice.id

        # Registrar el pago para que la factura quede cobrada
        self._registrar_pago_factura(invoice)

        return invoice

    def _registrar_pago_factura(self, invoice):
        """Registra automáticamente el pago de la factura"""
        # Buscar el diario adecuado según el método de pago
        diario_codigo = {
            'pago_movil': 'PMOV',
            'binance_pay': 'BINP',
            'usdt_trc20': 'USDT',
            'zelle': 'ZELL',
            'efectivo': 'CASH',
        }.get(self.metodo_pago, 'CASH')

        journal = self.env['account.journal'].search([
            ('code', '=', diario_codigo),
            ('company_id', '=', self.env.company.id),
        ], limit=1)

        if not journal:
            # Fallback: cualquier diario de tipo bank o cash
            journal = self.env['account.journal'].search([
                ('type', 'in', ('bank', 'cash')),
                ('company_id', '=', self.env.company.id),
            ], limit=1)

        if not journal:
            return  # Sin diario disponible, la factura queda abierta

        payment_vals = {
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': invoice.partner_id.id,
            'amount': self.monto_total,
            'currency_id': invoice.currency_id.id,
            'journal_id': journal.id,
            'date': fields.Date.today(),
            'ref': self.referencia,
        }
        payment = self.env['account.payment'].create(payment_vals)
        payment.action_post()

        # Conciliar pago con factura
        receivable_lines = (invoice.line_ids + payment.move_id.line_ids).filtered(
            lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled
        )
        if len(receivable_lines) >= 2:
            receivable_lines.reconcile()

    def action_marcar_aplicada(self):
        """Llamado por el ESP32 cuando confirma que activó el relé"""
        self.ensure_one()
        if self.estado != 'pagado':
            raise UserError(_('Solo se pueden aplicar transacciones pagadas.'))
        self.write({
            'estado': 'aplicado',
            'aplicada_en': fields.Datetime.now(),
        })

    def action_reembolsar(self):
        """Reembolso manual desde la UI"""
        self.ensure_one()
        if self.estado not in ('pagado', 'aplicado'):
            raise UserError(_('Solo se pueden reembolsar transacciones pagadas.'))

        if self.invoice_id and self.invoice_id.state == 'posted':
            # Crear nota de crédito
            move_reversal = self.env['account.move.reversal'].with_context(
                active_model='account.move',
                active_ids=self.invoice_id.ids,
            ).create({
                'journal_id': self.invoice_id.journal_id.id,
                'reason': _('Reembolso de carga %s') % self.referencia,
            })
            move_reversal.refund_moves()

        self.estado = 'reembolsado'

    @api.model
    def cron_expirar_transacciones(self):
        """Cron que marca como expiradas las transacciones pendientes vencidas"""
        ahora = fields.Datetime.now()
        a_expirar = self.search([
            ('estado', '=', 'pendiente'),
            ('expira_en', '<', ahora),
        ])
        a_expirar.write({'estado': 'expirado'})
        return len(a_expirar)

    def action_view_invoice(self):
        """Abre la factura asociada"""
        self.ensure_one()
        if not self.invoice_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id': self.invoice_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
