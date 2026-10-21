# -*- coding: utf-8 -*-
"""Portal del equipo (/equipo): emisiones, trámites, siniestros, cobranza y CRM.

Pantallas sencillas para las asistentes del agente, pensadas para el celular y la
computadora. La lógica y los permisos viven en el modelo ``insurance.team.portal``.
"""

import logging
from urllib.parse import urlencode

from odoo import _, http
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import content_disposition, request

from odoo.addons.portal.controllers.portal import CustomerPortal

from ..models.insurance_team import EMISSION_STAGES, PROCEDURE_STATES

_logger = logging.getLogger(__name__)

SLUGS = {
    "emisiones": "emission",
    "tramites": "procedure",
    "siniestros": "claim",
    "cobranza": "collection",
    "crm": "crm",
}
BLOCK_URL = {v: "/equipo/" + k for k, v in SLUGS.items()}
NAV = [
    ("emission", "Emisiones", "fa-file-text-o"),
    ("procedure", "Trámites", "fa-tasks"),
    ("claim", "Siniestros", "fa-medkit"),
    ("collection", "Cobranza", "fa-money"),
    ("crm", "CRM", "fa-handshake-o"),
]
CLAIM_STATE_LABELS = {
    "draft": "Aviso", "documents": "Documentos", "review": "En evaluación", "authorized": "Autorizado",
    "paid": "Pagado", "rejected": "Rechazado", "closed": "Cerrado",
}
COLLECTION_STAGE_LABELS = {
    "grace": ("En gracia", "ok"), "auto_charge": ("Cobro automático", "ok"), "extension": ("Prórroga", "warn"),
    "protection": ("Amparo", "bad"), "lapsed": ("Sin pago", "bad"), "paid": ("Pagada", "ok"),
    "cancelled": ("Cancelada", "na"),
}
ERRORS = (UserError, ValidationError, AccessError, MissingError, ValueError)


def _msg(error):
    return str(error.args[0] if getattr(error, "args", None) else error)


class InsuranceTeamPortal(http.Controller):

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _svc():
        return request.env["insurance.team.portal"]

    @staticmethod
    def _back(url, error=None, ok=None, **params):
        if error:
            params["error"] = error
        if ok:
            params["ok"] = ok
        params = {k: v for k, v in params.items() if v}
        return request.redirect(url + ("?" + urlencode(params) if params else ""))

    def _render(self, template, page="home", **values):
        svc = self._svc()
        base = CustomerPortal()._prepare_portal_layout_values()
        blocks = svc.blocks()
        base.update({
            "page_name": "insurance_team",
            "team_page": page,
            "svc": svc,
            "blocks": blocks,
            "nav": [n for n in NAV if blocks.get(n[0])],
            "block_url": BLOCK_URL,
            "sees_all": svc.sees_all(),
            "scope": request.params.get("ver") == "todos" and "all" or "mine",
            "error": request.params.get("error"),
            "ok": request.params.get("ok"),
            "money": svc.money,
            "today": svc.today(),
            "claim_states": CLAIM_STATE_LABELS,
            "collection_stages": COLLECTION_STAGE_LABELS,
            "emission_stages": EMISSION_STAGES,
            "procedure_states": PROCEDURE_STATES,
            "user": request.env.user,
            "today_label": self._today_label(svc.today()),
        })
        base.update(values)
        return request.render(template, base)

    @staticmethod
    def _today_label(day):
        days = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        months = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
                  "octubre", "noviembre", "diciembre"]
        return ("%s %s de %s" % (days[day.weekday()], day.day, months[day.month - 1])).capitalize()

    def _guard(self, block):
        if not request.env.user._ins_portal_can(block):
            return request.redirect("/equipo")
        return None

    @staticmethod
    def _files(name="files"):
        return request.httprequest.files.getlist(name)

    def _scope(self):
        return "all" if request.params.get("ver") == "todos" else "mine"

    # ------------------------------------------------------------------ inicio
    @http.route(["/equipo"], type="http", auth="user", website=True)
    def team_home(self, **kw):
        if not request.env.user.ins_portal_access:
            return self._render("insurance_management.team_no_access", page="none")
        return self._render("insurance_management.team_home", "home", dash=self._svc().home())

    @http.route(["/equipo/tarea/<int:task_id>/hecha"], type="http", auth="user", website=True, methods=["POST"])
    def team_task_done(self, task_id, **kw):
        try:
            self._svc().task_done(task_id)
        except ERRORS as e:
            return self._back("/equipo", error=_msg(e))
        return self._back("/equipo", ok=_("Tarea marcada como hecha."))

    @http.route(["/equipo/api/mover"], type="jsonrpc", auth="user")
    def team_api_move(self, block, record_id, column, **kw):
        try:
            self._svc().move(block, int(record_id), column)
            return {"ok": True}
        except ERRORS as e:
            return {"ok": False, "error": _msg(e)}

    @http.route(["/equipo/nota/<string:slug>/<int:record_id>"], type="http", auth="user", website=True,
                methods=["POST"])
    def team_note(self, slug, record_id, body=None, back=None, **kw):
        block = SLUGS.get(slug)
        url = back if back and back.startswith("/equipo") else "/equipo"
        try:
            self._svc().post_note(block, record_id, body, self._files())
        except ERRORS as e:
            return self._back(url, error=_msg(e))
        return self._back(url, ok=_("Nota guardada."))

    @http.route(["/equipo/archivo/<string:slug>/<int:record_id>/<int:attachment_id>"], type="http", auth="user")
    def team_file(self, slug, record_id, attachment_id, **kw):
        block = SLUGS.get(slug)
        try:
            record = self._svc()._record(block, record_id)
        except ERRORS:
            raise request.not_found()
        attachment = request.env["ir.attachment"].sudo().browse(attachment_id).exists()
        allowed = attachment and (
            (attachment.res_model == record._name and attachment.res_id == record.id)
            or (block == "claim" and attachment.res_model == "insurance.document"
                and attachment.res_id in record.document_ids.ids)
            or attachment.id in record.message_ids.attachment_ids.ids
        )
        if not allowed:
            raise request.not_found()
        return request.env["ir.binary"]._get_stream_from(attachment).get_response(as_attachment=False)

    @http.route(["/equipo/pdf/<string:slug>/<int:record_id>"], type="http", auth="user")
    def team_pdf(self, slug, record_id, **kw):
        """PDF con la marca de la agencia: resumen de póliza, seguimiento de siniestro o comprobante de pago."""
        reports = {
            "emisiones": "insurance_management.action_report_insurance_policy",
            "siniestros": "insurance_management.action_report_insurance_claim",
            "cobranza": "insurance_management.action_report_insurance_receipt",
        }
        block = SLUGS.get(slug)
        if slug not in reports:
            raise request.not_found()
        try:
            record = self._svc()._record(block, record_id)
        except ERRORS:
            raise request.not_found()
        if slug == "cobranza" and record.state != "paid":
            raise request.not_found()
        Report = request.env["ir.actions.report"].sudo()
        pdf, _type = Report._render_qweb_pdf(reports[slug], record.ids)
        name = {"emisiones": "Poliza", "siniestros": "Siniestro", "cobranza": "Comprobante"}[slug]
        filename = "%s-%s.pdf" % (name, (record.display_name or str(record.id)).replace("/", "-"))
        return request.make_response(pdf, headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Length", len(pdf)),
            ("Content-Disposition", content_disposition(filename, disposition_type="inline")),
        ])

    # ------------------------------------------------------------------ emisiones
    @http.route(["/equipo/emisiones"], type="http", auth="user", website=True)
    def team_emissions(self, **kw):
        if redirect := self._guard("emission"):
            return redirect
        return self._render("insurance_management.team_emissions", "emission",
                            columns=self._svc().emission_board(self._scope()))

    @http.route(["/equipo/emisiones/nueva"], type="http", auth="user", website=True, methods=["GET"])
    def team_emission_new(self, **kw):
        if redirect := self._guard("emission"):
            return redirect
        return self._render("insurance_management.team_emission_new", "emission", cat=self._svc().catalogs())

    @http.route(["/equipo/emisiones/nueva"], type="http", auth="user", website=True, methods=["POST"])
    def team_emission_create(self, **post):
        if redirect := self._guard("emission"):
            return redirect
        try:
            policy = self._svc().emission_create(post)
        except ERRORS as e:
            return self._back("/equipo/emisiones/nueva", error=_msg(e))
        return self._back("/equipo/emisiones/%s" % policy.id, ok=_("Solicitud creada."))

    @http.route(["/equipo/emisiones/caratula"], type="http", auth="user", website=True, methods=["POST"])
    def team_emission_caratula(self, **post):
        if redirect := self._guard("emission"):
            return redirect
        try:
            policy, warning = self._svc().emission_from_caratula(request.httprequest.files.get("caratula"))
        except ERRORS as e:
            return self._back("/equipo/emisiones", error=_msg(e))
        except Exception as e:  # PDF dañado u otro error de lectura
            _logger.exception("Error al cargar carátula desde el portal")
            return self._back("/equipo/emisiones", error=_("No se pudo leer el PDF: %s") % e)
        ok = _("Póliza %s cargada desde la carátula.") % policy.name
        if warning:
            ok += " " + _("Revisa: %s") % warning.replace("\n", " ")
        return self._back("/equipo/emisiones/%s" % policy.id, ok=ok)

    @http.route(["/equipo/emisiones/<int:policy_id>"], type="http", auth="user", website=True)
    def team_emission(self, policy_id, **kw):
        if redirect := self._guard("emission"):
            return redirect
        try:
            policy = self._svc()._record("emission", policy_id)
        except ERRORS:
            return request.redirect("/equipo/emisiones")
        return self._render("insurance_management.team_emission", "emission", policy=policy,
                            cat=self._svc().catalogs(), timeline=self._svc().timeline(policy))

    @http.route(["/equipo/emisiones/<int:policy_id>/etapa"], type="http", auth="user", website=True,
                methods=["POST"])
    def team_emission_move(self, policy_id, stage=None, **post):
        url = "/equipo/emisiones/%s" % policy_id
        try:
            self._svc().emission_move(policy_id, stage or "next", post)
        except ERRORS as e:
            return self._back(url, error=_msg(e))
        return self._back(url, ok=_("Etapa actualizada."))

    # ------------------------------------------------------------------ trámites
    @http.route(["/equipo/tramites"], type="http", auth="user", website=True)
    def team_procedures(self, tipo=None, **kw):
        if redirect := self._guard("procedure"):
            return redirect
        svc = self._svc()
        return self._render("insurance_management.team_procedures", "procedure",
                            columns=svc.procedure_board(self._scope(), tipo if tipo and tipo.isdigit() else None),
                            cat=svc.catalogs(), current_type=int(tipo) if tipo and tipo.isdigit() else 0)

    @http.route(["/equipo/tramites/nuevo"], type="http", auth="user", website=True, methods=["GET"])
    def team_procedure_new(self, tipo=None, **kw):
        if redirect := self._guard("procedure"):
            return redirect
        svc = self._svc()
        return self._render("insurance_management.team_procedure_new", "procedure", cat=svc.catalogs(),
                            policies=svc.policy_choices("procedure"),
                            current_type=int(tipo) if tipo and tipo.isdigit() else 0)

    @http.route(["/equipo/tramites/nuevo"], type="http", auth="user", website=True, methods=["POST"])
    def team_procedure_create(self, **post):
        if redirect := self._guard("procedure"):
            return redirect
        try:
            proc = self._svc().procedure_create(post, self._files())
        except ERRORS as e:
            return self._back("/equipo/tramites/nuevo", error=_msg(e))
        return self._back("/equipo/tramites/%s" % proc.id, ok=_("Trámite %s registrado.") % proc.name)

    @http.route(["/equipo/tramites/<int:proc_id>"], type="http", auth="user", website=True)
    def team_procedure(self, proc_id, **kw):
        if redirect := self._guard("procedure"):
            return redirect
        try:
            proc = self._svc()._record("procedure", proc_id)
        except ERRORS:
            return request.redirect("/equipo/tramites")
        return self._render("insurance_management.team_procedure", "procedure", proc=proc,
                            timeline=self._svc().timeline(proc))

    @http.route(["/equipo/tramites/<int:proc_id>/estado"], type="http", auth="user", website=True,
                methods=["POST"])
    def team_procedure_move(self, proc_id, state=None, **post):
        url = "/equipo/tramites/%s" % proc_id
        try:
            self._svc().procedure_move(proc_id, state, post)
        except ERRORS as e:
            return self._back(url, error=_msg(e))
        return self._back(url, ok=_("Trámite actualizado."))

    # ------------------------------------------------------------------ siniestros
    @http.route(["/equipo/siniestros"], type="http", auth="user", website=True)
    def team_claims(self, modo=None, **kw):
        if redirect := self._guard("claim"):
            return redirect
        return self._render("insurance_management.team_claims", "claim",
                            columns=self._svc().claim_board(self._scope(), modo), mode=modo or "")

    @http.route(["/equipo/siniestros/nuevo"], type="http", auth="user", website=True, methods=["GET"])
    def team_claim_new(self, **kw):
        if redirect := self._guard("claim"):
            return redirect
        svc = self._svc()
        return self._render("insurance_management.team_claim_new", "claim", cat=svc.catalogs(),
                            policies=svc.policy_choices("claim"), mode=kw.get("modo") or "")

    @http.route(["/equipo/siniestros/nuevo"], type="http", auth="user", website=True, methods=["POST"])
    def team_claim_create(self, **post):
        if redirect := self._guard("claim"):
            return redirect
        try:
            claim = self._svc().claim_create(post, self._files())
        except ERRORS as e:
            return self._back("/equipo/siniestros/nuevo", error=_msg(e))
        return self._back("/equipo/siniestros/%s" % claim.id, ok=_("Siniestro %s registrado.") % claim.name)

    @http.route(["/equipo/siniestros/<int:claim_id>"], type="http", auth="user", website=True)
    def team_claim(self, claim_id, **kw):
        if redirect := self._guard("claim"):
            return redirect
        try:
            claim = self._svc()._record("claim", claim_id)
        except ERRORS:
            return request.redirect("/equipo/siniestros")
        return self._render("insurance_management.team_claim", "claim", claim=claim,
                            timeline=self._svc().timeline(claim))

    @http.route(["/equipo/siniestros/<int:claim_id>/accion/<string:action>"], type="http", auth="user",
                website=True, methods=["POST"])
    def team_claim_action(self, claim_id, action, **post):
        url = "/equipo/siniestros/%s" % claim_id
        try:
            self._svc().claim_action(claim_id, action, post)
        except ERRORS as e:
            return self._back(url, error=_msg(e))
        return self._back(url, ok=_("Siniestro actualizado."))

    @http.route(["/equipo/siniestros/<int:claim_id>/documento/<int:document_id>"], type="http", auth="user",
                website=True, methods=["POST"])
    def team_claim_document(self, claim_id, document_id, signed=None, **post):
        url = "/equipo/siniestros/%s" % claim_id
        try:
            self._svc().claim_document_upload(claim_id, document_id, request.httprequest.files.get("file"),
                                              bool(signed))
        except ERRORS as e:
            return self._back(url, error=_msg(e))
        return self._back(url, ok=_("Documento cargado."))

    # ------------------------------------------------------------------ cobranza
    @http.route(["/equipo/cobranza"], type="http", auth="user", website=True)
    def team_collection(self, vista="pending", q=None, **kw):
        if redirect := self._guard("collection"):
            return redirect
        lines, totals, counts = self._svc().collection_list(vista, q)
        Line = request.env["insurance.installment"]
        channel_labels = dict(Line._fields["collection_channel"]._description_selection(request.env))
        return self._render("insurance_management.team_collection", "collection", lines=lines, totals=totals,
                            counts=counts, view=vista, q=q or "", channel_labels=channel_labels)

    @http.route(["/equipo/cobranza/<int:line_id>/pago"], type="http", auth="user", website=True,
                methods=["POST"])
    def team_collection_pay(self, line_id, back_vista=None, **post):
        try:
            line = self._svc().collection_pay(line_id, post)
        except ERRORS as e:
            return self._back("/equipo/cobranza", error=_msg(e), vista=back_vista)
        return self._back("/equipo/cobranza", ok=_("Pago registrado: %s") % line.name, vista=back_vista)

    @http.route(["/equipo/cobranza/<int:line_id>/correo"], type="http", auth="user", website=True,
                methods=["POST"])
    def team_collection_email(self, line_id, back_vista=None, **post):
        try:
            self._svc().collection_email(line_id)
        except ERRORS as e:
            return self._back("/equipo/cobranza", error=_msg(e), vista=back_vista)
        return self._back("/equipo/cobranza", ok=_("Recordatorio enviado por correo."), vista=back_vista)

    @http.route(["/equipo/cobranza/<int:line_id>/whatsapp"], type="http", auth="user", website=True)
    def team_collection_whatsapp(self, line_id, **kw):
        svc = self._svc()
        try:
            line = svc.collection_log_whatsapp(line_id)
        except ERRORS as e:
            return self._back("/equipo/cobranza", error=_msg(e))
        link = svc.wa_link(line.partner_id, svc.collection_reminder_text(line))
        if not link:
            return self._back("/equipo/cobranza", error=_("El cliente no tiene teléfono."))
        return request.redirect(link, local=False)

    # ------------------------------------------------------------------ CRM
    @http.route(["/equipo/crm"], type="http", auth="user", website=True)
    def team_crm(self, **kw):
        if redirect := self._guard("crm"):
            return redirect
        return self._render("insurance_management.team_crm", "crm", columns=self._svc().crm_board(self._scope()))

    @http.route(["/equipo/crm/nuevo"], type="http", auth="user", website=True, methods=["GET"])
    def team_crm_new(self, **kw):
        if redirect := self._guard("crm"):
            return redirect
        return self._render("insurance_management.team_crm_new", "crm", cat=self._svc().catalogs())

    @http.route(["/equipo/crm/nuevo"], type="http", auth="user", website=True, methods=["POST"])
    def team_crm_create(self, **post):
        if redirect := self._guard("crm"):
            return redirect
        try:
            lead = self._svc().crm_create(post)
        except ERRORS as e:
            return self._back("/equipo/crm/nuevo", error=_msg(e))
        return self._back("/equipo/crm/%s" % lead.id, ok=_("Prospecto registrado."))

    @http.route(["/equipo/crm/<int:lead_id>"], type="http", auth="user", website=True)
    def team_crm_lead(self, lead_id, **kw):
        if redirect := self._guard("crm"):
            return redirect
        svc = self._svc()
        try:
            lead = svc._record("crm", lead_id)
        except ERRORS:
            return request.redirect("/equipo/crm")
        return self._render("insurance_management.team_crm_lead", "crm", lead=lead, cat=svc.catalogs(),
                            stages=svc._crm_stages(), tasks=svc.crm_tasks(lead), timeline=svc.timeline(lead),
                            wa=svc.wa_link(lead.partner_id, "") if lead.partner_id else False)

    @http.route(["/equipo/crm/<int:lead_id>/<string:op>"], type="http", auth="user", website=True,
                methods=["POST"])
    def team_crm_op(self, lead_id, op, **post):
        svc = self._svc()
        url = "/equipo/crm/%s" % lead_id
        try:
            if op == "etapa":
                svc.crm_move(lead_id, post.get("stage"), post)
                ok = _("Etapa actualizada.")
            elif op == "datos":
                svc.crm_update(lead_id, post)
                ok = _("Datos guardados.")
            elif op == "cita":
                svc.crm_schedule(lead_id, post)
                ok = _("Cita agendada.")
            elif op == "emision":
                policy = svc.crm_to_emission(lead_id)
                if request.env.user._ins_portal_can("emission"):
                    return self._back("/equipo/emisiones/%s" % policy.id, ok=_("Enviado a emisión."))
                ok = _("Enviado a emisión: %s") % policy.display_name
            else:
                return request.redirect(url)
        except ERRORS as e:
            return self._back(url, error=_msg(e))
        return self._back(url, ok=ok)
