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
from odoo.tools import html2plaintext

from .insurance_team import EMISSION_STAGES, PROCEDURE_STATES

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
    "emission": "insurance.policy",
    "procedure": "insurance.procedure",
    "claim": "insurance.claim",
    "collection": "insurance.installment",
    "crm": "crm.lead",
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
        if scope == "all" and self.sees_all():
            return []
        if block == "procedure":
            return [("user_id", "=", uid)]
        if block == "crm":
            return ["|", ("team_user_id", "=", uid), ("user_id", "=", uid)]
        if block == "collection":
            return []  # quien tiene Cobranza ve toda la cobranza
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
            text = html2plaintext(msg.body or "").strip()
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
                self._mine_domain("crm") + [("stage_id", "in", self._crm_stage_ids()[:3])]
            )
            cards["crm"] = {"count": len(leads), "hot": len(leads.filtered(
                lambda l: l.activity_date_deadline and l.activity_date_deadline <= today)),
                "hot_label": _("con cita hoy o antes")}
        tasks = self.env["insurance.task"].sudo().search([
            ("user_id", "=", user.id), ("state", "=", "todo"),
            ("date_deadline", "<=", today + relativedelta(days=1)),
        ], limit=20)
        return {"cards": cards, "tasks": tasks}

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
    def _crm_stages(self):
        stages = []
        for key, xmlid in CRM_STAGE_XMLIDS:
            stage = self.env.ref(xmlid, raise_if_not_found=False)
            if stage:
                stages.append((key, stage.sudo()))
        return stages

    @api.model
    def _crm_stage_ids(self):
        return [stage.id for _key, stage in self._crm_stages()]

    @api.model
    def crm_board(self, scope="mine"):
        self._check("crm")
        stages = self._crm_stages()
        leads = self.env["crm.lead"].sudo().search(
            self._mine_domain("crm", scope) + [("stage_id", "in", [s.id for _k, s in stages]),
                                                ("type", "=", "opportunity")],
            order="priority desc, write_date desc",
        )
        return [(key, stage, leads.filtered(lambda l, s=stage: l.stage_id == s)) for key, stage in stages]

    @api.model
    def crm_create(self, post):
        self._check("crm")
        name = (post.get("name") or "").strip()
        if not name:
            raise UserError(_("Escribe el nombre del prospecto."))
        stages = dict(self._crm_stages())
        user = self._user()
        vals = {
            "name": post.get("title") or name,
            "contact_name": name,
            "phone": post.get("phone") or False,
            "email_from": post.get("email") or False,
            "type": "opportunity",
            "stage_id": stages["first"].id if "first" in stages else False,
            "team_user_id": user.id,
            "user_id": user.id if not user.share else False,
            "insurance_policy_type_id": self._int(post.get("ramo_id")),
            "insurance_origin": post.get("origin") if post.get("origin") in dict(
                self.env["crm.lead"]._fields["insurance_origin"]._description_selection(self.env)) else "new",
            "expected_revenue": self._float(post.get("amount")),
            "description": post.get("note") or False,
        }
        lead = self.env["crm.lead"].sudo().create({k: v for k, v in vals.items() if v is not False})
        if post.get("meeting_date"):
            self.crm_schedule(lead.id, {"date": post["meeting_date"], "time": post.get("meeting_time"),
                                        "kind": "first"})
        return lead

    @api.model
    def crm_move(self, lead_id, stage_key, post=None):
        lead = self._record("crm", lead_id)
        stages = dict(self._crm_stages())
        if stage_key not in stages:
            raise UserError(_("Etapa no válida."))
        vals = {"stage_id": stages[stage_key].id}
        if stage_key == "lost" and post and post.get("reason"):
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
        stages = dict(self._crm_stages())
        if kind in stages and lead.stage_id != stages[kind]:
            lead.stage_id = stages[kind]
        lead.message_post(body=_("%(t)s agendada para el %(d)s") % {"t": title, "d": day.strftime("%d/%m/%Y")},
                          author_id=self._author().id, subtype_xmlid="mail.mt_note")
        return task

    @api.model
    def crm_tasks(self, lead):
        return self.env["insurance.task"].sudo().search([("lead_id", "=", lead.id)], order="date_deadline desc")

    @api.model
    def crm_to_emission(self, lead_id):
        lead = self._record("crm", lead_id)
        stages = dict(self._crm_stages())
        if "won" in stages and lead.stage_id != stages["won"]:
            lead.stage_id = stages["won"]
        policy = lead.action_insurance_to_emission()
        if self._user()._ins_portal_can("emission") and policy.team_user_id != self._user():
            policy.team_user_id = self._user()
        return policy

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
