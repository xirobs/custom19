# -*- coding: utf-8 -*-
"""Datos del tablero de control (cliente OWL) y resumen diario al equipo."""

from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.tools import format_amount

MONTH_LABELS = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
TRACKED_CURRENCIES = ("MXN", "USD", "UDI")


class InsuranceControlDashboard(models.AbstractModel):
    _name = "insurance.control.dashboard"
    _description = "Tablero de control de seguros"

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    @api.model
    def _month_range(self, year, month):
        start = date(year, month, 1)
        return start, start + relativedelta(months=1)

    @api.model
    def _to_company(self, amount, currency, day):
        company = self.env.company
        if not currency or currency == company.currency_id or not amount:
            return amount or 0.0
        return currency._convert(amount, company.currency_id, company, day or fields.Date.context_today(self))

    @api.model
    def _delta(self, current, previous):
        if not previous:
            return None if not current else 100.0
        return round((current - previous) / abs(previous) * 100.0, 1)

    @api.model
    def _new_policies(self, start, end):
        return self.env["insurance.policy"].search([
            ("state", "in", ["confirmed", "expired", "done"]),
            ("origin_policy_id", "=", False),
            ("emission_date", ">=", start),
            ("emission_date", "<", end),
        ])

    @api.model
    def _new_customers(self, start, end):
        """Clientes cuya primera póliza se emitió en el periodo."""
        policies = self._new_policies(start, end)
        partners = policies.mapped("partner_id")
        if not partners:
            return partners, 0.0
        earlier = self.env["insurance.policy"].search([
            ("partner_id", "in", partners.ids),
            ("state", "in", ["confirmed", "expired", "done"]),
            ("emission_date", "<", start),
        ]).mapped("partner_id")
        new = partners - earlier
        premium = sum(
            self._to_company(p.policy_amount, p.currency_id, p.emission_date)
            for p in policies.filtered(lambda p: p.partner_id in new)
        )
        return new, premium

    @api.model
    def _prospects(self, start, end):
        return self.env["crm.lead"].search_count([
            ("is_insurance_opportunity", "=", True),
            ("create_date", ">=", fields.Datetime.to_datetime(start)),
            ("create_date", "<", fields.Datetime.to_datetime(end)),
        ])

    @api.model
    def _income(self, start, end):
        data = self.env["insurance.commission"]._read_group(
            [("state", "!=", "cancelled"), ("date", ">=", start), ("date", "<", end)],
            [], ["amount_company:sum"],
        )
        return data[0][0] if data and data[0][0] else 0.0

    @api.model
    def _paid_premium(self, start, end):
        lines = self.env["insurance.installment"].search([
            ("payment_date", ">=", start), ("payment_date", "<", end), ("paid_amount", ">", 0),
        ])
        return sum(self._to_company(l.paid_amount, l.currency_id, l.payment_date) for l in lines)

    @api.model
    def _period_metrics(self, start, end):
        policies = self._new_policies(start, end)
        customers, customer_premium = self._new_customers(start, end)
        return {
            "prospects": self._prospects(start, end),
            "new_policies": len(policies),
            "new_premium": sum(self._to_company(p.policy_amount, p.currency_id, p.emission_date) for p in policies),
            "new_customers": len(customers),
            "new_customers_premium": customer_premium,
            "income": self._income(start, end),
            "paid_premium": self._paid_premium(start, end),
        }

    @api.model
    def _exchange_rates(self):
        company = self.env.company
        today = fields.Date.context_today(self)
        result = []
        Rate = self.env["res.currency.rate"]
        for code in ("USD", "UDI"):
            currency = self.env["res.currency"].with_context(active_test=False).search([("name", "=", code)], limit=1)
            if not currency or currency == company.currency_id:
                continue
            last = Rate.search([
                ("currency_id", "=", currency.id),
                ("company_id", "in", [company.id, False]),
            ], order="name desc", limit=1)
            value = currency._convert(1.0, company.currency_id, company, today, round=False) if last else 0.0
            result.append({
                "code": code,
                "value": round(value, 6),
                "label": (
                    "1 %s = %s %s" % (code, "{:,.6f}".format(value).rstrip("0").rstrip("."), company.currency_id.name)
                    if last else _("%s: sin tipo de cambio registrado") % code
                ),
                "date": fields.Date.to_string(last.name) if last else False,
                "outdated": not last or last.name < today,
            })
        return result

    # ------------------------------------------------------------------
    # API del tablero
    # ------------------------------------------------------------------
    @api.model
    def get_dashboard_data(self, year=None, month=None):
        today = fields.Date.context_today(self)
        year = int(year or today.year)
        month = int(month or today.month)
        company = self.env.company
        Policy = self.env["insurance.policy"]
        Installment = self.env["insurance.installment"]
        Goal = self.env["insurance.goal"]

        start, end = self._month_range(year, month)
        prev_start = start - relativedelta(months=1)
        ly_start = start - relativedelta(years=1)
        current = self._period_metrics(start, end)
        previous = self._period_metrics(prev_start, start)
        last_year = self._period_metrics(ly_start, ly_start + relativedelta(months=1))

        def kpi(key, label, money=False, goal=None):
            return {
                "key": key,
                "label": label,
                "value": current[key],
                "previous": previous[key],
                "last_year": last_year[key],
                "delta_month": self._delta(current[key], previous[key]),
                "delta_year": self._delta(current[key], last_year[key]),
                "money": money,
                "goal": goal,
                "goal_pct": round(current[key] / goal * 100.0, 1) if goal else None,
            }

        kpis = [
            kpi("prospects", _("Prospectos nuevos")),
            kpi("new_customers", _("Clientes nuevos"),
                goal=Goal._get_target(year, month, "new_customers_target") or None),
            kpi("new_policies", _("Pólizas nuevas"),
                goal=Goal._get_target(year, month, "new_policies_target") or None),
            kpi("new_premium", _("Prima vendida"), money=True,
                goal=Goal._get_target(year, month, "premium_target") or None),
            kpi("paid_premium", _("Prima cobrada"), money=True),
            kpi("income", _("Ingresos (comisiones)"), money=True,
                goal=Goal._get_target(year, month, "income_target") or None),
        ]
        weak_points = [
            k["label"] for k in kpis
            if (k["delta_month"] is not None and k["delta_month"] < 0)
            or (k["delta_year"] is not None and k["delta_year"] < 0)
            or (k["goal_pct"] is not None and k["goal_pct"] < 80)
        ]

        # Cartera activa por moneda
        active = Policy.search([("state", "=", "confirmed")])
        portfolio = []
        for code in TRACKED_CURRENCIES:
            pols = active.filtered(lambda p: p.currency_id.name == code)
            portfolio.append({
                "currency": code,
                "count": len(pols),
                "premium": sum(pols.mapped("policy_amount")),
                "insured": sum(pols.mapped("insured_amount")),
            })
        by_ramo = {}
        for policy in active:
            name = policy.policy_type_id.name or _("Sin ramo")
            by_ramo[name] = by_ramo.get(name, 0) + 1

        # Cobranza por moneda y estatus
        collection = []
        for code in TRACKED_CURRENCIES:
            lines = Installment.search([("currency_id.name", "=", code), ("state", "!=", "cancelled")])
            paid_month = lines.filtered(lambda l: l.payment_date and start <= l.payment_date < end)
            collection.append({
                "currency": code,
                "paid": sum(paid_month.mapped("paid_amount")),
                "due_soon": sum(lines.filtered(lambda l: l.collection_status == "due_soon").mapped("balance")),
                "overdue": sum(lines.filtered(lambda l: l.collection_status == "overdue").mapped("balance")),
                "overdue_count": len(lines.filtered(lambda l: l.collection_status == "overdue")),
                "due_soon_count": len(lines.filtered(lambda l: l.collection_status == "due_soon")),
            })

        # Renovaciones
        renew_domain = [("state", "in", ["confirmed", "expired"])]
        renewals = {
            "pending": Policy.search_count(renew_domain + [("renewal_state", "=", "pending")]),
            "in_progress": Policy.search_count(renew_domain + [("renewal_state", "=", "in_progress")]),
            "renewed_month": Policy.search_count([
                ("origin_policy_id", "!=", False),
                ("state", "in", ["confirmed", "expired", "done"]),
                ("coverage_start_date", ">=", start), ("coverage_start_date", "<", end),
            ]),
            "lost": Policy.search_count(renew_domain + [("renewal_state", "=", "lost")]),
        }

        # Atención
        attention = Policy.search(
            [("attention_level", "=", "danger"), ("state", "in", ["confirmed", "expired", "draft"])],
            order="end_date", limit=8,
        )
        if len(attention) < 8:
            attention |= Policy.search(
                [("attention_level", "=", "warning"), ("state", "in", ["confirmed", "expired", "draft"])],
                order="end_date", limit=8 - len(attention),
            )
        attention_list = [{
            "id": p.id,
            "name": p.name,
            "partner": p.partner_id.name,
            "reason": p.attention_reason,
            "ramo": p.policy_type_id.name or "",
            "level": p.attention_level,
        } for p in attention]

        # Agenda
        Task = self.env["insurance.task"]
        week_end = today + relativedelta(days=6 - today.weekday())
        agenda = {
            "today": Task.search_count([("state", "=", "todo"), ("date_deadline", "=", today)]),
            "overdue": Task.search_count([("state", "=", "todo"), ("date_deadline", "<", today)]),
            "week": Task.search_count([("state", "=", "todo"), ("date_deadline", ">=", today), ("date_deadline", "<=", week_end)]),
            "activities_overdue": self.env["mail.activity"].search_count([
                ("res_model", "in", ["insurance.policy", "insurance.installment", "insurance.claim", "insurance.task", "crm.lead"]),
                ("date_deadline", "<", today),
            ]),
            "birthdays": len(self.env["res.partner"].search([
                ("is_insurance_customer", "=", True), ("mx_birth_date", "!=", False),
            ]).filtered(lambda p: (p.mx_birth_date.month, p.mx_birth_date.day) == (today.month, today.day))),
        }

        # Series de 12 meses: ingresos año actual vs anterior vs meta; tendencia
        income_series, income_prev, goals, trend_policies, trend_prospects, trend_customers = [], [], [], [], [], []
        for m in range(1, 13):
            s, e = self._month_range(year, m)
            ps, pe = self._month_range(year - 1, m)
            income_series.append(round(self._income(s, e), 2) if s <= today else None)
            income_prev.append(round(self._income(ps, pe), 2))
            goals.append(round(Goal._get_target(year, m, "income_target"), 2))
        for i in range(11, -1, -1):
            s = start - relativedelta(months=i)
            e = s + relativedelta(months=1)
            trend_policies.append(len(self._new_policies(s, e)))
            trend_prospects.append(self._prospects(s, e))
            trend_customers.append(len(self._new_customers(s, e)[0]))
        trend_labels = [
            "%s %s" % (MONTH_LABELS[(start - relativedelta(months=i)).month - 1], str((start - relativedelta(months=i)).year)[2:])
            for i in range(11, -1, -1)
        ]
        year_income = sum(v for v in income_series if v)
        year_goal = Goal._get_target(year, None, "income_target")

        return {
            "year": year,
            "month": month,
            "month_label": "%s %s" % (MONTH_LABELS[month - 1], year),
            "currency": company.currency_id.name,
            "currency_symbol": company.currency_id.symbol,
            "kpis": kpis,
            "weak_points": weak_points,
            "portfolio": portfolio,
            "active_total": len(active),
            "by_ramo": [{"name": k, "count": v} for k, v in sorted(by_ramo.items(), key=lambda kv: -kv[1])],
            "collection": collection,
            "renewals": renewals,
            "attention": attention_list,
            "attention_counts": {
                "danger": Policy.search_count([("attention_level", "=", "danger"), ("state", "in", ["confirmed", "expired"])]),
                "warning": Policy.search_count([("attention_level", "=", "warning"), ("state", "in", ["confirmed", "expired"])]),
            },
            "agenda": agenda,
            "rates": self._exchange_rates(),
            "income_chart": {
                "labels": MONTH_LABELS,
                "current": income_series,
                "previous": income_prev,
                "goal": goals,
            },
            "year_income": round(year_income, 2),
            "year_income_prev": round(sum(income_prev), 2),
            "year_goal": round(year_goal, 2),
            "trend_chart": {
                "labels": trend_labels,
                "policies": trend_policies,
                "prospects": trend_prospects,
                "customers": trend_customers,
            },
        }

    # ------------------------------------------------------------------
    # Resumen diario al canal del equipo
    # ------------------------------------------------------------------
    @api.model
    def _cron_daily_digest(self):
        if not self.env["ir.config_parameter"].sudo().get_param("insurance_management.daily_digest"):
            return
        channel = self.env.ref("insurance_management.channel_insurance_alerts", raise_if_not_found=False)
        if not channel:
            return
        data = self.get_dashboard_data()
        rates = "".join(
            "<li>%s%s</li>" % (r["label"], _(" — <b>sin actualizar hoy</b>") if r["outdated"] else "")
            for r in data["rates"]
        ) or "<li>%s</li>" % _("Sin tipos de cambio registrados")
        collection = "".join(
            "<li>%s: vencido %s (%s cuotas), por vencer %s</li>" % (
                c["currency"], "{:,.2f}".format(c["overdue"]), c["overdue_count"], "{:,.2f}".format(c["due_soon"]),
            )
            for c in data["collection"] if c["overdue"] or c["due_soon"]
        ) or "<li>%s</li>" % _("Sin cobranza pendiente")
        body = _(
            "<p><b>Resumen del día</b></p>"
            "<p>Tipos de cambio:</p><ul>%(rates)s</ul>"
            "<p>Cobranza:</p><ul>%(collection)s</ul>"
            "<p>Renovaciones: %(pending)s por renovar, %(progress)s en trámite, %(lost)s no renovadas.</p>"
            "<p>Atención urgente: %(danger)s pólizas · Tareas de hoy: %(today)s · Tareas atrasadas: %(overdue)s · "
            "Cumpleaños hoy: %(birthdays)s</p>"
        ) % {
            "rates": rates,
            "collection": collection,
            "pending": data["renewals"]["pending"],
            "progress": data["renewals"]["in_progress"],
            "lost": data["renewals"]["lost"],
            "danger": data["attention_counts"]["danger"],
            "today": data["agenda"]["today"],
            "overdue": data["agenda"]["overdue"],
            "birthdays": data["agenda"]["birthdays"],
        }
        from markupsafe import Markup
        channel.message_post(body=Markup(body), message_type="comment", subtype_xmlid="mail.mt_comment")
