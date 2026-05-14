# -*- coding: utf-8 -*-
"""
API pública para la webapp del cliente.
Esta API es la que se llama cuando el cliente escanea el QR
y completa el flujo de pago en su celular.
"""
import json
import logging
from datetime import timedelta
from odoo import http, fields, _
from odoo.http import request, Response

_logger = logging.getLogger(__name__)


def _json_response(data, status=200):
    return Response(
        json.dumps(data, default=str),
        status=status,
        content_type='application/json',
    )


def _calcular_precio(estacion, tipo_carga):
    """Calcula el precio considerando surge pricing"""
    icp = request.env['ir.config_parameter'].sudo()
    if tipo_carga == 'rapida':
        precio_base = float(icp.get_param('cargas.precio_rapida', '1.00'))
        minutos = 30
    else:
        precio_base = float(icp.get_param('cargas.precio_estandar', '0.50'))
        minutos = 60

    surge = estacion.en_bateria
    if surge:
        mult = float(icp.get_param('cargas.surge_multiplier', '1.5'))
        precio = round(precio_base * mult, 2)
    else:
        precio = precio_base

    return precio_base, precio, minutos, surge


def _instrucciones_pago(metodo, monto_usd, transaccion):
    """Genera instrucciones según el método de pago"""
    icp = request.env['ir.config_parameter'].sudo()
    tasa = float(icp.get_param('cargas.tasa_bcv', '36.50'))
    monto_bs = round(monto_usd * tasa, 2)

    if metodo == 'pago_movil':
        return {
            'tipo': 'pago_movil',
            'banco': icp.get_param('cargas.pago_movil_banco', '0102 - Banco de Venezuela'),
            'telefono': icp.get_param('cargas.pago_movil_telefono', ''),
            'cedula': icp.get_param('cargas.pago_movil_cedula', ''),
            'monto_bs': monto_bs,
            'monto_usd': monto_usd,
            'instrucciones': (
                f'1. Haz un pago móvil de Bs. {monto_bs:.2f}\n'
                f'2. Al destinatario indicado\n'
                f'3. Ingresa los últimos 6 dígitos de la referencia'
            ),
        }
    elif metodo == 'zelle':
        return {
            'tipo': 'zelle',
            'email': icp.get_param('cargas.zelle_email', ''),
            'monto_usd': monto_usd,
            'memo': transaccion.referencia,
            'instrucciones': 'Envía vía Zelle con el código en el memo.',
        }
    elif metodo == 'usdt_trc20':
        return {
            'tipo': 'usdt_trc20',
            'direccion': icp.get_param('cargas.usdt_direccion', ''),
            'red': 'TRON (TRC20)',
            'monto_usdt': monto_usd,
            'memo': transaccion.referencia,
            'instrucciones': 'Envía USDT a esta dirección. Confirmación en ~1 min.',
        }
    elif metodo == 'binance_pay':
        return {
            'tipo': 'binance_pay',
            'monto_usd': monto_usd,
            'instrucciones': 'Escanea el QR con tu app de Binance.',
        }
    else:
        return {
            'tipo': 'efectivo',
            'monto_usd': monto_usd,
            'monto_bs': monto_bs,
            'instrucciones': 'Paga en efectivo al operador.',
        }


class ClienteAPI(http.Controller):

    @http.route(
        '/api/pago/info-estacion/<string:codigo>',
        type='http',
        auth='public',
        methods=['GET'],
        csrf=False,
    )
    def info_estacion(self, codigo, **kwargs):
        """Cliente escanea QR. Le devolvemos info básica de la estación."""
        estacion = request.env['cargas.estacion'].sudo().search([
            ('codigo', '=', codigo),
            ('active', '=', True),
        ], limit=1)

        if not estacion:
            return _json_response({'error': 'estacion_no_encontrada'}, status=404)

        if not estacion.en_linea:
            return _json_response({'error': 'estacion_fuera_de_linea'}, status=503)

        icp = request.env['ir.config_parameter'].sudo()
        precio_estandar = float(icp.get_param('cargas.precio_estandar', '0.50'))
        precio_rapida = float(icp.get_param('cargas.precio_rapida', '1.00'))

        if estacion.en_bateria:
            mult = float(icp.get_param('cargas.surge_multiplier', '1.5'))
            precio_estandar = round(precio_estandar * mult, 2)
            precio_rapida = round(precio_rapida * mult, 2)

        return _json_response({
            'codigo': estacion.codigo,
            'nombre': estacion.name,
            'num_puertos': estacion.num_puertos,
            'surge_pricing': estacion.en_bateria,
            'precios': {
                'estandar': {'usd': precio_estandar, 'minutos': 60},
                'rapida': {'usd': precio_rapida, 'minutos': 30},
            },
        })

    @http.route(
        '/api/pago/iniciar',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
    )
    def iniciar_pago(self, **kwargs):
        """Cliente solicita una carga. Creamos transacción en estado pendiente."""
        try:
            data = json.loads(request.httprequest.data or '{}')
        except json.JSONDecodeError:
            return _json_response({'error': 'invalid_json'}, status=400)

        codigo_estacion = data.get('estacion_codigo')
        puerto = data.get('puerto')
        tipo_carga = data.get('tipo_carga', 'estandar')
        metodo_pago = data.get('metodo_pago')
        telefono = data.get('telefono_cliente')

        if not all([codigo_estacion, puerto, metodo_pago]):
            return _json_response({'error': 'missing_params'}, status=400)

        estacion = request.env['cargas.estacion'].sudo().search([
            ('codigo', '=', codigo_estacion),
            ('active', '=', True),
        ], limit=1)
        if not estacion:
            return _json_response({'error': 'estacion_no_encontrada'}, status=404)

        # Validar puerto disponible (no haya otra transacción activa en ese puerto)
        hace_dos_horas = fields.Datetime.now() - timedelta(hours=2)
        ocupado = request.env['cargas.transaccion'].sudo().search_count([
            ('estacion_id', '=', estacion.id),
            ('puerto', '=', int(puerto)),
            ('estado', '=', 'aplicado'),
            ('aplicada_en', '>=', hace_dos_horas),
        ])
        if ocupado:
            return _json_response({'error': 'puerto_ocupado'}, status=409)

        precio_base, precio_total, minutos, surge = _calcular_precio(estacion, tipo_carga)

        icp = request.env['ir.config_parameter'].sudo()
        tasa = float(icp.get_param('cargas.tasa_bcv', '36.50'))

        transaccion = request.env['cargas.transaccion'].sudo().create({
            'estacion_id': estacion.id,
            'puerto': int(puerto),
            'tipo_carga': tipo_carga,
            'minutos': minutos,
            'metodo_pago': metodo_pago,
            'monto_base': precio_base,
            'surge_aplicado': surge,
            'tasa_cambio': tasa,
            'monto_bs': round(precio_total * tasa, 2),
            'telefono_cliente': telefono,
        })

        instrucciones = _instrucciones_pago(metodo_pago, precio_total, transaccion)
        if surge:
            instrucciones['nota_surge'] = (
                '⚡ Precio especial por apagón. Tu carga sigue disponible '
                'gracias a nuestra batería de respaldo.'
            )

        return _json_response({
            'transaccion_id': transaccion.id,
            'referencia': transaccion.referencia,
            'monto_usd': precio_total,
            'monto_bs': transaccion.monto_bs,
            'instrucciones_pago': instrucciones,
            'expira_en': transaccion.expira_en.isoformat(),
        })

    @http.route(
        '/api/pago/confirmar',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
    )
    def confirmar_pago(self, **kwargs):
        """Cliente ingresa la referencia del pago realizado."""
        try:
            data = json.loads(request.httprequest.data or '{}')
        except json.JSONDecodeError:
            return _json_response({'error': 'invalid_json'}, status=400)

        transaccion_id = data.get('transaccion_id')
        referencia_pago = data.get('referencia_pago')

        if not transaccion_id or not referencia_pago:
            return _json_response({'error': 'missing_params'}, status=400)

        transaccion = request.env['cargas.transaccion'].sudo().browse(int(transaccion_id))
        if not transaccion.exists():
            return _json_response({'error': 'not_found'}, status=404)

        # Validación básica del formato de la referencia
        ref = referencia_pago.strip()
        if len(ref) < 4:
            return _json_response({'error': 'referencia_invalida'}, status=400)

        # NOTA: en producción aquí debes validar contra la API del banco,
        # o leer SMS de notificación, o un servicio como ConectaPay.
        # Por ahora aceptamos cualquier referencia válida.
        # Para Binance Pay y USDT esto se valida por webhook en otro endpoint.

        try:
            transaccion.action_confirmar_pago(referencia_pago=ref)
        except Exception as e:
            _logger.exception('Error confirmando pago %s', transaccion.referencia)
            return _json_response({'error': str(e)}, status=400)

        return _json_response({
            'ok': True,
            'mensaje': f'¡Pago confirmado! Tu puerto {transaccion.puerto} se activará en segundos.',
            'puerto': transaccion.puerto,
            'minutos': transaccion.minutos,
        })

    @http.route(
        '/api/pago/estado/<int:transaccion_id>',
        type='http',
        auth='public',
        methods=['GET'],
        csrf=False,
    )
    def estado_transaccion(self, transaccion_id, **kwargs):
        """Cliente puede verificar si su carga ya está activa."""
        transaccion = request.env['cargas.transaccion'].sudo().browse(transaccion_id)
        if not transaccion.exists():
            return _json_response({'error': 'not_found'}, status=404)

        return _json_response({
            'estado': transaccion.estado,
            'puerto': transaccion.puerto,
            'minutos': transaccion.minutos,
            'aplicada_en': transaccion.aplicada_en.isoformat() if transaccion.aplicada_en else None,
        })

    # ============================================
    # WEBHOOK BINANCE PAY (para confirmación automática)
    # ============================================
    @http.route(
        '/api/webhook/binance-pay',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
    )
    def webhook_binance(self, **kwargs):
        """Binance Pay llama acá cuando un pago se completa."""
        # TODO: validar firma del webhook contra BINANCE_PAY_SECRET
        try:
            data = json.loads(request.httprequest.data or '{}')
            merchant_trade_no = data.get('merchantTradeNo')
            status = data.get('bizStatus')

            if status == 'PAY_SUCCESS':
                transaccion = request.env['cargas.transaccion'].sudo().search([
                    ('referencia', '=', merchant_trade_no),
                    ('estado', '=', 'pendiente'),
                ], limit=1)
                if transaccion:
                    transaccion.action_confirmar_pago(referencia_pago=merchant_trade_no)
        except Exception as e:
            _logger.exception('Error en webhook Binance: %s', e)

        return _json_response({'returnCode': 'SUCCESS'})
