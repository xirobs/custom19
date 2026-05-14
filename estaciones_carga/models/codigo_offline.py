# -*- coding: utf-8 -*-
import secrets
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class CodigoOffline(models.Model):
    _name = 'cargas.codigo_offline'
    _description = 'Código de Activación Offline'
    _order = 'create_date desc'

    codigo = fields.Char(
        string='Código',
        required=True,
        copy=False,
        readonly=True,
        index=True,
    )
    estacion_id = fields.Many2one(
        'cargas.estacion',
        string='Estación',
        required=True,
        ondelete='cascade',
    )
    tipo_carga = fields.Selection(
        [('estandar', 'Estándar (60 min)'),
         ('rapida', 'Rápida (30 min)')],
        string='Tipo de carga',
        required=True,
        default='estandar',
    )
    minutos = fields.Integer(string='Duración (min)', required=True)
    usado = fields.Boolean(string='Usado', default=False)
    usado_en = fields.Datetime(string='Usado en', readonly=True)
    puerto_usado = fields.Integer(string='Puerto usado', readonly=True)
    lote_id = fields.Many2one('cargas.lote_codigos', string='Lote', ondelete='set null')

    _sql_constraints = [
        ('codigo_unique', 'unique(codigo)', 'El código debe ser único.'),
    ]

    @api.model
    def generar_codigo_unico(self):
        """Genera un código alfanumérico de 6 caracteres sin ambigüedades"""
        alfabeto = '0123456789ABCDEFGHJKMNPQRSTUVWXYZ'  # sin I, L, O
        while True:
            codigo = ''.join(secrets.choice(alfabeto) for _ in range(6))
            if not self.search_count([('codigo', '=', codigo)]):
                return codigo

    def marcar_usado(self, puerto):
        self.ensure_one()
        if self.usado:
            raise UserError(_('Este código ya fue utilizado.'))
        self.write({
            'usado': True,
            'usado_en': fields.Datetime.now(),
            'puerto_usado': puerto,
        })


class LoteCodigos(models.Model):
    """Agrupación de códigos generados juntos para entregar al operador"""
    _name = 'cargas.lote_codigos'
    _description = 'Lote de Códigos Offline'
    _order = 'create_date desc'

    name = fields.Char(string='Nombre', required=True, default=lambda self: _('Lote'))
    estacion_id = fields.Many2one('cargas.estacion', string='Estación', required=True)
    cantidad = fields.Integer(string='Cantidad', required=True, default=20)
    tipo_carga = fields.Selection(
        [('estandar', 'Estándar (60 min)'),
         ('rapida', 'Rápida (30 min)')],
        string='Tipo de carga',
        required=True,
        default='estandar',
    )
    codigo_ids = fields.One2many('cargas.codigo_offline', 'lote_id', string='Códigos')
    codigos_usados = fields.Integer(
        string='Usados',
        compute='_compute_estadisticas',
    )
    codigos_disponibles = fields.Integer(
        string='Disponibles',
        compute='_compute_estadisticas',
    )

    @api.depends('codigo_ids.usado')
    def _compute_estadisticas(self):
        for record in self:
            record.codigos_usados = len(record.codigo_ids.filtered('usado'))
            record.codigos_disponibles = len(record.codigo_ids) - record.codigos_usados
