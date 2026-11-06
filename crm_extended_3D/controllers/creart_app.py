"""App del equipo CreArt 3DP: portal móvil instalable (PWA)."""

import json
from datetime import datetime

from werkzeug.exceptions import Forbidden, NotFound

from odoo import fields, http, _
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

from odoo.addons.crm_extended_3D.hooks import CREART_STAGE_XMLID_BY_NAME

TEAM_GROUP = "crm_extended_3D.group_creart_portal_team"
SALES_GROUP = "sales_team.group_sale_salesman"
TEXT_FIELDS = ("name", "contact_name", "partner_name", "phone", "email_from", "filament_color")
FLOAT_FIELDS = ("expected_revenue", "advance_payment", "filament_grams")
INT_FIELDS = ("units", "print_hours", "print_time_minutes")
PAGE_SIZE = 40
STEP_NEXT = {"pending": "in_progress", "in_progress": "done", "done": "pending"}


class CreartApp(http.Controller):

    # ------------------------------------------------------------------
    # Apoyo
    # ------------------------------------------------------------------
    def _check_access(self):
        """Solo el equipo: usuarios portal autorizados y comerciales internos."""
        user = request.env.user
        if not (user.has_group(TEAM_GROUP) or user.has_group(SALES_GROUP)):
            raise Forbidden()

    def _company(self):
        return request.env.company

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
    def _date(value):
        return value.strftime("%d/%m/%Y") if value else "—"

    def _render(self, template, active, **values):
        values.update({
            "active_tab": active,
            "money": self._money,
            "fmt_date": self._date,
            "csrf": request.csrf_token(),
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
                    vals[key] = int(post.get(key) or 0)
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
        if "form_submitted" in post:
            vals["creart_no_charge"] = bool(post.get("creart_no_charge"))
        if post.get("filament_id"):
            product = request.env["product.product"].sudo().browse(int(post["filament_id"])).exists()
            vals["filament_id"] = product.id or False
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

    def _form_values(self, lead=None, error=None, post=None):
        return {
            "lead": lead,
            "error": error,
            "post": post or {},
            "stages": self._stages(),
            "payment_methods": request.env["crm.lead"]._fields["payment_method"].selection,
            "filaments": self._filaments(),
        }

    # ------------------------------------------------------------------
    # Páginas
    # ------------------------------------------------------------------
    @http.route("/creart/app", type="http", auth="user", sitemap=False)
    def dashboard(self, **kw):
        self._check_access()
        data = request.env["creart.kpi"].sudo().get_dashboard(self._company())
        return self._render("app_dashboard", "tablero", data=data, cards=data["cards"])

    @http.route("/creart/app/trabajos", type="http", auth="user", sitemap=False)
    def jobs(self, q="", etapa=None, archivados=None, **kw):
        self._check_access()
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
        leads = self._leads().search(domain, limit=PAGE_SIZE, order="priority desc, id desc")
        return self._render(
            "app_jobs", "trabajos", leads=leads, q=q, stage_id=stage_id,
            stages=self._stages(), archived=bool(archivados),
            total=self._leads().search_count(domain), page_size=PAGE_SIZE,
        )

    @http.route("/creart/app/trabajos/nuevo", type="http", auth="user", methods=["GET", "POST"], sitemap=False)
    def job_new(self, **post):
        self._check_access()
        error = None
        if request.httprequest.method == "POST":
            try:
                vals = self._values_from_post(post)
                vals.pop("stage_id", None)
                vals.update({"type": "opportunity", "company_id": self._company().id})
                lead = self._leads().create(vals)
                return request.redirect(f"/creart/app/trabajos/{lead.id}")
            except (UserError, ValidationError) as err:
                request.env.cr.rollback()
                error = str(err.args[0])
        return self._render("app_job_form", "nuevo", **self._form_values(error=error, post=post))

    @http.route("/creart/app/trabajos/<int:lead_id>", type="http", auth="user", methods=["GET", "POST"], sitemap=False)
    def job_detail(self, lead_id, **post):
        self._check_access()
        lead = self._get_lead(lead_id)
        error = None
        if request.httprequest.method == "POST":
            try:
                lead.write(self._values_from_post(post))
                return request.redirect(f"/creart/app/trabajos/{lead.id}?guardado=1")
            except (UserError, ValidationError) as err:
                request.env.cr.rollback()
                error = str(err.args[0])
        notes = lead.message_ids.filtered(lambda m: m.message_type == "comment" and m.body)[:8]
        return self._render(
            "app_job_form", "trabajos", notes=notes, saved=post.get("guardado"),
            **self._form_values(lead=lead, error=error, post=post if error else None),
        )

    @http.route("/creart/app/trabajos/<int:lead_id>/archivar", type="http", auth="user", methods=["POST"], sitemap=False)
    def job_archive(self, lead_id, **post):
        self._check_access()
        self._get_lead(lead_id).write({"active": False})
        return request.redirect("/creart/app/trabajos")

    @http.route("/creart/app/trabajos/<int:lead_id>/restaurar", type="http", auth="user", methods=["POST"], sitemap=False)
    def job_restore(self, lead_id, **post):
        self._check_access()
        self._get_lead(lead_id).write({"active": True})
        return request.redirect(f"/creart/app/trabajos/{lead_id}")

    @http.route("/creart/app/trabajos/<int:lead_id>/nota", type="http", auth="user", methods=["POST"], sitemap=False)
    def job_note(self, lead_id, nota="", **post):
        self._check_access()
        nota = (nota or "").strip()
        if nota:
            self._get_lead(lead_id).message_post(
                body=nota, message_type="comment", subtype_xmlid="mail.mt_note",
                author_id=request.env.user.partner_id.id,
            )
        return request.redirect(f"/creart/app/trabajos/{lead_id}#notas")

    @http.route(
        "/creart/app/trabajos/<int:lead_id>/paso/<int:step_id>",
        type="http", auth="user", methods=["POST"], sitemap=False,
    )
    def job_step(self, lead_id, step_id, **post):
        self._check_access()
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
        return request.redirect(f"/creart/app/trabajos/{lead_id}#pasos")

    @http.route("/creart/app/instalar", type="http", auth="user", sitemap=False)
    def install_help(self, **kw):
        self._check_access()
        return self._render("app_install", "instalar")

    # ------------------------------------------------------------------
    # PWA
    # ------------------------------------------------------------------
    @http.route("/creart/app/manifest.webmanifest", type="http", auth="public", sitemap=False)
    def manifest(self, **kw):
        base = "/crm_extended_3D/static/src/app"
        content = {
            "name": "CreArt 3DP",
            "short_name": "CreArt",
            "description": "Operación del equipo CreArt 3DP",
            "lang": "es",
            "start_url": "/creart/app",
            "scope": "/creart/app",
            "display": "standalone",
            "background_color": "#141414",
            "theme_color": "#141414",
            "icons": [
                {"src": f"{base}/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": f"{base}/icon-512.png", "sizes": "512x512", "type": "image/png"},
            ],
        }
        return request.make_response(
            json.dumps(content), headers=[("Content-Type", "application/manifest+json")],
        )

    @http.route("/creart/app/sw.js", type="http", auth="public", sitemap=False)
    def service_worker(self, **kw):
        body = request.env["ir.qweb"].sudo()._render("crm_extended_3D.app_service_worker", {})
        return request.make_response(body, headers=[
            ("Content-Type", "text/javascript; charset=utf-8"),
            ("Service-Worker-Allowed", "/creart/app"),
            ("Cache-Control", "no-cache"),
        ])

    @http.route("/creart/app/sin-conexion", type="http", auth="public", sitemap=False)
    def offline(self, **kw):
        return request.render("crm_extended_3D.app_offline")
