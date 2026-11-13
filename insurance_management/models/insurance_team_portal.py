# -*- coding: utf-8 -*-
"""Lógica del portal del equipo (/equipo).

Todo pasa por aquí: el controlador solo arma pantallas. Cada método valida que la
persona tenga el bloque habilitado y que el registro sea suyo (o que supervise a todo
el equipo) antes de leer o escribir con sudo.
"""

import base64
import re
from datetime import datetime, time
from urllib.parse import quote

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, MissingError, UserError
from markupsafe import Markup
from odoo.tools import html2plaintext, html_escape

from .insurance_team import EMISSION_STAGES, FOLLOWUP_STATES, LOG_CHANNELS, PROCEDURE_STATES

CRM_STAGE_XMLIDS = [
    ("first", "insurance_management.crm_stage_first_meeting"),
    ("closing", "insurance_management.crm_stage_closing_meeting"),
    ("followup", "insurance_management.crm_stage_followup"),
    ("won", "insurance_management.crm_stage_won"),
    ("lost", "insurance_management.crm_stage_lost_publicity"),
]
CLAIM_COLUMNS = [
    ("draft", "Aviso"),
    ("documents", "Documentos"),
    ("review", "En evaluación"),
    ("authorized", "Autorizado"),
    ("paid", "Pagado"),
]
BLOCK_MODELS = {
    "portfolio": "insurance.policy",
    "emission": "insurance.policy",
    "procedure": "insurance.procedure",
    "claim": "insurance.claim",
    "collection": "insurance.installment",
    "crm": "crm.lead",
    "renewal": "insurance.policy",
    "anniversary": "insurance.policy",
}
MAX_FILE = 15 * 1024 * 1024


class InsuranceTeamPortal(models.AbstractModel):
    _name = "insurance.team.portal"
    _description = "Portal del equipo de seguros"

    # ------------------------------------------------------------------ acceso
    @api.model
    def _user(self):
        return self.env.user

    @api.model
    def _check(self, block):
        if not self._user()._ins_portal_can(block):
            raise AccessError(_("No tienes habilitado este apartado del portal."))

    @api.model
    def blocks(self):
        user = self._user()
        return {b: user._ins_portal_can(b) for b in BLOCK_MODELS}

    @api.model
    def sees_all(self):
        return bool(self._user().sudo().ins_portal_all)

    @api.model
    def _mine_domain(self, block, scope="mine"):
        uid = self._user().id
        if block == "crm":
            # Solo oportunidades de seguros: en la misma base puede haber las de otros negocios.
            insurance = [("is_insurance_opportunity", "=", True), ("type", "=", "opportunity")]
            if scope == "all" and self.sees_all():
                return insurance
            return insurance + ["|", ("team_user_id", "=", uid), ("user_id", "=", uid)]
        if scope == "all" and self.sees_all():
            return []
        if block == "procedure":
            return [("user_id", "=", uid)]
        if block == "collection":
            return []  # quien tiene Cobranza ve toda la cobranza
        if block == "portfolio":
            return [("agent_id.user_id", "=", uid)]
        if block in ("renewal", "anniversary"):
            return []  # renovaciones y aniversarios los ve quien tenga el bloque
        return [("team_user_id", "=", uid)]

    @api.model
    def _record(self, block, record_id):
        self._check(block)
        Model = self.env[BLOCK_MODELS[block]].sudo()
        domain = [("id", "=", int(record_id))] + self._mine_domain(block, "all")
        if block == "crm":
            Model = Model.with_context(active_test=False)
        record = Model.search(domain, limit=1)
        if not record:
            raise MissingError(_("El registro no existe o no está asignado a ti."))
        return record

    # ------------------------------------------------------------------ utilidades
    @api.model
    def today(self):
        return fields.Date.context_today(self)

    @api.model
    def local(self, value):
        if not value:
            return value
        tz = pytz.timezone(self._user().tz or "America/Mexico_City")
        return pytz.utc.localize(value).astimezone(tz)

    @api.model
    def money(self, amount, currency=None):
        symbol = (currency.symbol if currency else "$") or "$"
        code = currency.name if currency else ""
        text = "{} {:,.2f}".format(symbol, amount or 0.0)
        if code and code != "MXN":
            text += " " + code
        return text

    @api.model
    def wa_link(self, partner, text):
        """Liga de WhatsApp; ``partner`` puede ser un contacto o un teléfono escrito."""
        raw = partner if isinstance(partner, str) else (partner._insurance_phone() if partner else "")
        phone = re.sub(r"\D", "", raw or "")
        if len(phone) == 10:
            phone = "52" + phone
        if not phone:
            return False
        return "https://wa.me/%s?text=%s" % (phone, quote(text or ""))

    @api.model
    def _author(self):
        return self._user().partner_id

    @api.model
    def _attachments(self, record, files, for_message=True):
        """Crea los adjuntos. Para un mensaje se crean "pendientes" (como el redactor de correo):
        así message_post los liga al registro también cuando quien sube es usuario de portal."""
        attachments = self.env["ir.attachment"].sudo()
        for upload in files or []:
            if not upload or not getattr(upload, "filename", None):
                continue
            data = upload.read()
            if not data:
                continue
            if len(data) > MAX_FILE:
                raise UserError(_("El archivo %s pasa de 15 MB.") % upload.filename)
            attachments |= attachments.create({
                "name": upload.filename,
                "datas": base64.b64encode(data),
                "res_model": "mail.compose.message" if for_message else record._name,
                "res_id": 0 if for_message else record.id,
                "mimetype": upload.mimetype or False,
            })
        return attachments

    @api.model
    def post_note(self, block, record_id, body, files=None):
        record = self._record(block, record_id)
        attachments = self._attachments(record, files)
        if not (body or "").strip() and not attachments:
            raise UserError(_("Escribe una nota o adjunta un archivo."))
        record.message_post(
            body=(body or "").strip() or _("Archivo adjunto"),
            author_id=self._author().id,
            attachment_ids=attachments.ids,
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        return record

    @api.model
    def timeline(self, record, limit=30):
        messages = record.sudo().message_ids.filtered(
            lambda m: m.message_type in ("comment", "email", "notification") or m.tracking_value_ids
        )[:limit]
        rows = []
        for msg in messages:
            text = re.sub(r"\*(.+?)\*", r"\1", html2plaintext(msg.body or "")).strip()
            if not text and msg.tracking_value_ids:
                parts = []
                for tv in msg.tracking_value_ids.sudo():
                    parts.append("%s: %s" % (tv.field_id.field_description, self._tracking_new(tv)))
                text = " · ".join(parts)
            if not text and not msg.attachment_ids:
                continue
            rows.append({
                "author": msg.author_id.name or _("Sistema"),
                "date": self.local(msg.date),
                "text": text,
                "files": msg.attachment_ids,
            })
        return rows

    @api.model
    def _tracking_new(self, tv):
        """Valor nuevo de un cambio registrado, legible (texto, número o fecha)."""
        ftype = tv.field_id.ttype
        if ftype in ("integer",):
            return str(tv.new_value_integer)
        if ftype in ("float", "monetary"):
            return "{:,.2f}".format(tv.new_value_float or 0.0)
        if ftype == "date" and tv.new_value_datetime:
            return tv.new_value_datetime.strftime("%d/%m/%Y")
        if ftype == "datetime" and tv.new_value_datetime:
            return self.local(tv.new_value_datetime).strftime("%d/%m/%Y %H:%M")
        if ftype == "boolean":
            return _("Sí") if tv.new_value_integer else _("No")
        return tv.new_value_char or "—"

    # ------------------------------------------------------------------ inicio
    @api.model
    def home(self):
        user = self._user()
        blocks = self.blocks()
        today = self.today()
        cards = {}
        if blocks["portfolio"]:
            policies = self.env["insurance.policy"].sudo().search(
                self._mine_domain("portfolio") + [("state", "=", "confirmed")])
            hot = len(policies.filtered(lambda p: p.renewal_state == "pending" or p.attention_level == "danger"))
            cards["portfolio"] = {"count": len(policies), "hot": hot, "hot_label": _("requieren atención"),
                                  "count_label": _("póliza vigente") if len(policies) == 1 else _("pólizas vigentes")}
        if blocks["emission"]:
            policies = self.env["insurance.policy"].sudo().search(
                self._mine_domain("emission") + [("emission_stage", "!=", "delivered"), ("state", "!=", "cancelled")]
            )
            cards["emission"] = {"count": len(policies),
                                 "hot": len(policies.filtered(lambda p: p.emission_stage == "issued")),
                                 "hot_label": _("por entregar")}
        if blocks["procedure"]:
            procs = self.env["insurance.procedure"].sudo().search(
                self._mine_domain("procedure") + [("state", "not in", ["done", "cancelled"])]
            )
            cards["procedure"] = {"count": len(procs), "hot": len(procs.filtered("is_late")),
                                  "hot_label": _("atrasados")}
        if blocks["claim"]:
            claims = self.env["insurance.claim"].sudo().search(
                self._mine_domain("claim") + [("state", "not in", ["paid", "rejected", "closed"])]
            )
            cards["claim"] = {"count": len(claims), "hot": len(claims.filtered(lambda c: c.state == "documents")),
                              "hot_label": _("piden documentos")}
        if blocks["collection"]:
            lines = self.env["insurance.installment"].sudo().search(
                [("collection_status", "in", ["overdue", "due_soon"])]
            )
            cards["collection"] = {"count": len(lines),
                                   "hot": len(lines.filtered(lambda l: l.collection_status == "overdue")),
                                   "hot_label": _("vencidos")}
        if blocks["crm"]:
            leads = self.env["crm.lead"].sudo().search(
                self._mine_domain("crm") + [("stage_id", "in", self._crm_open_stage_ids()),
                                            ("type", "=", "opportunity")]
            )
            cards["crm"] = {"count": len(leads), "hot": len(leads.filtered(
                lambda l: l.activity_date_deadline and l.activity_date_deadline <= today)),
                "hot_label": _("con cita hoy o antes")}
        if blocks["renewal"]:
            policies = self.renewal_policies()
            cards["renewal"] = {"count": len(policies),
                                "hot": len(policies.filtered(lambda p: p.end_date and p.end_date <= today + relativedelta(days=15))),
                                "hot_label": _("vencen en 15 días")}
        if blocks["anniversary"]:
            rows = self.anniversary_rows()
            cards["anniversary"] = {"count": len(rows),
                                    "hot": len([r for r in rows if r["days"] <= 15]),
                                    "hot_label": _("en 15 días o menos")}
        return {"cards": cards, "tasks": self.day_activities()}

    @api.model
    def task_done(self, task_id):
        task = self.env["insurance.task"].sudo().search(
            [("id", "=", int(task_id)), ("user_id", "=", self._user().id)], limit=1
        )
        if not task:
            raise MissingError(_("La tarea no existe."))
        task.action_done()
        return task

    # ------------------------------------------------------------------ catálogos
    @api.model
    def catalogs(self):
        env = self.env
        return {
            "ramos": env["insurance.policy.type"].sudo().search([]),
            "companies": env["insurance.company"].sudo().search([]),
            "offers": env["insurance.offer"].sudo().search([("parent_id", "=", False)]),
            "currencies": env["res.currency"].sudo().search([("name", "in", ["MXN", "USD", "UDI"])]),
            "premium_types": env["insurance.policy"]._fields["premium_type"]._description_selection(env),
            "procedure_types": env["insurance.procedure.type"].sudo().search([]),
            "claim_types": env["insurance.claim.type"].sudo().search([]),
            "origins": env["crm.lead"]._fields["insurance_origin"]._description_selection(env),
            "delivery_methods": env["insurance.policy"]._fields["delivery_method"]._description_selection(env),
        }

    @api.model
    def policy_choices(self, block):
        """Pólizas para elegir en trámites y siniestros (las vigentes o recientes)."""
        self._check(block)
        return self.env["insurance.policy"].sudo().search(
            [("state", "in", ["confirmed", "expired", "draft"])], order="name", limit=800
        )

    @api.model
    def _find_or_create_partner(self, name, phone=None, email=None):
        name = (name or "").strip()
        if not name:
            raise UserError(_("Escribe el nombre del cliente."))
        Partner = self.env["res.partner"].sudo()
        partner = Partner.search([("name", "=ilike", name)], limit=1)
        if not partner:
            partner = Partner.create({"name": name, "phone": phone or False, "email": email or False})
        else:
            updates = {}
            if phone and not partner.phone:
                updates["phone"] = phone
            if email and not partner.email:
                updates["email"] = email
            if updates:
                partner.write(updates)
        return partner

    @staticmethod
    def _int(value):
        return int(value) if value and str(value).isdigit() else False

    @staticmethod
    def _float(value):
        if value in (None, False, ""):
            return 0.0
        try:
            return float(str(value).replace(",", "").replace("$", "").strip() or 0)
        except ValueError:
            raise UserError(_("El importe «%s» no es válido.") % value)

    # ------------------------------------------------------------------ emisiones
    @api.model
    def emission_board(self, scope="mine"):
        self._check("emission")
        since = self.today() - relativedelta(days=30)
        policies = self.env["insurance.policy"].sudo().search(
            self._mine_domain("emission", scope) + [("state", "!=", "cancelled"),
            "|", ("emission_stage", "!=", "delivered"), ("delivery_date", ">=", since)],
            order="write_date desc",
        )
        return [(key, label, policies.filtered(lambda p, k=key: p.emission_stage == k)) for key, label in EMISSION_STAGES]

    @api.model
    def emission_create(self, post):
        self._check("emission")
        partner = self._find_or_create_partner(post.get("client"), post.get("phone"), post.get("email"))
        vals = {
            "partner_id": partner.id,
            "policy_type_id": self._int(post.get("ramo_id")),
            "insurance_company_id": self._int(post.get("company_id")),
            "offer_id": self._int(post.get("offer_id")),
            "policy_amount": self._float(post.get("amount")),
            "team_user_id": self._user().id,
            "emission_stage": "request",
            "agent_id": self.my_agent().id or False,
        }
        if self._user().share:
            vals["user_id"] = False
        premium_types = dict(self.env["insurance.policy"]._fields["premium_type"]._description_selection(self.env))
        if post.get("premium_type") in premium_types:
            vals["premium_type"] = post["premium_type"]
        currency = self._int(post.get("currency_id"))
        if currency:
            vals["currency_id"] = currency
        policy = self.env["insurance.policy"].sudo().create(
            {k: v for k, v in vals.items() if v is not False or k == "user_id"})
        note = (post.get("note") or "").strip()
        if note:
            policy.message_post(body=note, author_id=self._author().id, subtype_xmlid="mail.mt_note")
        return policy

    @api.model
    def emission_from_caratula(self, upload):
        self._check("emission")
        if not upload or not upload.filename:
            raise UserError(_("Elige el PDF de la carátula."))
        data = upload.read()
        if not data:
            raise UserError(_("El archivo está vacío."))
        wizard = self.env["insurance.policy.pdf.import"].sudo().create({
            "pdf_file": base64.b64encode(data),
            "pdf_filename": upload.filename,
        })
        wizard.action_read_pdf()
        result = wizard.action_create_policy()
        policy = wizard.policy_id
        if not policy and isinstance(result, dict) and result.get("res_id"):
            policy = self.env["insurance.policy"].sudo().browse(result["res_id"])
        if not policy:
            policy = self.env["insurance.policy"].sudo().search([("name", "=", wizard.policy_number)], limit=1)
        if not policy:
            raise UserError(_("No se pudo crear la póliza con esta carátula."))
        vals = {"emission_stage": "issued"} if policy.emission_stage in ("request", "documents", "submitted") else {}
        if not policy.team_user_id:
            vals["team_user_id"] = self._user().id
        if vals:
            policy.write(vals)
        policy.message_post(
            body=_("Carátula cargada desde el portal del equipo (%s).") % upload.filename,
            author_id=self._author().id, subtype_xmlid="mail.mt_note",
        )
        return policy, wizard.warning

    @api.model
    def emission_move(self, policy_id, stage, post=None):
        policy = self._record("emission", policy_id)
        keys = [key for key, _label in EMISSION_STAGES]
        if stage == "next":
            index = keys.index(policy.emission_stage or "request")
            stage = keys[min(index + 1, len(keys) - 1)]
        if stage not in keys:
            raise UserError(_("Etapa no válida."))
        vals = {"emission_stage": stage}
        if stage == "delivered" and post:
            if post.get("delivery_date"):
                vals["delivery_date"] = post["delivery_date"]
            if post.get("delivery_method") in dict(
                    self.env["insurance.policy"]._fields["delivery_method"]._description_selection(self.env)):
                vals["delivery_method"] = post["delivery_method"]
        policy.write(vals)
        return policy

    # ------------------------------------------------------------------ trámites
    @api.model
    def procedure_board(self, scope="mine", type_id=None):
        self._check("procedure")
        domain = self._mine_domain("procedure", scope) + [
            "|", ("state", "not in", ["done", "cancelled"]),
            ("date_done", ">=", self.today() - relativedelta(days=30)),
        ]
        if type_id:
            domain.append(("type_id", "=", int(type_id)))
        procs = self.env["insurance.procedure"].sudo().search(domain)
        columns = [(k, l) for k, l in PROCEDURE_STATES if k != "cancelled"]
        return [(key, label, procs.filtered(lambda p, k=key: p.state == k)) for key, label in columns]

    @api.model
    def procedure_create(self, post, files=None):
        self._check("procedure")
        type_id = self._int(post.get("type_id"))
        if not type_id:
            raise UserError(_("Elige el tipo de trámite."))
        policy = self.env["insurance.policy"].sudo().browse(self._int(post.get("policy_id")) or [])
        vals = {
            "type_id": type_id,
            "policy_id": policy.id or False,
            "user_id": self._user().id,
            "description": post.get("description") or False,
            "insurer_folio": post.get("insurer_folio") or False,
        }
        if not policy:
            vals["partner_id"] = self._find_or_create_partner(post.get("client"), post.get("phone")).id
        if post.get("date_deadline"):
            vals["date_deadline"] = post["date_deadline"]
        proc = self.env["insurance.procedure"].sudo().create(vals)
        attachments = self._attachments(proc, files)
        if attachments:
            proc.message_post(body=_("Documentos recibidos"), attachment_ids=attachments.ids,
                              author_id=self._author().id, subtype_xmlid="mail.mt_note")
        return proc

    @api.model
    def procedure_move(self, proc_id, state, post=None):
        proc = self._record("procedure", proc_id)
        if state not in dict(PROCEDURE_STATES):
            raise UserError(_("Estado no válido."))
        vals = {"state": state}
        if post:
            if post.get("insurer_folio"):
                vals["insurer_folio"] = post["insurer_folio"]
            if post.get("result"):
                vals["result"] = post["result"]
        proc.write(vals)
        return proc

    # ------------------------------------------------------------------ siniestros
    @api.model
    def claim_board(self, scope="mine", mode=None):
        self._check("claim")
        domain = self._mine_domain("claim", scope) + [
            "|", ("state", "not in", ["paid", "rejected", "closed"]),
            ("write_date", ">=", fields.Datetime.now() - relativedelta(days=30)),
        ]
        if mode == "pd":
            domain.append(("claim_type_id.code", "=", "PD"))
        elif mode == "ree":
            domain.append(("claim_type_id.code", "=", "REE"))
        claims = self.env["insurance.claim"].sudo().search(domain, order="occurrence_date desc")
        columns = CLAIM_COLUMNS + [("rejected", _("Rechazado / cerrado"))]
        out = []
        for key, label in columns:
            states = ("rejected", "closed") if key == "rejected" else (key,)
            out.append((key, label, claims.filtered(lambda c, s=states: c.state in s)))
        return out

    @api.model
    def claim_create(self, post, files=None):
        self._check("claim")
        policy = self.env["insurance.policy"].sudo().browse(self._int(post.get("policy_id")) or [])
        if not policy:
            raise UserError(_("Elige la póliza del cliente."))
        claim_type = self.env["insurance.claim.type"].sudo().browse(self._int(post.get("claim_type_id")) or [])
        vals = {
            "policy_id": policy.id,
            "partner_id": policy.partner_id.id,
            "claim_type_id": claim_type.id or False,
            "occurrence_date": post.get("occurrence_date") or self.today(),
            "diagnosis": post.get("diagnosis") or False,
            "provider_name": post.get("provider_name") or False,
            "contact_phone": post.get("contact_phone") or policy.partner_id._insurance_phone() or False,
            "claimed_amount": self._float(post.get("claimed_amount")),
            "description": post.get("description") or False,
            "team_user_id": self._user().id,
        }
        if self._user().share:
            vals["user_id"] = False
        if claim_type and claim_type.claim_kind:
            vals["claim_kind"] = claim_type.claim_kind
        claim = self.env["insurance.claim"].sudo().create(
            {k: v for k, v in vals.items() if v is not False or k == "user_id"})
        attachments = self._attachments(claim, files)
        if attachments:
            claim.message_post(body=_("Documentos recibidos"), attachment_ids=attachments.ids,
                               author_id=self._author().id, subtype_xmlid="mail.mt_note")
        return claim

    @api.model
    def claim_action(self, claim_id, action, post=None):
        claim = self._record("claim", claim_id)
        post = post or {}
        if action == "documents":
            claim.action_request_documents()
        elif action == "review":
            claim.action_submit_review()
        elif action == "authorize":
            vals = {}
            if post.get("coverage_id"):
                vals["coverage_id"] = self._int(post["coverage_id"])
            if post.get("authorized_amount"):
                vals["authorized_amount"] = self._float(post["authorized_amount"])
            if post.get("full"):
                vals["consumption_type"] = "full"
            if vals:
                claim.write(vals)
            claim.action_authorize()
        elif action == "paid":
            if post.get("paid_amount"):
                claim.paid_amount = self._float(post["paid_amount"])
            claim.action_mark_paid()
        elif action == "reject":
            claim.rejection_reason = post.get("reason") or claim.rejection_reason
            claim.action_reject()
        elif action == "close":
            claim.action_close()
        else:
            raise UserError(_("Acción no válida."))
        return claim

    @api.model
    def claim_document_upload(self, claim_id, document_id, upload, signed=False):
        claim = self._record("claim", claim_id)
        document = claim.document_ids.filtered(lambda d: d.id == int(document_id))
        if not document:
            raise MissingError(_("El documento no pertenece al caso."))
        attachments = self._attachments(document, [upload], for_message=False)
        if not attachments:
            raise UserError(_("Elige el archivo."))
        vals = {"attachment_ids": [(4, a) for a in attachments.ids], "state": "uploaded"}
        if signed:
            vals.update({"state": "signed", "signed": True, "signed_date": fields.Datetime.now(),
                         "signed_by": self._user().id})
        document.sudo().write(vals)
        return claim

    # ------------------------------------------------------------------ cobranza
    @api.model
    def collection_list(self, view="pending", search=None):
        self._check("collection")
        today = self.today()
        Line = self.env["insurance.installment"].sudo()
        if view == "overdue":
            domain = [("collection_status", "=", "overdue")]
        elif view == "due_soon":
            domain = [("collection_status", "=", "due_soon")]
        elif view == "paid":
            domain = [("state", "=", "paid"), ("payment_date", ">=", today.replace(day=1))]
        else:
            domain = [("collection_status", "in", ["overdue", "due_soon"])]
        if search:
            domain += ["|", ("partner_id.name", "ilike", search), ("policy_id.name", "ilike", search)]
        lines = Line.search(domain, order="due_date, id", limit=300)
        totals = {}
        for line in lines:
            amount = line.paid_amount if view == "paid" else (line.balance or line.amount)
            key = line.currency_id
            totals[key] = totals.get(key, 0.0) + (amount or 0.0)
        counts = {
            "overdue": Line.search_count([("collection_status", "=", "overdue")]),
            "due_soon": Line.search_count([("collection_status", "=", "due_soon")]),
            "paid": Line.search_count([("state", "=", "paid"), ("payment_date", ">=", today.replace(day=1))]),
        }
        return lines, totals, counts

    @api.model
    def collection_pay(self, line_id, post):
        line = self._record("collection", line_id)
        if line.state in ("paid", "cancelled"):
            raise UserError(_("Este recibo ya no está pendiente."))
        amount = self._float(post.get("amount")) or (line.balance or line.amount)
        if amount <= 0:
            raise UserError(_("Escribe el importe pagado."))
        line.write({
            "paid_amount": (line.paid_amount or 0.0) + amount,
            "payment_date": post.get("payment_date") or self.today(),
        })
        if line.paid_amount >= line.amount - 0.01 and line.state != "paid":
            line.action_mark_paid()
        line.message_post(
            body=_("Pago registrado desde el portal: %s") % self.money(amount, line.currency_id),
            author_id=self._author().id, subtype_xmlid="mail.mt_note",
        )
        return line

    @api.model
    def collection_reminder_text(self, line):
        return _(
            "Hola %(name)s, le recordamos el pago de su póliza %(policy)s (cuota %(num)s) por %(amount)s "
            "con fecha de cobro %(date)s. Si ya pagó, por favor envíenos su comprobante. ¡Gracias!"
        ) % {
            "name": (line.partner_id.name or "").split(" ")[0],
            "policy": line.policy_id.name,
            "num": line.number,
            "amount": self.money(line.balance or line.amount, line.currency_id),
            "date": line.due_date.strftime("%d/%m/%Y") if line.due_date else "",
        }

    @api.model
    def collection_email(self, line_id):
        line = self._record("collection", line_id)
        if not line.partner_id.email:
            raise UserError(_("El cliente no tiene correo."))
        template = self.env.ref("insurance_management.mail_template_installment_overdue", raise_if_not_found=False)
        if not template:
            raise UserError(_("No se encontró la plantilla de recordatorio."))
        line.message_post_with_source(template, message_type="comment", subtype_xmlid="mail.mt_comment")
        return line

    @api.model
    def collection_log_whatsapp(self, line_id):
        line = self._record("collection", line_id)
        line.message_post(body=_("Recordatorio enviado por WhatsApp desde el portal."),
                          author_id=self._author().id, subtype_xmlid="mail.mt_note")
        return line

    # ------------------------------------------------------------------ CRM
    @api.model
    def _crm_named(self, key):
        """Etapa por su papel (1ra cita, cierre, ganado, perdido); tolera que la hayan borrado o renombrado."""
        xmlid = dict(CRM_STAGE_XMLIDS).get(key)
        stage = self.env.ref(xmlid, raise_if_not_found=False) if xmlid else None
        if stage:
            return stage.sudo()
        Stage = self.env["crm.stage"].sudo()
        if key == "won":
            return Stage.search([("is_won", "=", True)], limit=1)
        if key == "first":
            return Stage.search([], order="sequence, id", limit=1)
        return Stage

    @staticmethod
    def _stage_key(stage):
        """Las etapas con el mismo nombre (sin importar mayúsculas ni espacios) se muestran como una sola."""
        return "".join((stage.name or "").lower().split())

    @api.model
    def _crm_stages(self):
        """Etapas del CRM de seguros igual que el tablero «Oportunidades de seguros» de la plataforma.

        Mismo criterio que Odoo: etapas generales (sin equipo) + las del equipo de ventas de las
        oportunidades de seguros + las que ya tienen oportunidades de seguros, todas juntas en el orden
        de Odoo (secuencia, nombre). Las que se llaman igual se juntan en una sola columna."""
        Stage = self.env["crm.stage"].sudo()
        leads = self.env["crm.lead"].sudo().search(
            [("is_insurance_opportunity", "=", True), ("type", "=", "opportunity")])
        teams = leads.mapped("team_id")
        domain = [("team_ids", "=", False)]
        if teams:
            domain = ["|", ("team_ids", "=", False), ("team_ids", "in", teams.ids)]
        stages = Stage.search(domain) | leads.mapped("stage_id")
        lost = self.env.ref("insurance_management.crm_stage_lost_publicity", raise_if_not_found=False)
        if lost:
            stages |= lost.sudo()
        columns, seen = [], set()
        for stage in stages.sorted(lambda st: (st.sequence, (st.name or "").lower(), st.id)):
            key = self._stage_key(stage)
            if key in seen:
                continue
            seen.add(key)
            columns.append((str(stage.id), stage))
        return columns

    @api.model
    def _crm_open_stage_ids(self):
        lost = self._crm_named("lost")
        closed = {self._stage_key(st) for _k, st in self._crm_stages() if st.is_won or st == lost}
        return self.env["crm.stage"].sudo().search([]).filtered(lambda st: self._stage_key(st) not in closed).ids

    @api.model
    def crm_board(self, scope="mine"):
        self._check("crm")
        stages = self._crm_stages()
        leads = self.env["crm.lead"].sudo().search(
            self._mine_domain("crm", scope),
            order="priority desc, write_date desc",
        )
        return [(key, stage, leads.filtered(lambda l, k=self._stage_key(stage): self._stage_key(l.stage_id) == k))
                for key, stage in stages]

    @api.model
    def crm_create(self, post):
        self._check("crm")
        name = (post.get("name") or "").strip()
        if not name:
            raise UserError(_("Escribe el nombre del prospecto."))
        first = self._crm_named("first")
        user = self._user()
        vals = {
            "name": post.get("title") or name,
            "contact_name": name,
            "phone": post.get("phone") or False,
            "email_from": post.get("email") or False,
            "type": "opportunity",
            "is_insurance_opportunity": True,
            "stage_id": first.id or False,
            "team_user_id": user.id,
            "user_id": user.id if not user.share else False,
            "insurance_policy_type_id": self._int(post.get("ramo_id")),
            "insurance_origin": post.get("origin") if post.get("origin") in dict(
                self.env["crm.lead"]._fields["insurance_origin"]._description_selection(self.env)) else "new",
            "expected_revenue": self._float(post.get("amount")),
            "description": post.get("note") or False,
        }
        lead = self.env["crm.lead"].sudo().create({k: v for k, v in vals.items() if v is not False})
        tags = self._tags_from_post(post)
        if tags:
            lead.tag_ids = [(6, 0, tags.ids)]
        if post.get("meeting_date"):
            self.crm_schedule(lead.id, {"date": post["meeting_date"], "time": post.get("meeting_time"),
                                        "kind": "first"})
        return lead

    @api.model
    def crm_move(self, lead_id, stage_key, post=None):
        lead = self._record("crm", lead_id)
        stage = self._crm_named(stage_key) if stage_key in dict(CRM_STAGE_XMLIDS) else \
            self.env["crm.stage"].sudo().browse(self._int(stage_key) or []).exists()
        columns = {self._stage_key(st): st for _k, st in self._crm_stages()}
        if not stage or self._stage_key(stage) not in columns:
            raise UserError(_("Etapa no válida."))
        stage = columns[self._stage_key(stage)]
        vals = {"stage_id": stage.id}
        lost = self._crm_named("lost")
        if lost and self._stage_key(stage) == self._stage_key(lost) and post and post.get("reason"):
            vals["insurance_lost_note"] = post["reason"]
        lead.write(vals)
        return lead

    @api.model
    def crm_update(self, lead_id, post):
        lead = self._record("crm", lead_id)
        vals = {
            "contact_name": post.get("name") or lead.contact_name,
            "phone": post.get("phone") or False,
            "email_from": post.get("email") or False,
            "insurance_policy_type_id": self._int(post.get("ramo_id")),
            "expected_revenue": self._float(post.get("amount")),
        }
        lead.write(vals)
        return lead

    @api.model
    def crm_schedule(self, lead_id, post):
        lead = self._record("crm", lead_id)
        if not post.get("date"):
            raise UserError(_("Elige la fecha de la cita."))
        day = fields.Date.to_date(post["date"])
        kind = post.get("kind") or "first"
        title = {"first": _("1ra cita"), "closing": _("Cita de cierre")}.get(kind, _("Seguimiento"))
        start = False
        if post.get("time"):
            hour, minute = (post["time"].split(":") + ["0"])[:2]
            tz = pytz.timezone(self._user().tz or "America/Mexico_City")
            local = tz.localize(datetime.combine(day, time(int(hour), int(minute))))
            start = local.astimezone(pytz.utc).replace(tzinfo=None)
        task = self.env["insurance.task"].sudo().create({
            "name": "%s: %s" % (title, lead.contact_name or lead.name),
            "task_type": "meeting" if kind in ("first", "closing") else "followup",
            "date_deadline": day,
            "date_start": start,
            "user_id": self._user().id,
            "partner_id": lead.partner_id.id or False,
            "lead_id": lead.id,
        })
        target = self._crm_named(kind) if kind in ("first", "closing") else False
        if target and lead.stage_id != target:
            lead.stage_id = target
        lead.message_post(body=_("%(t)s agendada para el %(d)s") % {"t": title, "d": day.strftime("%d/%m/%Y")},
                          author_id=self._author().id, subtype_xmlid="mail.mt_note")
        return task

    @api.model
    def crm_delete(self, lead_id, reason=None):
        """Quita el prospecto del CRM: se archiva (no se pierde su historial y se puede recuperar
        desde la plataforma) y se cancelan sus actividades pendientes."""
        lead = self._record("crm", lead_id)
        if lead.insurance_policy_id:
            raise UserError(_("Este prospecto ya tiene póliza (%s); no se puede eliminar.") % lead.insurance_policy_id.name)
        name = lead.contact_name or lead.name
        pending = self.env["insurance.task"].sudo().search([("lead_id", "=", lead.id), ("state", "=", "todo")])
        pending.write({"state": "cancelled"})
        lead.message_post(body=_("Eliminado desde el portal por %(who)s.%(why)s") % {
            "who": self._user().name, "why": (" " + _("Motivo: %s") % reason) if reason else ""},
            author_id=self._author().id, subtype_xmlid="mail.mt_note")
        lead.write({"active": False})
        return name

    @api.model
    def crm_tasks(self, lead):
        return self.env["insurance.task"].sudo().search([("lead_id", "=", lead.id)], order="date_deadline desc")

    @api.model
    def crm_to_emission(self, lead_id):
        lead = self._record("crm", lead_id)
        won = self._crm_named("won")
        if won and lead.stage_id != won:
            lead.stage_id = won
        policy = lead.action_insurance_to_emission()
        if self._user()._ins_portal_can("emission") and policy.team_user_id != self._user():
            policy.team_user_id = self._user()
        return policy

    # ------------------------------------------------------------------ actividades del día y registro de avances
    @api.model
    def day_activities(self, crm_only=False, scope="mine"):
        """Actividades pendientes de hoy y atrasadas (las atrasadas primero)."""
        domain = [("state", "=", "todo"), ("date_deadline", "<=", self.today())]
        if not (scope == "all" and self.sees_all()):
            domain.append(("user_id", "=", self._user().id))
        if crm_only:
            domain.append(("lead_id.is_insurance_opportunity", "=", True))
        return self.env["insurance.task"].sudo().search(domain, order="date_deadline, date_start, id", limit=60)

    @api.model
    def team_members(self):
        """Personas a las que se les puede asignar una actividad: equipo del portal y usuarios de Seguros."""
        Users = self.env["res.users"].sudo()
        group = self.env.ref("insurance_management.group_insurance_user", raise_if_not_found=False)
        users = Users.search([("share", "=", False), ("all_group_ids", "in", group.ids)]) if group else Users
        portal_flags = ["|"] * 7 + [("ins_portal_%s" % b, "=", True) for b in
                                     ("portfolio", "emission", "procedure", "claim", "collection", "crm", "renewal", "anniversary")]
        users |= Users.search(portal_flags)
        return (users | self._user()).sorted("name")

    @api.model
    def activity(self, task_id):
        domain = [("id", "=", int(task_id))]
        if not self.sees_all():
            domain += ["|", ("user_id", "=", self._user().id), ("create_uid", "=", self._user().id)]
        task = self.env["insurance.task"].sudo().search(domain, limit=1)
        if not task:
            raise MissingError(_("La actividad no existe o no está asignada a ti."))
        return task

    @api.model
    def activity_links(self, task):
        """A dónde lleva la actividad dentro del portal (prospecto o póliza)."""
        blocks = self.blocks()
        if task.lead_id and blocks.get("crm"):
            return "/equipo/crm/%s" % task.lead_id.id
        if task.policy_id:
            if blocks.get("portfolio") and task.policy_id.agent_id.user_id == self._user():
                return "/equipo/cartera/%s" % task.policy_id.id
            if blocks.get("emission") and task.policy_id.team_user_id == self._user():
                return "/equipo/emisiones/%s" % task.policy_id.id
        return False

    @api.model
    def task_phone(self, task):
        if task.lead_id and task.lead_id.phone:
            return task.lead_id.phone
        return task.partner_id._insurance_phone() if task.partner_id else ""

    @api.model
    def _tags_from_post(self, post):
        """Etiquetas marcadas + una nueva escrita a mano (se crea si no existe)."""
        Tag = self.env["crm.tag"].sudo()
        raw = post.get("tag_ids") or []
        if isinstance(raw, str):
            raw = [raw]
        ids = [int(x) for x in raw if str(x).isdigit()]
        tags = Tag.browse(ids).exists()
        new = (post.get("new_tag") or "").strip()
        if new:
            tag = Tag.search([("name", "=ilike", new)], limit=1) or Tag.create({"name": new[:60], "color": 4})
            tags |= tag
        return tags

    @api.model
    def _schedule(self, post, lead=None, policy=None, partner=None, title_hint=""):
        """Crea la siguiente actividad si pidieron agendar una."""
        if not post.get("next_date"):
            return self.env["insurance.task"]
        day = fields.Date.to_date(post["next_date"])
        types = dict(self.env["insurance.task"]._fields["task_type"].selection)
        task_type = post.get("next_type") if post.get("next_type") in types else "followup"
        start = False
        if post.get("next_time"):
            hour, minute = (post["next_time"].split(":") + ["0"])[:2]
            tz = pytz.timezone(self._user().tz or "America/Mexico_City")
            start = tz.localize(datetime.combine(day, time(int(hour), int(minute)))).astimezone(pytz.utc).replace(tzinfo=None)
        assignee = self.env["res.users"].sudo().browse(self._int(post.get("next_user_id")) or []).exists()
        if assignee and assignee not in self.team_members():
            raise UserError(_("Solo puedes asignar actividades a personas del equipo."))
        who = (lead.contact_name or lead.partner_name or lead.name) if lead else (partner.name if partner else "")
        name = (post.get("next_name") or "").strip() or "%s%s" % (types[task_type], (": " + who) if who else "")
        task = self.env["insurance.task"].sudo().create({
            "name": name,
            "task_type": task_type,
            "date_deadline": day,
            "date_start": start,
            "user_id": (assignee or self._user()).id,
            "partner_id": (partner or (lead.partner_id if lead else False) or self.env["res.partner"]).id or False,
            "lead_id": lead.id if lead else False,
            "policy_id": policy.id if policy else False,
            "description": post.get("next_note") or False,
        })
        if task.user_id != self._user():
            task.message_subscribe(partner_ids=task.user_id.partner_id.ids)
            task.message_post(body=_("%(who)s te asignó esta actividad para el %(day)s.") % {
                "who": self._user().name, "day": day.strftime("%d/%m/%Y")},
                author_id=self._author().id, partner_ids=task.user_id.partner_id.ids,
                message_type="comment", subtype_xmlid="mail.mt_comment")
        return task

    @api.model
    def log_progress(self, post, task=None, lead=None, policy=None, followup_field=None):
        """Registro rápido de lo que se hizo (llamada, WhatsApp, correo…): queda en el historial del
        prospecto o la póliza, cierra la actividad si se pidió y agenda la siguiente."""
        channels = dict(LOG_CHANNELS)
        channel = post.get("channel") if post.get("channel") in channels else False
        text = (post.get("body") or "").strip()
        if task:
            lead = lead or task.lead_id
            policy = policy or task.policy_id
        target = lead or policy or task
        if not (text or channel or post.get("next_date") or post.get("done") or post.get("new_tag")
                or post.get("followup") or post.get("tags_sent")):
            raise UserError(_("Escribe qué se hizo o elige una opción."))
        parts = []
        if channel:
            parts.append("<b>%s</b>" % channels[channel])
        if text:
            parts.append(html_escape(text).replace("\n", "<br/>"))
        if lead:
            tags = self._tags_from_post(post)
            if post.get("tags_sent") or post.get("new_tag"):
                added = tags - lead.tag_ids
                lead.tag_ids = [(6, 0, tags.ids)]
                if added:
                    parts.append(_("Etiquetas: %s") % ", ".join(added.mapped("name")))
        if followup_field and post.get("followup") in dict(FOLLOWUP_STATES):
            policy.write({followup_field: post["followup"]})
            parts.append(_("Avance: %s") % dict(FOLLOWUP_STATES)[post["followup"]])
        if task and parts:
            parts.insert(0, _("Actividad «%s»") % html_escape(task.name))
        if parts and target:
            target.message_post(body=Markup(" · ".join(parts)), author_id=self._author().id,
                                message_type="comment", subtype_xmlid="mail.mt_note")
        if task:
            vals = {}
            if text:
                vals["result"] = ((task.result + "\n") if task.result else "") + (
                    "%s: %s" % (channels[channel], text) if channel else text)
            if vals:
                task.write(vals)
            if post.get("done"):
                task.action_done()
        nxt = self._schedule(post, lead=lead, policy=policy,
                             partner=(task.partner_id if task else False) or (policy.partner_id if policy else False))
        return nxt

    @api.model
    def lead_timeline(self, lead):
        """Seguimiento del prospecto: actividades (pendientes y hechas) y notas, del más reciente al más antiguo."""
        rows = []
        for task in self.crm_tasks(lead):
            when = task.date_start and self.local(task.date_start) or datetime.combine(task.date_deadline, time(9, 0))
            rows.append({"kind": "task", "task": task, "date": when, "sort": when.replace(tzinfo=None)})
        for msg in self.timeline(lead, limit=60):
            date = msg["date"]
            rows.append({"kind": "note", "msg": msg, "date": date, "sort": date.replace(tzinfo=None) if date else datetime.min})
        return sorted(rows, key=lambda r: r["sort"], reverse=True)

    # ------------------------------------------------------------------ renovaciones (GMM) y aniversarios (vida)
    @api.model
    def renewal_policies(self, scope="mine", search=None):
        self._check("renewal")
        domain = [("policy_type_id.renewal_kind", "=", "renewal"), ("renewal_state", "in", ["pending", "in_progress"])]
        if search:
            domain += ["|", ("partner_id.name", "ilike", search), ("name", "ilike", search)]
        return self.env["insurance.policy"].sudo().search(domain, order="end_date, id")

    @api.model
    def anniversary_rows(self, scope="mine", search=None, days=60):
        self._check("anniversary")
        today = self.today()
        domain = [("policy_type_id.renewal_kind", "=", "anniversary"), ("state", "=", "confirmed")]
        if search:
            domain += ["|", ("partner_id.name", "ilike", search), ("name", "ilike", search)]
        rows = []
        for policy in self.env["insurance.policy"].sudo().search(domain):
            nxt = policy.next_anniversary
            if not nxt or nxt > today + relativedelta(days=days):
                continue
            if policy.anniversary_year_done == nxt.year:
                continue
            start = policy.coverage_start_date or policy.issue_date
            followup = policy.anniversary_followup
            if followup in ("accepted", "declined") and policy.anniversary_year_done != nxt.year:
                followup = False  # lo de años anteriores ya no cuenta
            rows.append({
                "policy": policy, "date": nxt, "days": (nxt - today).days,
                "years": nxt.year - start.year if start else 0, "followup": followup,
            })
        return sorted(rows, key=lambda r: r["date"])

    @api.model
    def followup_policy(self, block, policy_id):
        self._check(block)
        kind = "renewal" if block == "renewal" else "anniversary"
        policy = self.env["insurance.policy"].sudo().search(
            [("id", "=", int(policy_id)), ("policy_type_id.renewal_kind", "=", kind)], limit=1)
        if not policy:
            raise MissingError(_("La póliza no existe en esta lista."))
        return policy

    # ------------------------------------------------------------------ mi cartera (asesor)
    @api.model
    def my_agent(self):
        return self.env["insurance.agent"].sudo().search([("user_id", "=", self._user().id)], limit=1)

    @api.model
    def portfolio(self, scope="mine", view="attention", search=None):
        """Todo lo que el asesor necesita de su cartera en una sola consulta."""
        self._check("portfolio")
        today = self.today()
        month_start = today.replace(day=1)
        agents = self.env["insurance.agent"].sudo().search(
            [] if (scope == "all" and self.sees_all()) else [("user_id", "=", self._user().id)])
        Policy = self.env["insurance.policy"].sudo()
        base = [("agent_id", "in", agents.ids)]
        active = Policy.search(base + [("state", "=", "confirmed")])
        attention = active.filtered(lambda p: p.attention_level in ("danger", "warning")).sorted(
            lambda p: (p.attention_level != "danger", p.name))
        renewals = Policy.search(base + [("renewal_state", "in", ["pending", "in_progress"])], order="end_date")
        lines = self.env["insurance.installment"].sudo().search(
            [("policy_id.agent_id", "in", agents.ids), ("collection_status", "in", ["overdue", "due_soon"])],
            order="due_date")
        Commission = self.env["insurance.commission"].sudo()
        commissions = Commission.search([("agent_id", "in", agents.ids)], order="date desc", limit=40)
        month_comm = sum(Commission.search([("agent_id", "in", agents.ids), ("date", ">=", month_start),
                                            ("state", "!=", "cancelled")]).mapped("amount_company"))
        to_pay = sum(Commission.search([("agent_id", "in", agents.ids), ("state", "=", "to_pay")]).mapped("amount_company"))
        Goal = self.env["insurance.goal"].sudo()
        goal_domain = [("agent_id", "in", agents.ids), ("year", "=", today.year)]
        goal = Goal.search(goal_domain + [("month", "=", str(today.month))], limit=1)
        target = goal.income_target if goal else sum(
            Goal.search(goal_domain + [("month", "=", False)]).mapped("income_target")) / 12.0
        clients = active.mapped("partner_id")
        if search:
            needle = search.lower()
            clients = clients.filtered(lambda c: needle in (c.name or "").lower())
            active_view = active.filtered(lambda p: needle in (p.partner_id.name or "").lower() or needle in (p.name or "").lower())
        else:
            active_view = active
        premium = {}
        for policy in active:
            premium[policy.currency_id] = premium.get(policy.currency_id, 0.0) + (policy.policy_amount or 0.0)
        return {
            "agents": agents,
            "active": active,
            "active_view": active_view.sorted(lambda p: (p.partner_id.name or "")),
            "attention": attention,
            "renewals": renewals,
            "lines": lines,
            "commissions": commissions,
            "clients": clients.sorted("name"),
            "premium": premium,
            "month_comm": month_comm,
            "to_pay": to_pay,
            "target": target,
            "progress": min(100, int(round(month_comm * 100.0 / target))) if target else 0,
            "currency": self.env.company.currency_id,
            "counts": {
                "attention": len(attention), "renewals": len(renewals), "collection": len(lines),
                "policies": len(active), "commissions": len(commissions),
            },
        }

    @api.model
    def portfolio_policy(self, policy_id):
        return self._record("portfolio", policy_id)

    # ------------------------------------------------------------------ mover (arrastrar y soltar)
    @api.model
    def move(self, block, record_id, column):
        if block == "emission":
            return self.emission_move(record_id, column)
        if block == "procedure":
            return self.procedure_move(record_id, column)
        if block == "crm":
            return self.crm_move(record_id, column)
        raise UserError(_("Aquí no se puede arrastrar."))
