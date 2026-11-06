"""Indicadores y pronóstico de CreArt 3DP (tablero de la app del equipo)."""

from collections import defaultdict
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

# Probabilidad de cierre por etapa abierta. Se usa en lugar de la probabilidad
# automática de CRM, que con pocos datos históricos no es confiable.
STAGE_WEIGHT = {
    "Solicitud": 0.10,
    "Conceptualización/Diseño": 0.25,
    "Propuesta enviada": 0.40,
    "Negociación / Seguimiento": 0.60,
}
SOLD_PROGRESS = 45  # Desde "Ganada/Pendiente pago"
MONTH_NAMES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
PAUSED_STAGE = "Bloqueado/Pausado"


class CreartKpi(models.AbstractModel):
    _name = "creart.kpi"
    _description = "Indicadores CreArt 3DP"

    @api.model
    def _lead_amount(self, lead):
        if lead.creart_no_charge:
            return 0.0
        return lead.selected_total_price or lead.expected_revenue or 0.0

    @api.model
    def _lead_sold_date(self, lead):
        value = lead.date_closed or lead.order_date or lead.create_date
        return fields.Date.to_date(value)

    @api.model
    def _is_sold(self, lead, paused):
        if lead.creart_no_charge or lead.won_status == "lost":
            return False
        if lead.won_status == "won":
            return True
        return lead.active and lead.stage_progress >= SOLD_PROGRESS and lead.stage_id != paused

    @api.model
    def _forecast(self, values):
        """Regresión lineal simple sobre meses completos, sin valores negativos."""
        count = len(values)
        if not count:
            return [0.0, 0.0, 0.0]
        mean_x = (count - 1) / 2
        mean_y = sum(values) / count
        denominator = sum((x - mean_x) ** 2 for x in range(count))
        slope = (
            sum((x - mean_x) * (y - mean_y) for x, y in enumerate(values)) / denominator
            if denominator else 0.0
        )
        # El mes en curso es x = count; se proyectan los tres siguientes.
        return [max(0.0, mean_y + slope * (count + k - mean_x)) for k in (1, 2, 3)]

    @api.model
    def _confidence(self, months, sold_count):
        if months < 6 or sold_count < 10:
            return "baja"
        return "media" if months < 12 else "alta"

    @api.model
    def get_dashboard(self, company):
        Lead = self.env["crm.lead"].sudo().with_context(active_test=False)
        leads = Lead.search([
            ("type", "=", "opportunity"),
            ("company_id", "in", [company.id, False]),
        ])
        paused = self.env.ref("crm_extended_3D.crm_stage_bloqueado", raise_if_not_found=False)
        today = fields.Date.context_today(self)
        month_start = date(today.year, today.month, 1)

        sold = leads.filtered(lambda l: self._is_sold(l, paused))
        lost = leads.filtered(lambda l: l.won_status == "lost")
        active = leads.filtered("active")
        open_leads = active.filtered(
            lambda l: l.won_status != "won" and l.stage_progress < SOLD_PROGRESS and l.stage_id != paused
        )

        by_month = defaultdict(float)
        first_sale = None
        for lead in sold:
            sold_on = self._lead_sold_date(lead)
            if not sold_on:
                continue
            key = (sold_on.year, sold_on.month)
            by_month[key] += self._lead_amount(lead)
            first_sale = min(first_sale, sold_on) if first_sale else sold_on

        history = []
        for back in range(6, -1, -1):
            month = month_start - relativedelta(months=back)
            history.append({
                "label": MONTH_NAMES[month.month - 1],
                "value": by_month[(month.year, month.month)],
                "kind": "current" if back == 0 else "past",
            })

        complete = []
        if first_sale:
            cursor = date(first_sale.year, first_sale.month, 1)
            while cursor < month_start:
                complete.append(by_month[(cursor.year, cursor.month)])
                cursor += relativedelta(months=1)
        projection = self._forecast(complete)
        future = [{
            "label": MONTH_NAMES[(month_start + relativedelta(months=k + 1)).month - 1],
            "value": value,
            "kind": "forecast",
        } for k, value in enumerate(projection)]
        bars = history + future
        peak = max([bar["value"] for bar in bars] + [1.0])
        for bar in bars:
            bar["height"] = round(bar["value"] / peak * 100)

        open_value = sum(self._lead_amount(l) for l in open_leads)
        weighted = sum(
            self._lead_amount(l) * STAGE_WEIGHT.get(self.env["crm.lead"]._creart_stage_key(l.stage_id), 0.0)
            for l in open_leads
        )
        sold_amounts = [self._lead_amount(l) for l in sold if self._lead_amount(l)]
        current = by_month[(month_start.year, month_start.month)]
        previous_month = month_start - relativedelta(months=1)
        previous = by_month[(previous_month.year, previous_month.month)]
        closed = len(sold) + len(lost)
        cycle = [
            (fields.Date.to_date(l.date_closed) - fields.Date.to_date(l.create_date)).days
            for l in sold if l.date_closed and l.create_date
        ]
        margins = [l.margin_percent for l in sold if l.margin_percent]
        in_production = active.filtered(
            lambda l: SOLD_PROGRESS <= l.stage_progress < 100 and l.won_status != "won"
        )
        receivable = sum(
            max(self._lead_amount(l) - (l.advance_payment or 0.0), 0.0)
            for l in sold if l.active
        )

        stage_rows = []
        stages = self.env["crm.stage"].sudo().search([], order="sequence")
        counts = defaultdict(int)
        for lead in active:
            counts[lead.stage_id.id] += 1
        top = max(list(counts.values()) + [1])
        for stage in stages:
            if counts[stage.id]:
                stage_rows.append({
                    "name": stage.name,
                    "count": counts[stage.id],
                    "width": round(counts[stage.id] / top * 100),
                })

        return {
            "cards": {
                "sold_month": current,
                "sold_delta": current - previous,
                "open_jobs": len(open_leads),
                "open_value": open_value,
                "weighted": weighted,
                "average_ticket": sum(sold_amounts) / len(sold_amounts) if sold_amounts else 0.0,
                "conversion": round(len(sold) / closed * 100) if closed else 0,
                "cycle_days": round(sum(cycle) / len(cycle)) if cycle else 0,
                "average_margin": round(sum(margins) / len(margins)) if margins else 0,
                "receivable": receivable,
                "in_production": len(in_production),
                "overdue": len(in_production.filtered(lambda l: l.delivery_date and l.delivery_date < today)),
                "unpriced": len(active.filtered(lambda l: not self._lead_amount(l) and not l.creart_no_charge)),
                "shortage": len(active.filtered("material_shortage")),
            },
            "bars": bars,
            "forecast_total": sum(projection),
            "confidence": self._confidence(len(complete), len(sold)),
            "months_of_data": len(complete),
            "stages": stage_rows,
        }
