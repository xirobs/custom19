"""Plan estratégico de ventas CreArt 3DP (tablero por semanas, metas por cliente)."""

from datetime import date

from odoo import api, fields, models

WEEKS = [
    ("s0", "Metas por cliente"),
    ("s1", "Semana 1 · 16–22 nov · Meta acum. $8,000"),
    ("s2", "Semana 2 · 23–29 nov · Meta acum. $22,000"),
    ("s3", "Semana 3 · 30 nov–6 dic · Meta acum. $40,000"),
    ("s4", "Semana 4 · 7–13 dic · Meta acum. $62,000"),
    ("s5", "Semana 5 · 14–20 dic · Meta acum. $85,000"),
    ("s6", "Semana 6 · 21–31 dic · Meta acum. $100,000"),
]
CLIENTS = [
    ("upaep", "UPAEP"),
    ("protecnologia", "Protecnología"),
    ("corhos", "Corhos Europea Aloha"),
    ("tapas", "Tapas Bido"),
    ("temporada", "Venta de temporada"),
    ("produccion", "Producción"),
    ("general", "General"),
]


# Semana -> (inicio, fin, meta acumulada $). El ritmo es un SUPUESTO de planeación.
WEEK_INFO = {
    "s1": (date(2026, 11, 16), date(2026, 11, 22), 8000),
    "s2": (date(2026, 11, 23), date(2026, 11, 29), 22000),
    "s3": (date(2026, 11, 30), date(2026, 12, 6), 40000),
    "s4": (date(2026, 12, 7), date(2026, 12, 13), 62000),
    "s5": (date(2026, 12, 14), date(2026, 12, 20), 85000),
    "s6": (date(2026, 12, 21), date(2026, 12, 31), 100000),
}
WEEK_FOCUS = {
    "s1": "Cotizar y mandar propuestas",
    "s2": "Seguimiento y primeros anticipos",
    "s3": "Cerrar pedidos grandes",
    "s4": "Producir y último corte de pedidos",
    "s5": "Terminar, empacar y entregar",
    "s6": "Venta de último momento y cobro",
}
CLIENT_BASIS = {
    "upaep": "Mayoreo institucional (30%). Prospecto grande con pedido único; se estimó por volumen de piezas.",
    "protecnologia": "Mayoreo técnico (15%). Piezas y promocionales; puede volverse recurrente.",
    "corhos": "Mayoreo regalos de fin de año (15%). Temporada alta para obsequios corporativos.",
    "tapas": "Proyecto propio (10%). Se limita porque aún es prototipo/preventa y no debe quitar producción al mayoreo.",
    "temporada": "Venta directa navideña (30%). Es el colchón: cubre si un cliente grande se retrasa.",
}
PLAN_BASIS = [
    ("Meta $100,000",
     "Es tu objetivo. Debe cubrir costos de producción, el salario del trabajador ($6,000–8,000 al mes) y tu utilidad.",
     "dato"),
    ("Referencia de 71 funkos",
     "71 funkos × $1,400 = $99,400. Es la base de cálculo que tú diste; el plan usa 'funko-equivalente' para convertir otros productos.",
     "dato"),
    ("Reparto por cliente",
     "60% a los tres prospectos de mayoreo (UPAEP 30K, Protecnología 15K, Corhos 15K), 10% Tapas Bido y 30% venta de temporada. "
     "Es un supuesto: se prioriza mayoreo porque cierra volumen con pocos clientes. Ajústalo con cotizaciones reales.",
     "supuesto"),
    ("Metas por semana",
     "8K, 22K, 40K, 62K, 85K, 100K acumulado. Supuesto de ritmo: primero se cotiza y se cobra anticipo (50%), "
     "luego se cierra y se produce. Equivale a vender entre $14K y $23K por semana.",
     "supuesto"),
    ("Capacidad de producción",
     "Con 2 impresoras A1 y un trabajador, se asumió 11–12 funkos-equivalentes por semana. Aún no está medido: "
     "confírmalo con tus tiempos reales de impresión y pintura.",
     "supuesto"),
    ("Corte del 13 de diciembre",
     "Después de esa fecha no se aceptan pedidos nuevos personalizados para alcanzar a entregar antes de Navidad.",
     "supuesto"),
]


class CreartStrategyItem(models.Model):
    _name = "creart.strategy.item"
    _description = "Plan estratégico CreArt: acción o meta"
    _inherit = ["mail.thread"]
    _order = "week, sequence, date_deadline, id"

    name = fields.Char(string="Acción / Meta", required=True, tracking=True)
    kind = fields.Selection(
        [("action", "Acción"), ("goal", "Meta por cliente")],
        string="Tipo", default="action", required=True,
    )
    week = fields.Selection(WEEKS, string="Semana", default="s1", required=True, tracking=True)
    client = fields.Selection(CLIENTS, string="Cliente / canal", default="general", tracking=True)
    state = fields.Selection(
        [("todo", "Por hacer"), ("doing", "En curso"), ("done", "Hecho")],
        string="Estado", default="todo", required=True, tracking=True,
    )
    priority = fields.Selection(
        [("0", "Normal"), ("1", "Alta")], string="Prioridad", default="0",
    )
    date_deadline = fields.Date(string="Fecha límite", tracking=True)
    user_id = fields.Many2one("res.users", string="Responsable")
    lead_id = fields.Many2one("crm.lead", string="Oportunidad relacionada", domain="[('type', '=', 'opportunity')]")
    description = fields.Html(string="Detalle")
    sequence = fields.Integer(default=10)
    color = fields.Integer(string="Color")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id")
    amount_goal = fields.Monetary(string="Meta ($)", currency_field="currency_id", tracking=True)
    amount_done = fields.Monetary(string="Vendido ($)", currency_field="currency_id", tracking=True)
    progress = fields.Float(string="Avance (%)", compute="_compute_progress", store=True)
    overdue = fields.Boolean(string="Vencida", compute="_compute_overdue")

    @api.depends("amount_goal", "amount_done")
    def _compute_progress(self):
        for item in self:
            item.progress = (item.amount_done / item.amount_goal * 100.0) if item.amount_goal else 0.0

    @api.depends("date_deadline", "state")
    def _compute_overdue(self):
        today = fields.Date.context_today(self)
        for item in self:
            item.overdue = bool(
                item.kind == "action" and item.state != "done"
                and item.date_deadline and item.date_deadline < today
            )

    @api.model
    def get_plan_view(self, today=None):
        """Datos simples para la app: meta, semana actual, clientes y acciones de la semana."""
        today = today or fields.Date.context_today(self)
        items = self.search([])
        goals = items.filtered(lambda i: i.kind == "goal").sorted("sequence")
        actions = items.filtered(lambda i: i.kind == "action")
        total_goal = sum(goals.mapped("amount_goal"))
        sold = sum(goals.mapped("amount_done"))
        week = "s1"
        for key, (start, end, _target) in WEEK_INFO.items():
            if today >= start:
                week = key
            if start <= today <= end:
                break
        start, end, target = WEEK_INFO[week]
        days_left = max((date(2026, 12, 31) - today).days, 0)
        expected = target if today >= end else None
        weeks = []
        for key, (w_start, w_end, w_target) in WEEK_INFO.items():
            w_actions = actions.filtered(lambda a, k=key: a.week == k).sorted(
                lambda a: (a.state == "done", -int(a.priority), a.date_deadline or date.max))
            weeks.append({
                "key": key, "start": w_start, "end": w_end, "target": w_target,
                "focus": WEEK_FOCUS[key], "actions": w_actions,
                "done": len(w_actions.filtered(lambda a: a.state == "done")),
                "total": len(w_actions), "current": key == week,
            })
        return {
            "goal": total_goal, "sold": sold,
            "percent": round(sold / total_goal * 100) if total_goal else 0,
            "week": week, "week_target": target, "week_start": start, "week_end": end,
            "week_focus": WEEK_FOCUS[week], "days_left": days_left,
            "behind": max(target - sold, 0) if today >= start else 0,
            "clients": goals, "weeks": weeks,
            "client_basis": CLIENT_BASIS, "basis": PLAN_BASIS,
            "overdue": actions.filtered("overdue"),
        }

    @api.model
    def get_plan_summary(self):
        """Resumen para el Tablero CreArt: meta, vendido y avance de acciones."""
        items = self.search([])
        goals = items.filtered(lambda i: i.kind == "goal")
        actions = items.filtered(lambda i: i.kind == "action")
        goal = sum(goals.mapped("amount_goal"))
        sold = sum(goals.mapped("amount_done"))
        return {
            "goal": goal,
            "sold": sold,
            "percent": round(sold / goal * 100) if goal else 0,
            "actions_total": len(actions),
            "actions_done": len(actions.filtered(lambda i: i.state == "done")),
            "actions_overdue": len(actions.filtered("overdue")),
        }
