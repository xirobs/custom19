# -*- coding: utf-8 -*-
from odoo import models, fields, api


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    cargas_precio_estandar = fields.Float(
        string='Precio carga estándar (USD)',
        config_parameter='cargas.precio_estandar',
        default=0.50,
    )
    cargas_precio_rapida = fields.Float(
        string='Precio carga rápida (USD)',
        config_parameter='cargas.precio_rapida',
        default=1.00,
    )
    cargas_surge_multiplier = fields.Float(
        string='Multiplicador de surge pricing',
        config_parameter='cargas.surge_multiplier',
        default=1.5,
        help='Cuánto se multiplica el precio durante apagones',
    )
    cargas_heartbeat_timeout_seg = fields.Integer(
        string='Timeout de heartbeat (segundos)',
        config_parameter='cargas.heartbeat_timeout_seg',
        default=90,
    )
    cargas_crear_factura_por_transaccion = fields.Boolean(
        string='Crear factura por cada carga',
        config_parameter='cargas.crear_factura_por_transaccion',
        default=True,
    )
    cargas_pago_movil_telefono = fields.Char(
        string='Teléfono Pago Móvil',
        config_parameter='cargas.pago_movil_telefono',
    )
    cargas_pago_movil_cedula = fields.Char(
        string='Cédula Pago Móvil',
        config_parameter='cargas.pago_movil_cedula',
    )
    cargas_pago_movil_banco = fields.Char(
        string='Banco Pago Móvil',
        config_parameter='cargas.pago_movil_banco',
        default='0102 - Banco de Venezuela',
    )
    cargas_zelle_email = fields.Char(
        string='Email Zelle',
        config_parameter='cargas.zelle_email',
    )
    cargas_usdt_direccion = fields.Char(
        string='Dirección USDT (TRC20)',
        config_parameter='cargas.usdt_direccion',
    )
    cargas_tasa_bcv = fields.Float(
        string='Tasa BCV actual (Bs/USD)',
        config_parameter='cargas.tasa_bcv',
        default=36.50,
    )
