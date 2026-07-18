# -*- coding: utf-8 -*-
"""
Modelos para las mejoras de control de impresiones 3D:
- Pasos del proceso de manufactura (diseño → entrega)
"""
from odoo import models, fields  # type: ignore

class CrmPrintProcessStep(models.Model):
    """
    Paso del proceso de impresión 3D vinculado a una oportunidad.
    Permite llevar el control desde diseño hasta la entrega sin depender de MRP.
    """
    _name = 'crm.print.process.step'
    _description = 'Paso del proceso de impresión 3D'
    _order = 'sequence, id'

    lead_id = fields.Many2one(
        'crm.lead',  # type: ignore
        string="Opportunity",
        ondelete='cascade'
    )
    name = fields.Char(string='Paso', required=True)
    sequence = fields.Integer(string='Secuencia', default=10)
    state = fields.Selection(
        [
            ('pending', 'Pendiente'),
            ('in_progress', 'En curso'),
            ('done', 'Completado'),
        ],
        string='Estado',
        default='pending',
        required=True,
    )
    date_start = fields.Datetime(string='Inicio')
    date_done = fields.Datetime(string='Fin')
    user_id = fields.Many2one(
        'res.users',  # type: ignore
        string='Responsable',
        default=lambda self: self.env.user,
    )
    notes = fields.Text(string='Notas')