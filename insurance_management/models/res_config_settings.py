# -*- coding: utf-8 -*-

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    insurance_auto_collection = fields.Boolean(
        string="Recordatorios de pago y pagos atrasados",
        config_parameter="insurance_management.auto_collection",
    )
    insurance_auto_renewal = fields.Boolean(
        string="Aviso de renovación", config_parameter="insurance_management.auto_renewal",
    )
    insurance_renewal_notice_days = fields.Integer(
        string="Días antes del vencimiento", config_parameter="insurance_management.renewal_notice_days", default=30,
    )
    insurance_auto_last_payments = fields.Boolean(
        string="Aviso de últimos pagos", config_parameter="insurance_management.auto_last_payments",
    )
    insurance_auto_signature = fields.Boolean(
        string="Recordatorio de firma de contrato", config_parameter="insurance_management.auto_signature",
    )
    insurance_auto_welcome = fields.Boolean(
        string="Bienvenida al confirmar la póliza (suma asegurada y beneficios)",
        config_parameter="insurance_management.auto_welcome",
    )
    insurance_auto_birthday = fields.Boolean(
        string="Felicitación de cumpleaños", config_parameter="insurance_management.auto_birthday",
    )
    insurance_auto_profession = fields.Boolean(
        string="Felicitación del día de su profesión", config_parameter="insurance_management.auto_profession",
    )
    insurance_due_soon_days = fields.Integer(
        string="Días para considerar una cuota «por vencer»",
        config_parameter="insurance_management.due_soon_days",
        default=15,
    )
    insurance_daily_digest = fields.Boolean(
        string="Resumen diario al equipo (tipos de cambio, cobranza, renovaciones, agenda)",
        config_parameter="insurance_management.daily_digest",
    )
