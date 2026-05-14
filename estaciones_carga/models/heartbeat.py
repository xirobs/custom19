# -*- coding: utf-8 -*-
from datetime import timedelta
from odoo import models, fields, api


class Heartbeat(models.Model):
    _name = 'cargas.heartbeat'
    _description = 'Heartbeat de Estación'
    _order = 'timestamp desc'
    _rec_name = 'timestamp'

    estacion_id = fields.Many2one(
        'cargas.estacion',
        string='Estación',
        required=True,
        index=True,
        ondelete='cascade',
    )
    timestamp = fields.Datetime(
        string='Momento',
        default=fields.Datetime.now,
        required=True,
        index=True,
    )
    puertos_activos = fields.Integer(string='Puertos activos')
    voltaje_ac = fields.Float(string='Voltaje AC', digits=(5, 1))
    en_bateria = fields.Boolean(string='En batería')
    rssi_wifi = fields.Integer(string='Señal WiFi (dBm)')
    firmware_version = fields.Char(string='Versión firmware')
    ip_address = fields.Char(string='IP')

    @api.model
    def cron_limpiar_antiguos(self):
        """Conserva solo los últimos 30 días de heartbeats para evitar
        que la tabla crezca indefinidamente"""
        limite = fields.Datetime.now() - timedelta(days=30)
        antiguos = self.search([('timestamp', '<', limite)])
        antiguos.unlink()
        return len(antiguos)

    @api.model
    def detectar_apagones(self):
        """Cron que detecta apagones y activa surge pricing.
        Si los últimos 3 heartbeats de una estación reportan en_bateria=True,
        marcamos la estación como en apagón."""
        cinco_min = fields.Datetime.now() - timedelta(minutes=5)
        estaciones = self.env['cargas.estacion'].search([('active', '=', True)])

        for estacion in estaciones:
            recientes = self.search([
                ('estacion_id', '=', estacion.id),
                ('timestamp', '>=', cinco_min),
            ], order='timestamp desc', limit=3)

            if len(recientes) >= 2:
                en_apagon = all(hb.en_bateria for hb in recientes)
                if en_apagon != estacion.en_bateria:
                    estacion.en_bateria = en_apagon
                    if en_apagon:
                        estacion.message_post(
                            body='⚡ Apagón detectado. Surge pricing activado.'
                        )
                    else:
                        estacion.message_post(
                            body='✅ Energía restaurada. Surge pricing desactivado.'
                        )
