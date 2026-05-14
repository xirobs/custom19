# -*- coding: utf-8 -*-
import secrets
import hashlib
from datetime import timedelta
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError


class Estacion(models.Model):
    _name = 'cargas.estacion'
    _description = 'Estación de Carga de Celulares'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'codigo'

    # Identificación
    codigo = fields.Char(
        string='Código',
        required=True,
        copy=False,
        tracking=True,
        help='Identificador único, ej: EST-001'
    )
    name = fields.Char(
        string='Nombre',
        required=True,
        tracking=True,
    )
    active = fields.Boolean(default=True)

    # Ubicación
    direccion = fields.Char(string='Dirección', tracking=True)
    ciudad = fields.Char(string='Ciudad')
    estado_geo = fields.Char(string='Estado / Región')
    latitud = fields.Float(string='Latitud', digits=(10, 6))
    longitud = fields.Float(string='Longitud', digits=(10, 6))

    # Configuración técnica
    num_puertos = fields.Integer(
        string='Cantidad de puertos',
        default=8,
        required=True,
    )
    api_key = fields.Char(
        string='API Key',
        copy=False,
        readonly=True,
        help='Clave que el ESP32 usa para autenticarse',
    )
    hmac_secret = fields.Char(
        string='HMAC Secret',
        copy=False,
        readonly=True,
        help='Secreto compartido con el ESP32 para firmar tokens',
    )

    # Operación
    operador_id = fields.Many2one(
        'res.partner',
        string='Operador / Encargado',
        domain=[('is_company', '=', False)],
        tracking=True,
    )
    comision_operador = fields.Float(
        string='Comisión operador (%)',
        default=20.0,
        help='Porcentaje que recibe el operador del local'
    )

    # Cuenta de ingresos para facturación
    journal_id = fields.Many2one(
        'account.journal',
        string='Diario contable',
        domain=[('type', '=', 'sale')],
        help='Diario donde se asentarán las facturas de esta estación',
    )
    product_carga_estandar_id = fields.Many2one(
        'product.product',
        string='Producto: Carga estándar',
    )
    product_carga_rapida_id = fields.Many2one(
        'product.product',
        string='Producto: Carga rápida',
    )

    # Estado en tiempo real
    ultimo_heartbeat = fields.Datetime(
        string='Último heartbeat',
        readonly=True,
    )
    en_linea = fields.Boolean(
        string='En línea',
        compute='_compute_en_linea',
        store=False,
    )
    en_bateria = fields.Boolean(
        string='En batería (apagón)',
        readonly=True,
        tracking=True,
    )

    # Estadísticas (calculadas)
    ingresos_hoy = fields.Monetary(
        string='Ingresos hoy',
        compute='_compute_estadisticas',
        currency_field='currency_id',
    )
    ingresos_mes = fields.Monetary(
        string='Ingresos del mes',
        compute='_compute_estadisticas',
        currency_field='currency_id',
    )
    cargas_hoy = fields.Integer(
        string='Cargas hoy',
        compute='_compute_estadisticas',
    )
    cargas_mes = fields.Integer(
        string='Cargas del mes',
        compute='_compute_estadisticas',
    )
    currency_id = fields.Many2one(
        'res.currency',
        default=lambda self: self.env.company.currency_id,
    )

    # Relaciones
    transaccion_ids = fields.One2many(
        'cargas.transaccion',
        'estacion_id',
        string='Transacciones',
    )
    heartbeat_ids = fields.One2many(
        'cargas.heartbeat',
        'estacion_id',
        string='Heartbeats',
    )

    # Constraints e índices (Odoo 19 los declara como atributos)
    _sql_constraints = [
        ('codigo_unique', 'unique(codigo)', 'El código de estación debe ser único.'),
    ]

    @api.constrains('num_puertos')
    def _check_num_puertos(self):
        for record in self:
            if record.num_puertos < 1 or record.num_puertos > 16:
                raise ValidationError(_('La cantidad de puertos debe estar entre 1 y 16.'))

    @api.depends('ultimo_heartbeat')
    def _compute_en_linea(self):
        timeout_seg = int(self.env['ir.config_parameter'].sudo().get_param(
            'cargas.heartbeat_timeout_seg', '90'
        ))
        ahora = fields.Datetime.now()
        for record in self:
            if record.ultimo_heartbeat:
                delta = (ahora - record.ultimo_heartbeat).total_seconds()
                record.en_linea = delta < timeout_seg
            else:
                record.en_linea = False

    @api.depends('transaccion_ids.estado', 'transaccion_ids.monto_total', 'transaccion_ids.pagada_en')
    def _compute_estadisticas(self):
        ahora = fields.Datetime.now()
        hoy_inicio = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
        mes_inicio = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        for record in self:
            txns = record.transaccion_ids.filtered(
                lambda t: t.estado in ('pagado', 'aplicado') and t.pagada_en
            )
            txns_hoy = txns.filtered(lambda t: t.pagada_en >= hoy_inicio)
            txns_mes = txns.filtered(lambda t: t.pagada_en >= mes_inicio)

            record.ingresos_hoy = sum(txns_hoy.mapped('monto_total'))
            record.ingresos_mes = sum(txns_mes.mapped('monto_total'))
            record.cargas_hoy = len(txns_hoy)
            record.cargas_mes = len(txns_mes)

    @api.model_create_multi
    def create(self, vals_list):
        """Generar API key y HMAC secret automáticamente al crear"""
        for vals in vals_list:
            if not vals.get('api_key'):
                vals['api_key'] = secrets.token_urlsafe(32)
            if not vals.get('hmac_secret'):
                vals['hmac_secret'] = secrets.token_hex(32)
        return super().create(vals_list)

    def action_regenerar_credenciales(self):
        """Botón para regenerar API key y secret (si se compromete uno)"""
        self.ensure_one()
        self.write({
            'api_key': secrets.token_urlsafe(32),
            'hmac_secret': secrets.token_hex(32),
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Credenciales regeneradas'),
                'message': _('Recuerda actualizar el ESP32 con las nuevas claves.'),
                'type': 'warning',
            }
        }

    def action_ver_transacciones(self):
        self.ensure_one()
        return {
            'name': _('Transacciones de %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'cargas.transaccion',
            'view_mode': 'list,form',
            'domain': [('estacion_id', '=', self.id)],
            'context': {'default_estacion_id': self.id},
        }
