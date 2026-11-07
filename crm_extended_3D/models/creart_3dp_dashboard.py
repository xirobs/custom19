"""Dashboard operativo CreArt 3DP."""

from odoo import api, fields, models, _


class Creart3dpDashboard(models.TransientModel):
    _name = "creart.3dp.dashboard"
    _description = "Dashboard CreArt 3DP"

    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    jobs_active = fields.Integer(string="Trabajos activos", compute="_compute_kpis")
    jobs_printing = fields.Integer(string="Imprimiendo", compute="_compute_kpis")
    jobs_queue = fields.Integer(string="En cola / diseño", compute="_compute_kpis")
    jobs_not_profitable = fields.Integer(string="No rentables", compute="_compute_kpis")
    jobs_material_shortage = fields.Integer(string="Faltante de stock", compute="_compute_kpis")
    total_expected_revenue = fields.Monetary(
        string="Ingreso esperado (pipeline)",
        compute="_compute_kpis",
        currency_field="currency_id",
    )
    total_cost_pipeline = fields.Monetary(
        string="Costo estimado (pipeline)",
        compute="_compute_kpis",
        currency_field="currency_id",
    )
    total_margin = fields.Monetary(
        string="Margen estimado",
        compute="_compute_kpis",
        currency_field="currency_id",
    )
    avg_margin_percent = fields.Float(string="Margen promedio (%)", compute="_compute_kpis")
    filament_kg_pipeline = fields.Float(string="Filamento en pipeline (kg)", compute="_compute_kpis")

    # Indicadores compartidos con la app del equipo (modelo creart.kpi)
    kpi_sold_month = fields.Monetary(string="Ventas del mes", compute="_compute_creart_kpis", currency_field="currency_id")
    kpi_sold_delta = fields.Monetary(string="Diferencia vs. mes anterior", compute="_compute_creart_kpis", currency_field="currency_id")
    kpi_average_ticket = fields.Monetary(string="Ticket promedio", compute="_compute_creart_kpis", currency_field="currency_id")
    kpi_receivable = fields.Monetary(string="Por cobrar", compute="_compute_creart_kpis", currency_field="currency_id")
    kpi_open_value = fields.Monetary(string="En proceso comercial", compute="_compute_creart_kpis", currency_field="currency_id")
    kpi_weighted = fields.Monetary(string="Pipeline ponderado", compute="_compute_creart_kpis", currency_field="currency_id")
    kpi_forecast_total = fields.Monetary(string="Pronóstico 3 meses", compute="_compute_creart_kpis", currency_field="currency_id")
    kpi_conversion = fields.Integer(string="Conversión (%)", compute="_compute_creart_kpis")
    kpi_cycle_days = fields.Integer(string="Ciclo de venta (días)", compute="_compute_creart_kpis")
    kpi_overdue = fields.Integer(string="Entregas vencidas", compute="_compute_creart_kpis")
    kpi_unpriced = fields.Integer(string="Sin precio", compute="_compute_creart_kpis")
    kpi_months_of_data = fields.Integer(string="Meses con datos", compute="_compute_creart_kpis")
    kpi_confidence = fields.Selection(
        [("baja", "Baja"), ("media", "Media"), ("alta", "Alta")],
        string="Confianza del pronóstico", compute="_compute_creart_kpis",
    )

    @api.depends_context("uid", "allowed_company_ids")
    def _compute_creart_kpis(self):
        for dash in self:
            data = self.env["creart.kpi"].get_dashboard(self.env.company)
            cards = data["cards"]
            dash.kpi_sold_month = cards["sold_month"]
            dash.kpi_sold_delta = cards["sold_delta"]
            dash.kpi_average_ticket = cards["average_ticket"]
            dash.kpi_receivable = cards["receivable"]
            dash.kpi_open_value = cards["open_value"]
            dash.kpi_weighted = cards["weighted"]
            dash.kpi_forecast_total = data["forecast_total"]
            dash.kpi_conversion = cards["conversion"]
            dash.kpi_cycle_days = cards["cycle_days"]
            dash.kpi_overdue = cards["overdue"]
            dash.kpi_unpriced = cards["unpriced"]
            dash.kpi_months_of_data = data["months_of_data"]
            dash.kpi_confidence = data["confidence"]

    def _compute_display_name(self):
        for dash in self:
            dash.display_name = _("Tablero CreArt 3DP")

    def _get_leads(self):
        return self.env["crm.lead"].search([
            ("type", "=", "opportunity"),
            ("active", "=", True),
            ("lead_sequence", "!=", False),
        ])

    @api.depends_context("uid")
    def _compute_kpis(self):
        printing = {"Imprimiendo", "En la cola de impresión", "En la cola de impresion"}
        queue = {"Conceptualización/Diseño", "Diseño/Ajustes", "Solicitud", "Ganada/Pendiente pago"}
        for dash in self:
            leads = dash._get_leads()
            dash.jobs_active = len(leads)
            dash.jobs_printing = len(leads.filtered(
                lambda l: l.stage_id and l.stage_id.name in printing
            ))
            dash.jobs_queue = len(leads.filtered(
                lambda l: l.stage_id and l.stage_id.name in queue
            ))
            dash.jobs_not_profitable = len(leads.filtered(
                lambda l: not l.rentable and l.total_cost
            ))
            dash.jobs_material_shortage = len(leads.filtered("material_shortage"))
            dash.total_expected_revenue = sum(leads.mapped("expected_revenue"))
            dash.total_cost_pipeline = sum(leads.mapped("total_cost"))
            dash.total_margin = dash.total_expected_revenue - dash.total_cost_pipeline
            margins = [l.margin_percent for l in leads if l.margin_percent]
            dash.avg_margin_percent = sum(margins) / len(margins) if margins else 0.0
            dash.filament_kg_pipeline = sum((l.filament_grams or 0.0) / 1000.0 for l in leads)

    def _open_leads(self, name, domain):
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": "crm.lead",
            "view_mode": "kanban,list,form,graph,pivot",
            "domain": domain + [("type", "=", "opportunity"), ("active", "=", True)],
            "context": {"search_default_opportunity": 1},
        }

    def action_open_active_jobs(self):
        return self._open_leads(_("Trabajos activos CreArt"), [("lead_sequence", "!=", False)])

    def action_open_printing_jobs(self):
        return self._open_leads(_("Imprimiendo"), [
            ("stage_id.name", "in", list({"Imprimiendo", "En la cola de impresión", "En la cola de impresion"})),
        ])

    def action_open_not_profitable(self):
        return self._open_leads(_("Trabajos no rentables"), [
            ("rentable", "=", False),
            ("total_cost", ">", 0),
        ])

    def action_open_material_shortage(self):
        return self._open_leads(_("Faltante de materiales"), [("material_shortage", "=", True)])

    def action_open_pipeline_graph(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Pipeline CreArt"),
            "res_model": "crm.lead",
            "view_mode": "graph,pivot,list,kanban,form",
            "domain": [
                ("type", "=", "opportunity"),
                ("active", "=", True),
                ("lead_sequence", "!=", False),
            ],
            "context": {
                "graph_mode": "bar",
                "graph_groupbys": ["stage_id"],
            },
        }

    def action_refresh_dashboard(self):
        self._compute_kpis()
        self._compute_creart_kpis()
        return {
            "type": "ir.actions.act_window",
            "name": _("Dashboard CreArt 3DP"),
            "res_model": "creart.3dp.dashboard",
            "view_mode": "form",
            "views": [[False, "form"]],
            "res_id": self.id,
            "target": "current",
        }

    @api.model
    def action_open_dashboard(self):
        dashboard = self.create({})
        return {
            "type": "ir.actions.act_window",
            "name": _("Dashboard CreArt 3DP"),
            "res_model": "creart.3dp.dashboard",
            "view_mode": "form",
            "views": [[False, "form"]],
            "res_id": dashboard.id,
            "target": "current",
        }
