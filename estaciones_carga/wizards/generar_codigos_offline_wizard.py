# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class GenerarCodigosOfflineWizard(models.TransientModel):
    _name = 'cargas.generar_codigos_offline.wizard'
    _description = 'Generar Lote de Códigos Offline'

    estacion_id = fields.Many2one(
        'cargas.estacion',
        string='Estación',
        required=True,
    )
    cantidad = fields.Integer(
        string='Cantidad',
        default=20,
        required=True,
    )
    tipo_carga = fields.Selection(
        [('estandar', 'Estándar (60 min)'),
         ('rapida', 'Rápida (30 min)')],
        string='Tipo de carga',
        required=True,
        default='estandar',
    )
    nombre_lote = fields.Char(string='Nombre del lote', default=lambda self: _('Lote'))

    def action_generar(self):
        self.ensure_one()
        if self.cantidad < 1 or self.cantidad > 200:
            raise UserError(_('La cantidad debe estar entre 1 y 200.'))

        minutos = 30 if self.tipo_carga == 'rapida' else 60

        lote = self.env['cargas.lote_codigos'].create({
            'name': self.nombre_lote,
            'estacion_id': self.estacion_id.id,
            'cantidad': self.cantidad,
            'tipo_carga': self.tipo_carga,
        })

        codigos_vals = []
        for _i in range(self.cantidad):
            codigo = self.env['cargas.codigo_offline'].generar_codigo_unico()
            codigos_vals.append({
                'codigo': codigo,
                'estacion_id': self.estacion_id.id,
                'tipo_carga': self.tipo_carga,
                'minutos': minutos,
                'lote_id': lote.id,
            })

        self.env['cargas.codigo_offline'].create(codigos_vals)

        # Abrir el lote recién creado
        return {
            'type': 'ir.actions.act_window',
            'name': _('Lote generado'),
            'res_model': 'cargas.lote_codigos',
            'res_id': lote.id,
            'view_mode': 'form',
            'target': 'current',
        }
