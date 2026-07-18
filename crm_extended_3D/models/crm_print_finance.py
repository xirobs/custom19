"""Campos financieros para el flujo de impresion 3D en CRM."""

import math

from odoo import models, fields, api

class CrmLeadFinance(models.Model):
    """Agrega campos de costeo financiero al modelo `crm.lead`."""

    _inherit = 'crm.lead'  # type: ignore

    def _get_default_cost_value(self, field_name):
        """Obtiene valor por defecto desde la configuracion global de costos."""
        config = self.env['print.cost.config'].search([], limit=1)
        return getattr(config, field_name, 0.0) if config else 0.0

    def _get_sale_markups(self):
        """Multiplicadores de venta por tramo (desde configuración global)."""
        config = self.env['print.cost.config'].search([], limit=1)
        if not config:
            return {'1_12': 2.0, '12_24': 1.75, '24_plus': 1.5}
        return {
            '1_12': config.sale_markup_1_12 or 2.0,
            '12_24': config.sale_markup_12_24 or 1.75,
            '24_plus': config.sale_markup_24_plus or 1.5,
        }

    def _effective_piece_count(self):
        """Cantidad comercial de piezas (sincroniza units con quantity del lead)."""
        self.ensure_one()
        qty = int(self.units or 0)
        if qty <= 0 and self.quantity:
            qty = max(1, int(round(self.quantity)))
        return max(qty, 1)

    @api.onchange('quantity')
    def _onchange_quantity_sync_units(self):
        if self.quantity:
            self.units = max(1, int(round(self.quantity)))

    @api.onchange('units')
    def _onchange_units_sync_quantity(self):
        if self.units:
            self.quantity = float(self.units)

    def _round_unit_price_up(self, amount):
        """Redondeo comercial hacia arriba a decenas (hoja CreArt)."""
        return int(math.ceil((amount or 0.0) / 10.0) * 10)

    def _round_sale_total_up(self, amount):
        """Redondeo comercial hacia arriba a centenas (hoja CreArt)."""
        return int(math.ceil((amount or 0.0) / 100.0) * 100)

    def action_reload_cost_config(self):
        """Recarga tarifas globales en la oportunidad y recalcula costos."""
        self._apply_cost_config_masters()
        return True

    def _apply_cost_config_masters(self):
        """Copia tarifas globales al lead (hoja CreArt / configuración)."""
        config = self.env["print.cost.config"].search([], limit=1)
        if not config:
            return
        mapping = {
            "mo_concept_rate": "mo_concept_hour",
            "mo_design_rate": "mo_design_hour",
            "mo_operation_rate": "mo_operation_hour",
            "mo_post_rate": "mo_post_hour",
            "internet_hour_cost_master": "internet_hour_cost",
            "electricity_hour_cost_master": "electricity_hour_cost",
            "depreciation_hour_cost_master": "depreciation_hour_cost",
            "capacity_increase_hour_cost_master": "capacity_increase_hour_cost",
            "spare_parts_hour_cost_master": "spare_parts_hour_cost",
            "postprocess_consumables_cost_master": "postprocess_consumables_cost",
            "dead_time_hour_cost_master": "dead_time_hour_cost",
            "fiscal_charge_percent_master": "fiscal_charge_percent",
        }
        for lead in self:
            lead.write({lead_field: getattr(config, cfg_field) for lead_field, cfg_field in mapping.items()})

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._apply_cost_config_masters()
        return records

    def _hours_from_parts(self, hours, minutes, seconds):
        return (hours or 0.0) + (minutes or 0.0) / 60.0 + (seconds or 0.0) / 3600.0

    def _get_print_hours(self):
        self.ensure_one()
        return self._hours_from_parts(
            self.print_hours, self.print_time_minutes, self.print_time_seconds
        )

    def _get_dead_hours(self):
        self.ensure_one()
        return self._hours_from_parts(
            self.dead_time_hours, self.dead_time_minutes, self.dead_time_seconds
        )

    def _get_active_machine_hour_rate(self):
        """Tarifa $/hr impresión activa (hoja CreArt: conceptos fijos + consumible/hr)."""
        self.ensure_one()
        return (
            (self.internet_hour_cost_master or 0.0)
            + (self.electricity_hour_cost_master or 0.0)
            + (self.depreciation_hour_cost_master or 0.0)
            + (self.capacity_increase_hour_cost_master or 0.0)
            + (self.spare_parts_hour_cost_master or 0.0)
            + (self.postprocess_consumables_cost_master or 0.0)
        )

    def _get_filament_cost(self):
        self.ensure_one()
        if not self.filament_id or not self.filament_grams:
            return 0.0
        price_kg = self.filament_id.standard_price or self.filament_id.lst_price or 0.0
        material = self.filament_grams * (price_kg / 1000.0)
        return material * (1 + ((self.filament_waste_percent or 0.0) / 100.0))

    # --------------------------------
    # SECCION 0: Datos maestro (tarifas base desde configuracion)
    # --------------------------------
    mo_concept_rate = fields.Float(
        string="Tarifa MO conceptualización",
        default=lambda self: self._get_default_cost_value('mo_concept_hour'),
    )
    mo_design_rate = fields.Float(
        string="Tarifa MO diseño",
        default=lambda self: self._get_default_cost_value('mo_design_hour'),
    )
    mo_operation_rate = fields.Float(
        string="Tarifa MO operación",
        default=lambda self: self._get_default_cost_value('mo_operation_hour'),
    )
    mo_post_rate = fields.Float(
        string="Tarifa MO postproceso",
        default=lambda self: self._get_default_cost_value('mo_post_hour'),
    )
    internet_hour_cost_master = fields.Float(
        string="Cuota internet / hora",
        default=lambda self: self._get_default_cost_value('internet_hour_cost'),
    )
    electricity_hour_cost_master = fields.Float(
        string="Consumo eléctrico / hora",
        default=lambda self: self._get_default_cost_value('electricity_hour_cost'),
    )
    depreciation_hour_cost_master = fields.Float(
        string="Depreciación / hora",
        default=lambda self: self._get_default_cost_value('depreciation_hour_cost'),
    )
    capacity_increase_hour_cost_master = fields.Float(
        string="Aumento capacidad / hora",
        default=lambda self: self._get_default_cost_value('capacity_increase_hour_cost'),
    )
    spare_parts_hour_cost_master = fields.Float(
        string="Repuestos / hora",
        default=lambda self: self._get_default_cost_value('spare_parts_hour_cost'),
    )
    postprocess_consumables_cost_master = fields.Float(
        string="Consumibles postproceso / hr",
        default=lambda self: self._get_default_cost_value('postprocess_consumables_cost'),
    )
    dead_time_hour_cost_master = fields.Float(
        string="Tiempo muerto / hr",
        default=0.55,
        help="Tarifa reducida para horas de tiempo muerto (ver Configuración costos).",
    )
    fiscal_charge_percent_master = fields.Float(
        string="Carga fiscal (%)",
        default=lambda self: self._get_default_cost_value('fiscal_charge_percent'),
    )

    # --------------------------------
    # SECCION 1: Datos de impresion
    # --------------------------------
    filament_id = fields.Many2one(
        'product.product',
        string="Filamento"
    )

    filament_grams = fields.Float("Gramos usados")

    print_hours = fields.Integer("Horas impresión")
    print_time_minutes = fields.Integer("Minutos impresión")
    print_time_seconds = fields.Integer("Segundos impresión")

    dead_time_hours = fields.Integer("Tiempo muerto")
    dead_time_minutes = fields.Integer("Minutos tiempo muerto")
    dead_time_seconds = fields.Integer("Segundos tiempo muerto")

    units = fields.Integer(
        string="Cantidad piezas",
        default=1,
        help="Piezas del pedido. Se sincroniza con el campo Quantity del lead.",
    )
    filament_waste_percent = fields.Float(string="% desperdicio/soporte", default=0.0)
    print_progress = fields.Float(string="Progreso impresión %", compute="_compute_print_progress", store=True)

    # --------------------------------
    # SECCION 2: Mano de obra
    # --------------------------------
    mo_concept_hours = fields.Float("Horas conceptualización")
    mo_design_hours = fields.Float("Horas diseño")
    mo_operation_hours = fields.Float("Horas operación")
    mo_postprocess_hours = fields.Float("Horas postprocesado")

    accessory_ids = fields.One2many(
        'crm.print.accessory.line',
        'lead_id'
    )

    # --------------------------------
    # SECCION 3: Resultados financieros
    # --------------------------------
    unit_cost = fields.Float(
        compute="_compute_costs",
        store=True
    )

    total_cost = fields.Float(
        compute="_compute_costs",
        store=True
    )

    price_50 = fields.Float(compute="_compute_costs", store=True)
    price_75 = fields.Float(compute="_compute_costs", store=True)
    price_100 = fields.Float(compute="_compute_costs", store=True)
    distributor_margin_percent = fields.Float(
        string="% margen distribuidor",
        default=35.0,
        help="Margen aplicado para propuesta de precio a distribuidor.",
    )
    price_distributor = fields.Float(
        string="Precio distribuidor",
        compute="_compute_price_tiers",
        store=True,
    )
    price_1_12 = fields.Float(
        string="Precio unitario 1-12 piezas",
        compute="_compute_price_tiers",
        store=True,
        digits=(16, 0),
    )
    price_12_24 = fields.Float(
        string="Precio unitario 12-24 piezas",
        compute="_compute_price_tiers",
        store=True,
        digits=(16, 0),
    )
    price_24_plus = fields.Float(
        string="Precio unitario 24+ piezas",
        compute="_compute_price_tiers",
        store=True,
        digits=(16, 0),
    )
    selected_unit_price = fields.Float(
        string="Precio unitario según cantidad",
        compute="_compute_price_tiers",
        store=True,
        digits=(16, 0),
    )
    selected_total_price = fields.Float(
        string="Precio total según cantidad",
        compute="_compute_price_tiers",
        store=True,
        digits=(16, 0),
    )
    batch_sale_reference = fields.Float(
        string="Venta ref. sobre costo total lote",
        compute="_compute_price_tiers",
        store=True,
        digits=(16, 0),
        help="Costo total del trabajo × multiplicador del tramo. Útil para comparar con hoja de cálculo.",
    )
    selected_unit_price_tax = fields.Float(
        string="P. unitario c/IVA",
        compute="_compute_price_tiers",
        store=True,
        digits=(16, 2),
    )
    selected_total_price_tax = fields.Float(
        string="P. venta c/IVA",
        compute="_compute_price_tiers",
        store=True,
        digits=(16, 2),
    )
    cost_machine_print = fields.Float(
        string="Costo horas impresión activa",
        compute="_compute_cost_breakdown",
        store=True,
    )
    cost_machine_dead = fields.Float(
        string="Costo tiempo muerto",
        compute="_compute_cost_breakdown",
        store=True,
    )

    accessories_total = fields.Float(
        string="Total accesorios",
        compute="_compute_cost_breakdown",
        store=True,
    )
    cost_material = fields.Float(string="Monto material", compute="_compute_cost_breakdown", store=True)
    cost_energy = fields.Float(string="Monto energía", compute="_compute_cost_breakdown", store=True)
    cost_internet = fields.Float(string="Monto internet", compute="_compute_cost_breakdown", store=True)
    cost_depreciation = fields.Float(string="Monto depreciación", compute="_compute_cost_breakdown", store=True)
    mo_cost = fields.Float(string="Monto mano de obra", compute="_compute_cost_breakdown", store=True)
    pct_material = fields.Float(string="% material", compute="_compute_cost_breakdown", store=True)
    pct_energy = fields.Float(string="% energía", compute="_compute_cost_breakdown", store=True)
    pct_internet = fields.Float(string="% internet", compute="_compute_cost_breakdown", store=True)
    pct_depreciation = fields.Float(string="% depreciación", compute="_compute_cost_breakdown", store=True)
    pct_labor = fields.Float(string="% mano de obra", compute="_compute_cost_breakdown", store=True)
    pct_accessories = fields.Float(string="% accesorios", compute="_compute_cost_breakdown", store=True)
    cost_fiscal = fields.Float(string="Monto fiscal", compute="_compute_cost_breakdown", store=True)
    pct_fiscal = fields.Float(string="% fiscal", compute="_compute_cost_breakdown", store=True)

    @api.depends('stage_progress', 'print_process_step_ids.state')
    def _compute_print_progress(self):
        for lead in self:
            steps = lead.print_process_step_ids
            step_pct = (len(steps.filtered(lambda s: s.state == 'done')) / len(steps) * 100) if steps else 0
            lead.print_progress = min(100.0, (lead.stage_progress or 0) * 0.6 + step_pct * 0.4)

    @api.depends(
        'filament_grams', 'filament_waste_percent', 'print_hours', 'print_time_minutes', 'print_time_seconds',
        'dead_time_hours', 'dead_time_minutes', 'dead_time_seconds', 'units', 'quantity',
        'mo_concept_rate', 'mo_design_rate', 'mo_operation_rate', 'mo_post_rate',
        'internet_hour_cost_master', 'electricity_hour_cost_master', 'depreciation_hour_cost_master',
        'capacity_increase_hour_cost_master', 'spare_parts_hour_cost_master',
        'postprocess_consumables_cost_master', 'dead_time_hour_cost_master',
        'fiscal_charge_percent_master',
        'mo_concept_hours', 'mo_design_hours', 'mo_operation_hours', 'mo_postprocess_hours',
        'accessory_ids', 'accessory_ids.subtotal', 'cost_line_ids.amount',
    )
    def _compute_costs(self):
        """Calcula costos alineados con la hoja CreArt (pestaña prueba)."""

        for lead in self:
            print_hours = lead._get_print_hours()
            dead_hours = lead._get_dead_hours()
            active_rate = lead._get_active_machine_hour_rate()
            dead_rate = lead.dead_time_hour_cost_master or 0.0

            machine_print_cost = print_hours * active_rate
            machine_dead_cost = dead_hours * dead_rate
            filament_cost = lead._get_filament_cost()

            mo_cost = (
                lead.mo_concept_hours * (lead.mo_concept_rate or 0.0)
                + lead.mo_design_hours * (lead.mo_design_rate or 0.0)
                + lead.mo_operation_hours * (lead.mo_operation_rate or 0.0)
                + lead.mo_postprocess_hours * (lead.mo_post_rate or 0.0)
            )

            accessories_cost = sum(
                line.subtotal if line.subtotal else (
                    (line.quantity or 0.0) * (
                        line.price_unit or (
                            line.product_id
                            and (line.product_id.standard_price or line.product_id.lst_price)
                        ) or 0.0
                    )
                )
                for line in lead.accessory_ids
            )

            manual_extra = sum(lead.cost_line_ids.mapped('amount'))
            taxable_subtotal = (
                machine_print_cost + filament_cost
                + mo_cost + accessories_cost + manual_extra
            )
            fiscal_amount = taxable_subtotal * (lead.fiscal_charge_percent_master or 0.0) / 100.0
            total = taxable_subtotal + fiscal_amount + machine_dead_cost

            lead.total_cost = total
            piece_count = lead._effective_piece_count()
            lead.unit_cost = total / piece_count

            markups = lead._get_sale_markups()
            lead.price_50 = lead.unit_cost * markups['24_plus']
            lead.price_75 = lead.unit_cost * markups['12_24']
            lead.price_100 = lead.unit_cost * markups['1_12']

    @api.depends(
        'unit_cost', 'price_50', 'price_75', 'price_100', 'distributor_margin_percent',
        'units', 'quantity', 'total_cost', 'fiscal_charge_percent_master',
    )
    def _compute_price_tiers(self):
        """Calcula propuestas de precio por tramo de volumen y distribuidor."""
        for lead in self:
            markups = lead._get_sale_markups()
            price_1_12 = lead.price_100 or (lead.unit_cost * markups['1_12'])
            price_12_24 = lead.price_75 or (lead.unit_cost * markups['12_24'])
            price_24_plus = lead.price_50 or (lead.unit_cost * markups['24_plus'])
            price_distributor = lead.unit_cost * (1 + (lead.distributor_margin_percent or 0.0) / 100.0)

            lead.price_1_12 = lead._round_unit_price_up(price_1_12)
            lead.price_12_24 = lead._round_unit_price_up(price_12_24)
            lead.price_24_plus = lead._round_unit_price_up(price_24_plus)
            lead.price_distributor = lead._round_unit_price_up(price_distributor)
            qty = lead._effective_piece_count()
            if qty < 12:
                lead.selected_unit_price = lead.price_1_12
                markup = markups['1_12']
            elif qty <= 24:
                lead.selected_unit_price = lead.price_12_24
                markup = markups['12_24']
            else:
                lead.selected_unit_price = lead.price_24_plus
                markup = markups['24_plus']
            lead.selected_total_price = lead._round_sale_total_up(
                lead.selected_unit_price * qty
            )
            lead.batch_sale_reference = lead._round_sale_total_up(
                (lead.total_cost or 0.0) * markup
            )
            iva_factor = 1 + ((lead.fiscal_charge_percent_master or 0.0) / 100.0)
            lead.selected_unit_price_tax = round(lead.selected_unit_price * iva_factor, 2)
            lead.selected_total_price_tax = round(lead.selected_total_price * iva_factor, 2)

    @api.depends(
        'total_cost', 'filament_grams', 'filament_id', 'filament_waste_percent',
        'print_hours', 'print_time_minutes', 'print_time_seconds',
        'dead_time_hours', 'dead_time_minutes', 'dead_time_seconds',
        'mo_concept_hours', 'mo_design_hours', 'mo_operation_hours', 'mo_postprocess_hours',
        'mo_concept_rate', 'mo_design_rate', 'mo_operation_rate', 'mo_post_rate',
        'internet_hour_cost_master', 'electricity_hour_cost_master', 'depreciation_hour_cost_master',
        'capacity_increase_hour_cost_master', 'spare_parts_hour_cost_master',
        'postprocess_consumables_cost_master', 'dead_time_hour_cost_master',
        'fiscal_charge_percent_master', 'accessory_ids.subtotal',
    )
    def _compute_cost_breakdown(self):
        """Calcula montos y porcentajes para separar pagos por rubro de costo."""
        for lead in self:
            print_hours = lead._get_print_hours()
            dead_hours = lead._get_dead_hours()
            active_rate = lead._get_active_machine_hour_rate()
            dead_rate = lead.dead_time_hour_cost_master or 0.0

            machine_print = print_hours * active_rate
            machine_dead = dead_hours * dead_rate
            material = lead._get_filament_cost()
            labor = (
                lead.mo_concept_hours * (lead.mo_concept_rate or 0.0)
                + lead.mo_design_hours * (lead.mo_design_rate or 0.0)
                + lead.mo_operation_hours * (lead.mo_operation_rate or 0.0)
                + lead.mo_postprocess_hours * (lead.mo_post_rate or 0.0)
            )
            accessories = sum(
                line.subtotal if line.subtotal else (
                    (line.quantity or 0.0) * (
                        line.price_unit or (
                            line.product_id
                            and (line.product_id.standard_price or line.product_id.lst_price)
                        ) or 0.0
                    )
                )
                for line in lead.accessory_ids
            )
            energy = print_hours * (lead.electricity_hour_cost_master or 0.0)
            energy += dead_hours * (lead.dead_time_hour_cost_master or 0.0)
            internet = print_hours * (lead.internet_hour_cost_master or 0.0)
            depreciation = print_hours * (lead.depreciation_hour_cost_master or 0.0)
            fiscal_percent = lead.fiscal_charge_percent_master or 0.0
            taxable = machine_print + material + labor + accessories
            fiscal = taxable * fiscal_percent / 100.0 if taxable else 0.0
            total = lead.total_cost or 0.0

            lead.cost_machine_print = machine_print
            lead.cost_machine_dead = machine_dead
            lead.accessories_total = accessories
            lead.cost_material = material
            lead.cost_energy = energy
            lead.cost_internet = internet
            lead.cost_depreciation = depreciation
            lead.mo_cost = labor
            lead.pct_material = (material / total * 100.0) if total else 0.0
            lead.pct_energy = (energy / total * 100.0) if total else 0.0
            lead.pct_internet = (internet / total * 100.0) if total else 0.0
            lead.pct_depreciation = (depreciation / total * 100.0) if total else 0.0
            lead.pct_labor = (labor / total * 100.0) if total else 0.0
            lead.pct_accessories = (accessories / total * 100.0) if total else 0.0
            lead.cost_fiscal = fiscal
            lead.pct_fiscal = (fiscal / total * 100.0) if total else 0.0
        