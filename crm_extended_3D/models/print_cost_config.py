from odoo import models, fields, api, _
from odoo.exceptions import UserError  # type: ignore


class PrintCostConfig(models.Model):
    _name = 'print.cost.config'
    _description = 'Configuración costos impresión 3D'

    name = fields.Char(default="Configuración General")

    @api.model
    def get_config(self):
        """Devuelve el único registro de configuración global."""
        config = self.search([], order='id', limit=1)
        if not config:
            config = self.create({'name': 'CreArt 3DP - Configuración General'})
        return config

    @api.model
    def action_open_configuration(self):
        """Abre siempre el registro único de configuración (evita salir del módulo)."""
        config = self.get_config()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Configuración Costos 3D'),
            'res_model': 'print.cost.config',
            'view_mode': 'form',
            'res_id': config.id,
            'target': 'current',
            'context': {'create': False, 'delete': False},
        }

    @api.model_create_multi
    def create(self, vals_list):
        if self.search_count([]):
            raise UserError(_('Solo puede existir una configuración de costos CreArt.'))
        return super().create(vals_list)

    # COSTOS FIJOS
    internet_hour_cost = fields.Float("Costo internet / hora")
    electricity_hour_cost = fields.Float("Costo electricidad / hora")
    depreciation_hour_cost = fields.Float("Depreciación equipo / hora")
    capacity_increase_hour_cost = fields.Float("Aumento capacidad / hora")
    spare_parts_hour_cost = fields.Float("Repuestos / hora")
    postprocess_consumables_cost = fields.Float(
        string="Consumibles postprocesado / hr",
        help="Se aplica solo sobre horas de impresión activa (no tiempo muerto).",
    )
    dead_time_hour_cost = fields.Float(
        string="Costo tiempo muerto / hr",
        default=0.55,
        help="Tarifa reducida para horas de tiempo muerto (hoja CreArt).",
    )

    fiscal_charge_percent = fields.Float("Carga fiscal (%)")

    def action_apply_sheet_defaults(self):
        """Restaura tarifas de la pestaña prueba del spreadsheet CreArt."""
        self.write({
            "internet_hour_cost": 1.35,
            "electricity_hour_cost": 0.29,
            "depreciation_hour_cost": 2.0,
            "capacity_increase_hour_cost": 4.0,
            "spare_parts_hour_cost": 0.5,
            "postprocess_consumables_cost": 10.0,
            "dead_time_hour_cost": 0.55,
            "fiscal_charge_percent": 16.0,
            "mo_concept_hour": 70.0,
            "mo_design_hour": 150.0,
            "mo_operation_hour": 50.0,
            "mo_post_hour": 100.0,
            "sale_markup_1_12": 2.0,
            "sale_markup_12_24": 1.75,
            "sale_markup_24_plus": 1.5,
        })

    # MANO DE OBRA
    mo_concept_hour = fields.Float("MO Conceptualización / hora")
    mo_design_hour = fields.Float("MO Diseño / hora")
    mo_operation_hour = fields.Float("MO Operación / hora")
    mo_post_hour = fields.Float("MO Postprocesado / hora")

    sale_markup_1_12 = fields.Float(
        string="Utilidad menor 12 pzas (×)",
        default=2.0,
        help="100% utilidad → precio = costo unitario × 2.",
    )
    sale_markup_12_24 = fields.Float(
        string="Utilidad 12-24 pzas (×)",
        default=1.75,
        help="75% utilidad → precio = costo unitario × 1.75.",
    )
    sale_markup_24_plus = fields.Float(
        string="Utilidad 24+ pzas (×)",
        default=1.5,
        help="50% utilidad → precio = costo unitario × 1.5.",
    )