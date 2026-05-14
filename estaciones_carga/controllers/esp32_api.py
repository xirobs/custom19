# -*- coding: utf-8 -*-
"""
API REST para los ESP32 de las estaciones.

Autenticación: Bearer token (API key de la estación).
Todos los endpoints son auth='none' porque los ESP32 no son usuarios de Odoo.
La validación se hace contra el campo api_key del modelo cargas.estacion.

NOTA Odoo 19: los controladores JSON ahora usan type='jsonrpc'.
Aquí usamos type='http' porque el ESP32 envía JSON simple, no JSON-RPC.
"""
import json
import logging
from datetime import datetime
from odoo import http, fields
from odoo.http import request, Response

_logger = logging.getLogger(__name__)


def _json_response(data, status=200):
    """Helper para devolver JSON consistente"""
    return Response(
        json.dumps(data, default=str),
        status=status,
        content_type='application/json',
    )


def _autenticar_estacion(codigo_estacion):
    """
    Valida el header Authorization contra la api_key de la estación.
    Retorna el record de la estación o None si falla.
    """
    auth = request.httprequest.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return None

    api_key = auth.replace('Bearer ', '').strip()

    # Usamos sudo() porque el request no tiene usuario autenticado
    estacion = request.env['cargas.estacion'].sudo().search([
        ('codigo', '=', codigo_estacion),
        ('api_key', '=', api_key),
        ('active', '=', True),
    ], limit=1)

    return estacion or None


class EstacionAPI(http.Controller):

    @http.route(
        '/api/estacion/<string:codigo>/activaciones',
        type='http',
        auth='none',
        methods=['GET'],
        csrf=False,
    )
    def obtener_activaciones(self, codigo, **kwargs):
        """
        El ESP32 hace polling cada 5 segundos a este endpoint.
        Devuelve las transacciones PAGADAS aún no aplicadas.
        """
        estacion = _autenticar_estacion(codigo)
        if not estacion:
            return _json_response({'error': 'unauthorized'}, status=401)

        # Buscar transacciones pagadas sin aplicar
        transacciones = request.env['cargas.transaccion'].sudo().search([
            ('estacion_id', '=', estacion.id),
            ('estado', '=', 'pagado'),
        ])

        activaciones = [{
            'transaccion_id': t.id,
            'referencia': t.referencia,
            'puerto': t.puerto,
            'minutos': t.minutos,
            'token': t.token_activacion,
        } for t in transacciones]

        return _json_response({
            'activaciones': activaciones,
            'timestamp_servidor': fields.Datetime.now().isoformat(),
        })

    @http.route(
        '/api/estacion/<string:codigo>/confirmar-aplicacion',
        type='http',
        auth='none',
        methods=['POST'],
        csrf=False,
    )
    def confirmar_aplicacion(self, codigo, **kwargs):
        """
        El ESP32 confirma que ya activó el relé del puerto.
        Marcamos la transacción como APLICADO.
        """
        estacion = _autenticar_estacion(codigo)
        if not estacion:
            return _json_response({'error': 'unauthorized'}, status=401)

        try:
            data = json.loads(request.httprequest.data or '{}')
        except json.JSONDecodeError:
            return _json_response({'error': 'invalid_json'}, status=400)

        transaccion_id = data.get('transaccion_id')
        token = data.get('token')

        if not transaccion_id or not token:
            return _json_response({'error': 'missing_params'}, status=400)

        transaccion = request.env['cargas.transaccion'].sudo().browse(int(transaccion_id))
        if not transaccion.exists():
            return _json_response({'error': 'not_found'}, status=404)

        if transaccion.estacion_id.id != estacion.id:
            return _json_response({'error': 'wrong_station'}, status=403)

        if transaccion.token_activacion != token:
            return _json_response({'error': 'invalid_token'}, status=403)

        transaccion.action_marcar_aplicada()
        return _json_response({'ok': True})

    @http.route(
        '/api/estacion/<string:codigo>/heartbeat',
        type='http',
        auth='none',
        methods=['POST'],
        csrf=False,
    )
    def heartbeat(self, codigo, **kwargs):
        """
        El ESP32 reporta su estado cada minuto.
        """
        estacion = _autenticar_estacion(codigo)
        if not estacion:
            return _json_response({'error': 'unauthorized'}, status=401)

        try:
            data = json.loads(request.httprequest.data or '{}')
        except json.JSONDecodeError:
            data = {}

        # Crear el heartbeat
        request.env['cargas.heartbeat'].sudo().create({
            'estacion_id': estacion.id,
            'puertos_activos': data.get('puertos_activos', 0),
            'voltaje_ac': data.get('voltaje_ac'),
            'en_bateria': data.get('en_bateria', False),
            'rssi_wifi': data.get('rssi_wifi'),
            'firmware_version': data.get('firmware_version'),
            'ip_address': request.httprequest.remote_addr,
        })

        # Actualizar timestamp en la estación
        estacion.sudo().ultimo_heartbeat = fields.Datetime.now()

        return _json_response({
            'ok': True,
            'server_time': fields.Datetime.now().isoformat(),
            'surge_pricing_activo': estacion.en_bateria,
        })

    @http.route(
        '/api/estacion/<string:codigo>/codigo-offline',
        type='http',
        auth='none',
        methods=['POST'],
        csrf=False,
    )
    def usar_codigo_offline(self, codigo, **kwargs):
        """
        Validar un código offline (cuando el operador lo ingresa físicamente).
        """
        estacion = _autenticar_estacion(codigo)
        if not estacion:
            return _json_response({'error': 'unauthorized'}, status=401)

        try:
            data = json.loads(request.httprequest.data or '{}')
        except json.JSONDecodeError:
            return _json_response({'error': 'invalid_json'}, status=400)

        codigo_str = (data.get('codigo') or '').upper().strip()
        puerto = data.get('puerto')

        if not codigo_str or not puerto:
            return _json_response({'error': 'missing_params'}, status=400)

        codigo_obj = request.env['cargas.codigo_offline'].sudo().search([
            ('codigo', '=', codigo_str),
            ('estacion_id', '=', estacion.id),
            ('usado', '=', False),
        ], limit=1)

        if not codigo_obj:
            return _json_response({'error': 'codigo_invalido_o_usado'}, status=404)

        codigo_obj.marcar_usado(puerto)

        return _json_response({
            'ok': True,
            'puerto': puerto,
            'minutos': codigo_obj.minutos,
            'tipo_carga': codigo_obj.tipo_carga,
        })
