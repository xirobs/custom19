"""App del equipo CreArt 3DP: portal instalable (PWA) para celular y computadora."""

import json
from collections import OrderedDict
from datetime import datetime, timedelta

import pytz
from werkzeug.exceptions import NotFound

from odoo import fields, http, _
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

from odoo.addons.crm_extended_3D.hooks import CREART_STAGE_XMLID_BY_NAME
from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.addons.portal.controllers.web import Home

TEAM_GROUP = "crm_extended_3D.group_creart_portal_team"
APP_URL = "/creart/app"
DEFAULT_TZ = "America/Mexico_City"
TEXT_FIELDS = ("name", "contact_name", "partner_name", "phone", "email_from", "filament_color")
FLOAT_FIELDS = (
    "expected_revenue", "advance_payment", "filament_grams", "filament_waste_percent",
    "mo_design_hours", "mo_concept_hours", "mo_postprocess_hours", "mo_operation_hours",
)
INT_FIELDS = (
    "units", "print_hours", "print_time_minutes", "print_time_seconds",
    "dead_time_hours", "dead_time_minutes", "dead_time_seconds",
)
PAGE_SIZE = 40
STEP_NEXT = {"pending": "in_progress", "in_progress": "done", "done": "pending"}
TASK_CLOSED = ("1_done", "1_canceled")
WEEKDAYS = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
WEEKDAYS_LONG = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def is_team_user(user=None):
    """Equipo habilitado: cualquier usuario (portal o interno) con el grupo de la app."""
    user = user or request.env.user
    return bool(user) and not user._is_public() and user.has_group(TEAM_GROUP)


def lands_in_app(user):
    """El equipo entra directo a la app; los administradores conservan Odoo completo."""
    return is_team_user(user) and not user.has_group("base.group_system")


class CreartHome(Home):
    """El equipo habilitado entra directo a la app al iniciar sesión (celular o computadora)."""

    def _login_redirect(self, uid, redirect=None):
        if not redirect:
            user = request.env["res.users"].sudo().browse(uid)
            if lands_in_app(user):
                redirect = APP_URL
        return super()._login_redirect(uid, redirect=redirect)

    def web_client(self, s_action=None, **kw):
        """/odoo sin acción lleva al equipo a la app (los enlaces directos siguen funcionando)."""
        uid = request.session.uid
        path = request.httprequest.path.rstrip("/")
        if uid and path in ("/odoo", "/web") and not kw.get("debug") and not kw.get("odoo"):
            user = request.env["res.users"].sudo().browse(uid)
            if lands_in_app(user):
                return request.redirect(APP_URL)
        return super().web_client(s_action=s_action, **kw)


class CreartPortalHome(CustomerPortal):
    """/my lleva al equipo a la app en lugar del portal estándar."""

    def home(self, **kw):
        if is_team_user():
            return request.redirect(APP_URL)
        return super().home(**kw)


class CreartApp(http.Controller):

    # ------------------------------------------------------------------
    # Apoyo
    # ------------------------------------------------------------------
    def _guard(self):
        """La app es para el equipo habilitado (y administradores, para revisarla)."""
        user = request.env.user
        if is_team_user(user) or user.has_group("base.group_system"):
            return None
        return request.redirect("/odoo" if not user.share else "/my")

    def _company(self):
        return request.env.company

    def _tz(self):
        return pytz.timezone(request.env.user.tz or DEFAULT_TZ)

    def _local(self, value):
        """Datetime UTC (naive) -> hora local del usuario (naive)."""
        if not value:
            return None
        return pytz.utc.localize(value).astimezone(self._tz()).replace(tzinfo=None)

    def _to_utc(self, value):
        """Hora local (naive) -> UTC naive para guardar."""
        return self._tz().localize(value).astimezone(pytz.utc).replace(tzinfo=None)

    def _today(self):
        return self._local(fields.Datetime.now()).date()

    def _lead_domain(self):
        return [("type", "=", "opportunity"), ("company_id", "in", [self._company().id, False])]

    def _leads(self):
        return request.env["crm.lead"].sudo().with_context(active_test=False)

    def _get_lead(self, lead_id):
        lead = self._leads().search(self._lead_domain() + [("id", "=", lead_id)], limit=1)
        if not lead:
            raise NotFound()
        return lead

    def _stages(self):
        stages = request.env["crm.stage"].sudo()
        for xmlid in CREART_STAGE_XMLID_BY_NAME.values():
            stages |= request.env.ref(f"crm_extended_3D.{xmlid}", raise_if_not_found=False) or stages.browse()
        return stages.sorted("sequence")

    @staticmethod
    def _money(value):
        return "$ {:,.0f}".format(value or 0)

    @staticmethod
    def _num(value, digits=2):
        value = value or 0
        if float(value).is_integer():
            return "{:,.0f}".format(value)
        return ("{:,.%df}" % digits).format(value)

    @staticmethod
    def _date(value):
        return value.strftime("%d/%m/%Y") if value else "—"

    def _short_date(self, value):
        if not value:
            return "—"
        if isinstance(value, datetime):
            value = self._local(value).date()
        return f"{value.day} {MONTHS[value.month - 1]}"

    def _hour(self, value):
        local = self._local(value)
        return local.strftime("%H:%M") if local else ""

    def _initials(self, name):
        parts = [p for p in (name or "").split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or "C"

    def _render(self, template, active, **values):
        user = request.env.user
        values.update({
            "active_tab": active,
            "money": self._money,
            "num": self._num,
            "fmt_date": self._date,
            "short_date": self._short_date,
            "hour": self._hour,
            "csrf": request.csrf_token(),
            "app_user": user,
            "user_initials": self._initials(user.name),
            "is_internal": not user.share,
            "flash": request.params.get("ok"),
        })
        return request.render(f"crm_extended_3D.{template}", values)

    def _values_from_post(self, post):
        vals = {key: (post.get(key) or "").strip() for key in TEXT_FIELDS if key in post}
        if "name" in vals and not vals["name"]:
            raise ValidationError(_("Escribe el nombre del trabajo."))
        for key in FLOAT_FIELDS:
            if key in post:
                try:
                    vals[key] = float(post.get(key) or 0)
                except ValueError as err:
                    raise ValidationError(_("Revisa los números capturados.")) from err
        for key in INT_FIELDS:
            if key in post:
                try:
                    vals[key] = int(float(post.get(key) or 0))
                except ValueError as err:
                    raise ValidationError(_("Revisa los números capturados.")) from err
        if "description" in post:
            vals["description"] = post.get("description") or False
        if post.get("priority") in ("0", "1", "2", "3"):
            vals["priority"] = post["priority"]
        if "payment_method" in post:
            allowed = dict(request.env["crm.lead"]._fields["payment_method"].selection)
            method = post.get("payment_method")
            vals["payment_method"] = method if method in allowed else False
        if post.get("order_date"):
            try:
                vals["order_date"] = datetime.strptime(post["order_date"], "%Y-%m-%d").date()
            except ValueError as err:
                raise ValidationError(_("La fecha no es válida.")) from err
        if post.get("quote_form"):
            vals["creart_no_charge"] = bool(post.get("creart_no_charge"))
        if "filament_id" in post:
            product = False
            if post.get("filament_id"):
                product = request.env["product.product"].sudo().browse(int(post["filament_id"])).exists()
            vals["filament_id"] = product.id if product else False
        stage_id = int(post.get("stage_id") or 0)
        if stage_id and stage_id in self._stages().ids:
            vals["stage_id"] = stage_id
        return vals

    def _filaments(self):
        """Materiales ya usados en trabajos: evita listar todo el catálogo."""
        used = request.env["crm.lead"].sudo().with_context(active_test=False).search(
            [("filament_id", "!=", False)]
        ).mapped("filament_id")
        return used.sorted("name")

    def _accessory_products(self):
        Product = request.env["product.product"].sudo()
        used = request.env["crm.print.accessory.line"].sudo().search([]).mapped("product_id")
        others = Product.search([("type", "=", "consu"), ("id", "not in", used.ids)], limit=200, order="name")
        return (used | others).sorted("name")

    def _form_values(self, lead=None, error=None, post=None):
        return {
            "lead": lead,
            "error": error,
            "post": post or {},
            "stages": self._stages(),
            "payment_methods": request.env["crm.lead"]._fields["payment_method"].selection,
            "filaments": self._filaments(),
        }

    def _open_tasks(self, start, end):
        """Tareas de planificación (producción) entre dos fechas locales."""
        return request.env["project.task"].sudo().search([
            ("lead_id", "!=", False),
            ("state", "not in", TASK_CLOSED),
            ("planned_date_start", "<", self._to_utc(end)),
            ("planned_date_end", ">=", self._to_utc(start)),
        ], order="planned_date_start asc")

    def _group_by_day(self, records, field_name, start, days):
        groups = OrderedDict()
        for offset in range(days):
            day = start + timedelta(days=offset)
            groups[day] = []
        for rec in records:
            value = rec[field_name]
            if isinstance(value, datetime):
                value = self._local(value).date()
            if value in groups:
                groups[value].append(rec)
        return groups

    def _day_label(self, day):
        today = self._today()
        if day == today:
            return "Hoy"
        if day == today + timedelta(days=1):
            return "Mañana"
        return f"{WEEKDAYS_LONG[day.weekday()]} {day.day} {MONTHS[day.month - 1]}"

    def _step_progress(self, lead):
        steps = lead.print_process_step_ids
        if not steps:
            return 0
        return round(len(steps.filtered(lambda s: s.state == "done")) / len(steps) * 100)

    # ------------------------------------------------------------------
    # Inicio
    # ------------------------------------------------------------------
    @http.route(APP_URL, type="http", auth="user", sitemap=False)
    def dashboard(self, **kw):
        if (blocked := self._guard()):
            return blocked
        data = request.env["creart.kpi"].sudo().get_dashboard(self._company())
        today = self._today()
        tasks_today = self._open_tasks(datetime.combine(today, datetime.min.time()),
                                       datetime.combine(today + timedelta(days=1), datetime.min.time()))
        deliveries = self._leads().search(self._lead_domain() + [
            ("active", "=", True), ("delivery_date", ">=", today),
            ("delivery_date", "<=", today + timedelta(days=14)),
        ], order="delivery_date asc", limit=6)
        events = request.env["calendar.event"].sudo().search([
            ("start", ">=", self._to_utc(datetime.combine(today, datetime.min.time()))),
            ("start", "<", self._to_utc(datetime.combine(today + timedelta(days=7), datetime.min.time()))),
        ], order="start asc", limit=5)
        hour = self._local(fields.Datetime.now()).hour
        greeting = "Buenos días" if hour < 12 else ("Buenas tardes" if hour < 19 else "Buenas noches")
        return self._render(
            "app_dashboard", "inicio", data=data, cards=data["cards"], tasks_today=tasks_today,
            deliveries=deliveries, events=events, greeting=greeting, today=today,
            day_label=self._day_label,
        )

    # ------------------------------------------------------------------
    # Trabajos (oportunidades 3D)
    # ------------------------------------------------------------------
    @http.route(f"{APP_URL}/trabajos", type="http", auth="user", sitemap=False)
    def jobs(self, q="", etapa=None, archivados=None, vista=None, **kw):
        if (blocked := self._guard()):
            return blocked
        domain = self._lead_domain() + [("active", "=", not archivados)]
        stage_id = int(etapa) if etapa and str(etapa).isdigit() else None
        if stage_id:
            domain.append(("stage_id", "=", stage_id))
        q = (q or "").strip()
        if q:
            domain += [
                "|", "|", "|", ("name", "ilike", q), ("partner_name", "ilike", q),
                ("contact_name", "ilike", q), ("lead_sequence", "ilike", q),
            ]
        board = vista == "tablero" and not archivados
        stages = self._stages()
        if board:
            leads = self._leads().search(domain, order="priority desc, id desc", limit=300)
            columns = [(stage, leads.filtered(lambda l, s=stage: l.stage_id == s)) for stage in stages]
            total = len(leads)
        else:
            leads = self._leads().search(domain, limit=PAGE_SIZE, order="priority desc, id desc")
            columns = []
            total = self._leads().search_count(domain)
        return self._render(
            "app_jobs", "trabajos", leads=leads, q=q, stage_id=stage_id, stages=stages,
            archived=bool(archivados), total=total, page_size=PAGE_SIZE, board=board, columns=columns,
        )

    @http.route(f"{APP_URL}/trabajos/nuevo", type="http", auth="user", methods=["GET", "POST"], sitemap=False)
    def job_new(self, **post):
        if (blocked := self._guard()):
            return blocked
        error = None
        if request.httprequest.method == "POST":
            try:
                vals = self._values_from_post(post)
                vals.pop("stage_id", None)
                vals.update({"type": "opportunity", "company_id": self._company().id})
                if request.env.user.share is False:
                    vals.setdefault("user_id", request.env.user.id)
                lead = self._leads().create(vals)
                return request.redirect(f"{APP_URL}/trabajos/{lead.id}?ok=creado")
            except (UserError, ValidationError) as err:
                request.env.cr.rollback()
                error = str(err.args[0])
        return self._render("app_job_new", "nuevo", **self._form_values(error=error, post=post))

    @http.route(f"{APP_URL}/trabajos/<int:lead_id>", type="http", auth="user", methods=["GET", "POST"], sitemap=False)
    def job_detail(self, lead_id, seccion="resumen", **post):
        if (blocked := self._guard()):
            return blocked
        lead = self._get_lead(lead_id)
        error = None
        if request.httprequest.method == "POST":
            try:
                lead.write(self._values_from_post(post))
                return request.redirect(f"{APP_URL}/trabajos/{lead.id}?seccion={seccion}&ok=guardado")
            except (UserError, ValidationError) as err:
                request.env.cr.rollback()
                error = str(err.args[0])
        notes = lead.message_ids.filtered(lambda m: m.message_type == "comment" and m.body)[:15]
        tasks = lead.planning_task_ids.sorted(lambda t: t.planned_date_start or datetime.max)
        return self._render(
            "app_job_form", "trabajos", notes=notes, tasks=tasks, section=seccion,
            step_progress=self._step_progress(lead),
            accessory_products=self._accessory_products(),
            **self._form_values(lead=lead, error=error, post=post if error else None),
        )

    def _lead_post(self, lead_id, section="resumen", msg="guardado"):
        return request.redirect(f"{APP_URL}/trabajos/{lead_id}?seccion={section}&ok={msg}")

    @http.route(f"{APP_URL}/trabajos/<int:lead_id>/etapa/<int:stage_id>", type="http", auth="user", methods=["POST"], sitemap=False)
    def job_stage(self, lead_id, stage_id, **post):
        if (blocked := self._guard()):
            return blocked
        lead = self._get_lead(lead_id)
        if stage_id in self._stages().ids:
            lead.write({"stage_id": stage_id})
        return self._lead_post(lead_id, post.get("seccion") or "resumen", "etapa")

    @http.route(f"{APP_URL}/trabajos/<int:lead_id>/archivar", type="http", auth="user", methods=["POST"], sitemap=False)
    def job_archive(self, lead_id, **post):
        if (blocked := self._guard()):
            return blocked
        self._get_lead(lead_id).write({"active": False})
        return request.redirect(f"{APP_URL}/trabajos?ok=archivado")

    @http.route(f"{APP_URL}/trabajos/<int:lead_id>/restaurar", type="http", auth="user", methods=["POST"], sitemap=False)
    def job_restore(self, lead_id, **post):
        if (blocked := self._guard()):
            return blocked
        self._get_lead(lead_id).write({"active": True})
        return self._lead_post(lead_id, msg="restaurado")

    @http.route(f"{APP_URL}/trabajos/<int:lead_id>/nota", type="http", auth="user", methods=["POST"], sitemap=False)
    def job_note(self, lead_id, nota="", **post):
        if (blocked := self._guard()):
            return blocked
        nota = (nota or "").strip()
        if nota:
            self._get_lead(lead_id).message_post(
                body=nota, message_type="comment", subtype_xmlid="mail.mt_note",
                author_id=request.env.user.partner_id.id,
            )
        return self._lead_post(lead_id, "notas", "nota")

    @http.route(f"{APP_URL}/trabajos/<int:lead_id>/paso/<int:step_id>", type="http", auth="user", methods=["POST"], sitemap=False)
    def job_step(self, lead_id, step_id, **post):
        if (blocked := self._guard()):
            return blocked
        lead = self._get_lead(lead_id)
        step = lead.print_process_step_ids.filtered(lambda s: s.id == step_id)
        if not step:
            raise NotFound()
        new_state = STEP_NEXT[step.state]
        vals = {"state": new_state}
        now = fields.Datetime.now()
        if new_state == "in_progress" and not step.date_start:
            vals["date_start"] = now
        if new_state == "done":
            vals["date_done"] = now
        step.write(vals)
        back = post.get("volver")
        if back == "produccion":
            return request.redirect(f"{APP_URL}/produccion?ok=paso")
        return self._lead_post(lead_id, "produccion", "paso")

    @http.route(f"{APP_URL}/trabajos/<int:lead_id>/pasos", type="http", auth="user", methods=["POST"], sitemap=False)
    def job_default_steps(self, lead_id, **post):
        if (blocked := self._guard()):
            return blocked
        self._get_lead(lead_id).action_create_default_process_steps()
        return self._lead_post(lead_id, "produccion", "pasos")

    @http.route(f"{APP_URL}/trabajos/<int:lead_id>/planificar", type="http", auth="user", methods=["POST"], sitemap=False)
    def job_plan(self, lead_id, **post):
        if (blocked := self._guard()):
            return blocked
        lead = self._get_lead(lead_id)
        try:
            if lead.project_id:
                lead.action_resync_planning_project()
            else:
                lead.action_create_planning_project()
        except (UserError, ValidationError) as err:
            request.env.cr.rollback()
            return request.redirect(f"{APP_URL}/trabajos/{lead_id}?seccion=produccion&ok=error_plan")
        return self._lead_post(lead_id, "produccion", "planificado")

    @http.route(f"{APP_URL}/trabajos/<int:lead_id>/aplicar-precio", type="http", auth="user", methods=["POST"], sitemap=False)
    def job_apply_price(self, lead_id, **post):
        if (blocked := self._guard()):
            return blocked
        self._get_lead(lead_id).action_apply_quote_to_expected_revenue()
        return self._lead_post(lead_id, "cotizar", "precio")

    @http.route(f"{APP_URL}/trabajos/<int:lead_id>/accesorio", type="http", auth="user", methods=["POST"], sitemap=False)
    def job_accessory_add(self, lead_id, **post):
        if (blocked := self._guard()):
            return blocked
        lead = self._get_lead(lead_id)
        product = request.env["product.product"].sudo().browse(int(post.get("product_id") or 0)).exists()
        if product:
            mode = post.get("usage_mode") if post.get("usage_mode") in ("percent", "unit") else "percent"
            try:
                quantity = float(post.get("quantity") or 1)
                percent = float(post.get("usage_percent") or 5)
                price = float(post.get("price_unit")) if post.get("price_unit") else product.standard_price
            except ValueError:
                return self._lead_post(lead_id, "cotizar", "error_num")
            request.env["crm.print.accessory.line"].sudo().create({
                "lead_id": lead.id, "product_id": product.id, "usage_mode": mode,
                "quantity": quantity, "usage_percent": percent, "price_unit": price,
            })
        return self._lead_post(lead_id, "cotizar", "accesorio")

    @http.route(f"{APP_URL}/trabajos/<int:lead_id>/accesorio/<int:line_id>/quitar", type="http", auth="user", methods=["POST"], sitemap=False)
    def job_accessory_remove(self, lead_id, line_id, **post):
        if (blocked := self._guard()):
            return blocked
        lead = self._get_lead(lead_id)
        lead.accessory_ids.filtered(lambda l: l.id == line_id).unlink()
        return self._lead_post(lead_id, "cotizar", "accesorio_quitado")

    # ------------------------------------------------------------------
    # Producción: cola y planificación
    # ------------------------------------------------------------------
    @http.route(f"{APP_URL}/produccion", type="http", auth="user", sitemap=False)
    def production(self, **kw):
        if (blocked := self._guard()):
            return blocked
        Lead = request.env["crm.lead"].sudo()
        queue = Lead._get_ordered_queue_leads().filtered(
            lambda l: l.print_process_step_ids and any(s.state != "done" for s in l.print_process_step_ids)
        )
        rows = []
        for position, lead in enumerate(queue, start=1):
            steps = lead.print_process_step_ids.sorted("sequence")
            current = steps.filtered(lambda s: s.state == "in_progress")[:1] or \
                steps.filtered(lambda s: s.state == "pending")[:1]
            open_tasks = lead.planning_task_ids.filtered(lambda t: t.state not in TASK_CLOSED and t.planned_date_end)
            planned_end = max(open_tasks.mapped("planned_date_end")) if open_tasks else False
            rows.append({
                "position": position, "lead": lead, "current": current,
                "progress": self._step_progress(lead), "planned_end": planned_end,
                "late": bool(lead.delivery_date and planned_end and self._local(planned_end).date() > lead.delivery_date),
            })
        today = self._today()
        start = datetime.combine(today, datetime.min.time())
        tasks = self._open_tasks(start, start + timedelta(days=7))
        agenda = self._group_by_day(tasks, "planned_date_start", today, 7)
        # Las tareas que empezaron antes de hoy y siguen abiertas se muestran hoy.
        for task in tasks:
            if self._local(task.planned_date_start).date() < today:
                agenda[today].insert(0, task)
        printing = len([r for r in rows if r["current"] and r["current"].state == "in_progress"])
        return self._render(
            "app_production", "produccion", rows=rows, agenda=agenda, day_label=self._day_label,
            printing=printing, today=today,
        )

    @http.route(f"{APP_URL}/produccion/replanificar", type="http", auth="user", methods=["POST"], sitemap=False)
    def production_resync(self, **post):
        if (blocked := self._guard()):
            return blocked
        try:
            request.env["crm.lead"].sudo().action_resync_planning_queue()
        except (UserError, ValidationError):
            request.env.cr.rollback()
            return request.redirect(f"{APP_URL}/produccion?ok=error_plan")
        return request.redirect(f"{APP_URL}/produccion?ok=replanificado")

    # ------------------------------------------------------------------
    # Calendario comercial
    # ------------------------------------------------------------------
    @http.route(f"{APP_URL}/agenda", type="http", auth="user", methods=["GET", "POST"], sitemap=False)
    def agenda(self, semana="0", **post):
        if (blocked := self._guard()):
            return blocked
        error = None
        Event = request.env["calendar.event"].sudo()
        if request.httprequest.method == "POST":
            try:
                name = (post.get("name") or "").strip()
                if not name:
                    raise ValidationError(_("Escribe el título del evento."))
                start_local = datetime.strptime(post.get("start") or "", "%Y-%m-%dT%H:%M")
                duration = float(post.get("duration") or 1)
                start = self._to_utc(start_local)
                vals = {
                    "name": name,
                    "start": start,
                    "stop": start + timedelta(hours=duration),
                    "duration": duration,
                    "user_id": request.env.user.id,
                    "partner_ids": [(4, request.env.user.partner_id.id)],
                    "location": (post.get("location") or "").strip() or False,
                    "description": (post.get("description") or "").strip() or False,
                }
                if post.get("categ_id"):
                    vals["categ_ids"] = [(6, 0, [int(post["categ_id"])])]
                if post.get("lead_id"):
                    vals["opportunity_id"] = self._get_lead(int(post["lead_id"])).id
                event = Event.create(vals)
                week = (self._local(event.start).date() - self._today()).days // 7
                return request.redirect(f"{APP_URL}/agenda?semana={max(week, 0)}&ok=evento")
            except (ValueError, UserError, ValidationError) as err:
                request.env.cr.rollback()
                error = str(err.args[0]) if isinstance(err, (UserError, ValidationError)) else _("Revisa la fecha y la duración.")
        try:
            week = int(semana)
        except ValueError:
            week = 0
        today = self._today()
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week)
        start = datetime.combine(monday, datetime.min.time())
        events = Event.search([
            ("start", ">=", self._to_utc(start)),
            ("start", "<", self._to_utc(start + timedelta(days=7))),
        ], order="start asc")
        deliveries = self._leads().search(self._lead_domain() + [
            ("active", "=", True), ("delivery_date", ">=", monday),
            ("delivery_date", "<", monday + timedelta(days=7)),
        ])
        days = []
        ev_by_day = self._group_by_day(events, "start", monday, 7)
        del_by_day = self._group_by_day(deliveries, "delivery_date", monday, 7)
        for day in ev_by_day:
            days.append({
                "date": day, "label": self._day_label(day), "short": WEEKDAYS[day.weekday()],
                "events": ev_by_day[day], "deliveries": del_by_day[day], "is_today": day == today,
            })
        sunday = monday + timedelta(days=6)
        week_label = f"{monday.day} {MONTHS[monday.month - 1]} – {sunday.day} {MONTHS[sunday.month - 1]} {sunday.year}"
        default_start = (self._local(fields.Datetime.now()) + timedelta(hours=1)).replace(minute=0)
        open_leads = self._leads().search(self._lead_domain() + [("active", "=", True)], limit=100, order="id desc")
        return self._render(
            "app_agenda", "agenda", days=days, week=week, week_label=week_label, error=error,
            categories=request.env["calendar.event.type"].sudo().search([], order="name"),
            default_start=default_start.strftime("%Y-%m-%dT%H:%M"), open_leads=open_leads,
            total_events=len(events),
        )

    # ------------------------------------------------------------------
    # Instalar y PWA
    # ------------------------------------------------------------------
    @http.route(f"{APP_URL}/instalar", type="http", auth="user", sitemap=False)
    def install_help(self, **kw):
        if (blocked := self._guard()):
            return blocked
        return self._render("app_install", "instalar")

    @http.route(f"{APP_URL}/manifest.webmanifest", type="http", auth="public", sitemap=False)
    def manifest(self, **kw):
        base = "/crm_extended_3D/static/src/app"
        content = {
            "name": "CreArt 3DP",
            "short_name": "CreArt",
            "description": "Operación del equipo CreArt 3DP",
            "lang": "es",
            "start_url": APP_URL,
            "scope": APP_URL,
            "display": "standalone",
            "background_color": "#F7F3EC",
            "theme_color": "#141414",
            "icons": [
                {"src": f"{base}/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": f"{base}/icon-512.png", "sizes": "512x512", "type": "image/png"},
            ],
        }
        return request.make_response(
            json.dumps(content), headers=[("Content-Type", "application/manifest+json")],
        )

    @http.route(f"{APP_URL}/sw.js", type="http", auth="public", sitemap=False)
    def service_worker(self, **kw):
        body = request.env["ir.qweb"].sudo()._render("crm_extended_3D.app_service_worker", {})
        return request.make_response(body, headers=[
            ("Content-Type", "text/javascript; charset=utf-8"),
            ("Service-Worker-Allowed", APP_URL),
            ("Cache-Control", "no-cache"),
        ])

    @http.route(f"{APP_URL}/sin-conexion", type="http", auth="public", sitemap=False)
    def offline(self, **kw):
        return request.render("crm_extended_3D.app_offline", {"active_tab": None})
