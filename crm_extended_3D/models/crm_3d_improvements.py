# -*- coding: utf-8 -*-
"""
Modelos para las mejoras de control de impresiones 3D:
- Líneas de coste por oportunidad
- Comunicaciones WhatsApp (seguimiento)
"""
from odoo import models, fields  # type: ignore


class CrmLeadCostLine(models.Model):
    """
    Línea de coste asociada a una oportunidad.
    Desglose por concepto (material, mano de obra, otro) para control de gastos.
    """
    _name = 'crm.lead.cost.line'
    _description = 'Línea de coste de oportunidad'

    lead_id = fields.Many2one(
        'crm.lead',  # type: ignore
        string='Oportunidad',
        required=True,
        ondelete='cascade',
    )
    name = fields.Char(string='Concepto', required=True)
    amount = fields.Float(string='Importe', digits='Product Price')
    cost_type = fields.Selection(
        [
            ('material', 'Material'),
            ('labor', 'Mano de obra'),
            ('external', 'Externalizado'),
            ('other', 'Otro'),
        ],
        string='Tipo',
        default='other',
    )
    date = fields.Date(string='Fecha', default=fields.Date.context_today)


class CrmWhatsappCommunication(models.Model):
    """
    Registro de una comunicación por WhatsApp (seguimiento, cotización, etc.).
    No envía mensajes; solo historial para seguimiento de la oportunidad.
    """
    _name = 'crm.whatsapp.communication'
    _description = 'Comunicación WhatsApp'

    lead_id = fields.Many2one(
        'crm.lead',  # type: ignore
        string='Oportunidad',
        required=True,
        ondelete='cascade',
    )
    date = fields.Datetime(string='Fecha', default=fields.Datetime.now)
    direction = fields.Selection(
        [
            ('outgoing', 'Enviado'),
            ('incoming', 'Recibido'),
        ],
        string='Dirección',
        default='outgoing',
    )
    communication_type = fields.Selection(
        [
            ('follow_up', 'Seguimiento'),
            ('quotation', 'Cotización'),
            ('reminder', 'Recordatorio'),
            ('other', 'Otro'),
        ],
        string='Tipo',
        default='follow_up',
    )
    summary = fields.Text(string='Resumen')
    user_id = fields.Many2one(
        'res.users',  # type: ignore
        string='Registrado por',
        default=lambda self: self.env.user,
    )
