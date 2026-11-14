# -*- coding: utf-8 -*-
"""Importar la cartera descargada del portal de agentes de Seguros Monterrey.

Acepta, juntos y en cualquier cantidad:
* la lista de pólizas exportada desde «Consultas › Pólizas» (archivo .xls), y
* las páginas «Consulta de datos generales de la Póliza» guardadas desde el navegador (.html).

Crea o actualiza clientes, asegurados (con fecha de nacimiento), pólizas, plan, forma y medio de
cobro, domicilio y contacto. Si una póliza ya existe, solo completa lo que le falta y actualiza el
estatus; no pisa lo capturado a mano ni lo leído de la carátula.
"""

import base64
from html import escape

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models import smnyl_portal_parser as smnyl

STATUS_MAP = {
    "en vigor": "confirmed",
    "vigor prorrogado": "confirmed",
    "vigente": "confirmed",
    "anulada": "cancelled",
    "cancelada": "cancelled",
    "rescatada": "cancelled",
    "vencida": "expired",
}
PAYMENT_MAP = {
    "mensual": "monthly",
    "trimestral": "quarterly",
    "semestral": "semiannual",
    "anual": "yearly",
}
# Producto del portal → (ramo, oferta, sub-oferta) por xmlid; el resto queda como plan/sub-oferta en texto
PRODUCT_RULES = [
    ("alfa medical flex a", "medical", "offer_gmm_flexible", "offer_gmm_flexible_flexa"),
    ("alfa medical flex b", "medical", "offer_gmm_flexible", "offer_gmm_flexible_flexb"),
    ("alfa medical internacional", "medical", "offer_gmm_internacional", None),
    ("internacional", "medical", "offer_gmm_internacional", None),
    ("pleno", "medical", "offer_gmm_tradicional", "offer_gmm_tradicional_plenos"),
    ("integro", "medical", "offer_gmm_tradicional", "offer_gmm_tradicional_integros"),
    ("practico", "medical", "offer_gmm_tradicional", "offer_gmm_tradicional_practicos"),
    ("orvi", "life", "offer_vida_orvi", None),
    ("dotal", "life", "offer_vida_dotal", None),
    ("segubeca", "life", "offer_vida_segubeca", None),
    ("vida mujer", "life", "offer_vida_mujer", None),
    ("imagina ser", "life", "offer_vida_imagina_ser", None),
    ("nuevo plenitud", "life", "offer_vida_nuevo_plenitud", None),
]


class InsuranceSmnylImport(models.TransientModel):
    _name = "insurance.smnyl.import"
    _description = "Importar cartera del portal Seguros Monterrey"

    attachment_ids = fields.Many2many(
        "ir.attachment", "insurance_smnyl_import_attachment_rel", "wizard_id", "attachment_id",
        string="Archivos", help="Lista de pólizas (.xls) y/o páginas de detalle guardadas (.html).",
    )
    insurance_company_id = fields.Many2one(
        "insurance.company", string="Aseguradora",
        default=lambda self: self.env["insurance.company"].search([("name", "ilike", "monterrey")], limit=1),
    )
    agent_id = fields.Many2one("insurance.agent", string="Asesor si no se reconoce la clave")
    include_cancelled = fields.Boolean(
        string="Incluir pólizas anuladas",
        help="Si no se marca, las anuladas que aún no existen en el sistema se omiten. "
             "Las que ya existen siempre se actualizan a «Cancelado».",
    )
    attach_pages = fields.Boolean(string="Guardar la página de detalle en la póliza", default=True)
    state = fields.Selection([("draft", "Por importar"), ("done", "Listo")], default="draft")
    result_html = fields.Html(string="Resultado", readonly=True, sanitize=False)

    # ------------------------------------------------------------------ utilidades
    @api.model
    def _ramo(self, key):
        return self.env.ref("insurance_management.policy_type_%s" % key, raise_if_not_found=False)

    @api.model
    def _offer(self, xmlid):
        return self.env.ref("insurance_management.%s" % xmlid, raise_if_not_found=False) if xmlid else False

    def _classify(self, number, product, kind=""):
        """Ramo, oferta y sub-oferta a partir del producto (o del tipo de seguro / prefijo)."""
        text = smnyl.norm(product)
        for needle, ramo_key, offer_xmlid, sub_xmlid in PRODUCT_RULES:
            if text.startswith(needle) or needle in text:
                return self._ramo(ramo_key), self._offer(offer_xmlid), self._offer(sub_xmlid)
        kind_n = smnyl.norm(kind)
        if (number or "").startswith("GM") or "gastos medicos" in kind_n:
            return self._ramo("medical"), False, False
        if (number or "").startswith("VI") or "vida" in kind_n:
            return self._ramo("life"), False, False
        return False, False, False

    def _partner(self, name, cache):
        name = " ".join((name or "").split())
        if not name:
            return self.env["res.partner"]
        key = name.lower()
        if key not in cache:
            Partner = self.env["res.partner"]
            cache[key] = Partner.search([("name", "=ilike", name)], limit=1) or Partner.create({"name": name})
        return cache[key]

    def _currency(self, text, product):
        n = smnyl.norm(text) + " " + smnyl.norm(product)
        code = "UDI" if "udi" in n else ("USD" if "dolar" in n or "usd" in n else ("MXN" if "peso" in n else None))
        if not code:
            return False
        return self.env["res.currency"].with_context(active_test=False).search([("name", "=", code)], limit=1)

    @staticmethod
    def _policy_year_start(emission):
        """Inicio del año póliza en curso (las pólizas de vida son multianuales y GMM se renueva cada año)."""
        today = fields.Date.today()
        start = emission
        years = 1
        while True:
            candidate = emission + relativedelta(years=years)
            if candidate > today:
                break
            start = candidate
            years += 1
        return start

    def _state_id(self, name):
        if not name:
            return False
        mexico = self.env.ref("base.mx", raise_if_not_found=False)
        target = smnyl.norm(name)
        for st in self.env["res.country.state"].search([("country_id", "=", mexico.id)] if mexico else []):
            if smnyl.norm(st.name) == target or smnyl.norm(st.code) == target:
                return st.id
        return False

    def _fill(self, record, vals):
        """Solo escribe lo que el registro aún no tiene."""
        missing = {k: v for k, v in vals.items() if v not in (False, None, "", 0, 0.0) and not record[k]}
        if missing:
            record.write(missing)
        return missing

    # ------------------------------------------------------------------ importación
    def action_import(self):
        self.ensure_one()
        if not self.attachment_ids:
            raise UserError(_("Sube la lista de pólizas (.xls) y/o las páginas de detalle guardadas (.html)."))
        rows, details, ignored = {}, {}, []
        for att in self.attachment_ids:
            data = base64.b64decode(att.datas or b"")
            kind = smnyl.detect(data)
            if kind == "bundle":
                b_rows, b_details = smnyl.split_bundle(data)
                for row in b_rows:
                    rows[row["number"]] = row
                for number, page in b_details:
                    info = smnyl.parse_detail(page)
                    if info.get("number"):
                        info["_html"] = page
                        details[info["number"]] = info
            elif kind == "list":
                for row in smnyl.parse_list(data):
                    rows[row["number"]] = row
            elif kind == "detail":
                info = smnyl.parse_detail(data)
                if info.get("number"):
                    info["_html"] = data
                    details[info["number"]] = info
                else:
                    ignored.append(att.name)
            else:
                ignored.append(att.name)
        if not rows and not details:
            raise UserError(_("No reconocí ningún archivo del portal de Seguros Monterrey."))

        Policy = self.env["insurance.policy"]
        cache, log = {}, []
        counts = {"created": 0, "updated": 0, "skipped": 0}
        for number in sorted(set(rows) | set(details)):
            row, info = rows.get(number, {}), details.get(number, {})
            status = smnyl.norm(info.get("status") or row.get("status"))
            state = STATUS_MAP.get(status, "confirmed" if "vigor" in status else False)
            policy = Policy.search([("name", "=", number)], limit=1)
            if not policy and state == "cancelled" and not self.include_cancelled:
                counts["skipped"] += 1
                log.append((number, _("Omitida (anulada)"), row.get("contractor") or ""))
                continue
            notes = []
            product = info.get("plan") or row.get("product") or ""
            ramo, offer, sub_offer = self._classify(number, product, info.get("kind"))
            contractor = self._partner(info.get("contractor") or row.get("contractor"), cache)
            people = info.get("people") or []
            principal_name = next((p["name"] for p in people if p["principal"]), row.get("insured") or "")
            principal = self._partner(principal_name, cache) if principal_name else contractor
            if not contractor:
                contractor = policy.partner_id or principal

            # domicilio y contacto (en el contratante; si es la misma persona, en el asegurado)
            if info and contractor:
                address = {
                    "street": info.get("street"), "street2": info.get("colonia"), "city": info.get("city"),
                    "zip": info.get("zip"), "state_id": self._state_id(info.get("state")),
                    "country_id": self.env.ref("base.mx").id if "mex" in smnyl.norm(info.get("country") or "mexico") else False,
                    "email": info.get("email"), "phone": info.get("phone"),
                }
                if self._fill(contractor, address):
                    notes.append(_("domicilio"))

            agent_key = False
            agent = self.agent_id
            if info.get("agent_code"):
                agent_key = self.env["insurance.agent.key"].search([("name", "=", info["agent_code"])], limit=1)
                agent = agent_key.agent_id if agent_key else agent
            currency = self._currency(info.get("currency"), product)
            emission = info.get("emission_date")
            vals = {
                "partner_id": contractor.id if contractor else False,
                "policy_type_id": ramo.id if ramo else False,
                "offer_id": offer.id if offer else False,
                "sub_offer_id": sub_offer.id if sub_offer else False,
                "sub_offer_text": product if (offer and not sub_offer and ramo == self._ramo("life")) else False,
                "plan_basic": product or False,
                "insurance_company_id": self.insurance_company_id.id or False,
                "agent_id": agent.id if agent else False,
                "agent_key_id": agent_key.id if agent_key else False,
                "insured_partner_id": principal.id if principal else False,
                "insured_amount": info.get("insured_amount") or False,
                "currency_id": currency.id if currency else False,
                "premium_type": PAYMENT_MAP.get(smnyl.norm(info.get("payment_form"))),
            }
            if emission:
                vals["emission_date"] = emission
                vals["coverage_start_date"] = self._policy_year_start(emission)
            channel = smnyl.norm(info.get("collection_channel"))
            if channel:
                vals["collection_channel"] = "domiciled" if any(w in channel for w in ("cargo", "domicil", "automat", "tarjeta")) else "direct"
            if info.get("collection_day"):
                vals.update({"collection_date_mode": "fixed", "collection_day": info["collection_day"]})
            vals = {k: v for k, v in vals.items() if v not in (False, None, "")}

            if not policy:
                if not vals.get("partner_id"):
                    counts["skipped"] += 1
                    log.append((number, _("Omitida: sin contratante"), ""))
                    continue
                vals.update({"name": number, "policy_amount": 0.0, "emission_stage": "delivered"})
                policy = Policy.create(vals)
                counts["created"] += 1
                action = _("Creada")
            else:
                filled = self._fill(policy, {k: v for k, v in vals.items() if k in policy._fields})
                counts["updated"] += 1
                action = _("Actualizada") if filled else _("Sin cambios")
                if filled:
                    notes.append(_("%s dato(s)") % len(filled))
            if state and policy.state != state:
                policy.write({"state": state})
                notes.append(_("estatus: %s") % dict(policy._fields["state"].selection).get(state))

            # asegurados con fecha de nacimiento
            for person in people:
                member = policy.member_ids.filtered(lambda m, n=person["name"]: smnyl.norm(m.name) == smnyl.norm(n))[:1]
                mvals = {"name": person["name"], "insured_role": "insured",
                         "relationship": "holder" if person["principal"] else (member.relationship or "other")}
                if person["birth_date"]:
                    mvals["birth_date"] = person["birth_date"]
                if person["principal"] and principal:
                    mvals["partner_id"] = principal.id
                    if person["birth_date"] and not principal.mx_birth_date:
                        principal.mx_birth_date = person["birth_date"]
                if member:
                    member.write(mvals)
                else:
                    policy.member_ids = [(0, 0, mvals)]
            if people:
                notes.append(_("%s asegurado(s)") % len(people))

            if info.get("_html") and self.attach_pages:
                self.env["ir.attachment"].search([
                    ("res_model", "=", "insurance.policy"), ("res_id", "=", policy.id),
                    ("name", "=", "Portal SMNYL - %s.html" % number),
                ]).unlink()
                self.env["ir.attachment"].create({
                    "name": "Portal SMNYL - %s.html" % number,
                    "datas": base64.b64encode(info["_html"]),
                    "res_model": "insurance.policy", "res_id": policy.id, "mimetype": "text/html",
                })
            log.append((number, action, ", ".join(notes) or (row.get("contractor") or "")))

        self.write({"state": "done", "result_html": self._result_html(counts, log, ignored)})
        return {
            "type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
            "view_mode": "form", "target": "new", "name": _("Importar del portal Seguros Monterrey"),
        }

    @staticmethod
    def _result_html(counts, log, ignored):
        parts = [
            "<p><b>%s</b> creadas · <b>%s</b> actualizadas · <b>%s</b> omitidas</p>"
            % (counts["created"], counts["updated"], counts["skipped"])
        ]
        if ignored:
            parts.append("<p class='text-warning'>No reconocidos: %s</p>" % escape(", ".join(ignored)))
        parts.append("<table class='table table-sm'><thead><tr><th>Póliza</th><th>Resultado</th><th>Detalle</th></tr></thead><tbody>")
        for number, action, note in log[:600]:
            parts.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (escape(number), escape(action), escape(note)))
        parts.append("</tbody></table>")
        return "".join(parts)
