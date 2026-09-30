# -*- coding: utf-8 -*-

import base64
import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from ..models.caratula_parser import CaratulaParseError, normalize, parse_caratula

_logger = logging.getLogger(__name__)

PAYMENT_FORMS = {
    "MENSUAL": "monthly",
    "TRIMESTRAL": "quarterly",
    "SEMESTRAL": "semiannual",
    "ANUAL": "yearly",
}
CURRENCY_ALIASES = {
    "UDI": "UDI", "UDIS": "UDI",
    "PESOS": "MXN", "MN": "MXN", "M.N.": "MXN", "MXN": "MXN", "MONEDA NACIONAL": "MXN",
    "DOLARES": "USD", "DOLAR": "USD", "USD": "USD", "DLS": "USD",
}


class InsurancePolicyPdfImport(models.TransientModel):
    _name = "insurance.policy.pdf.import"
    _description = "Cargar carátula de póliza desde PDF"

    state = fields.Selection(
        [("upload", "Cargar PDF"), ("preview", "Revisar datos")],
        default="upload",
        required=True,
    )
    pdf_file = fields.Binary(string="Carátula (PDF)", required=True, attachment=False)
    pdf_filename = fields.Char(string="Archivo")
    policy_id = fields.Many2one(
        "insurance.policy",
        string="Póliza a actualizar",
        help="Vacío = se crea una póliza nueva. Si ya existe una con el mismo número se propone actualizarla.",
    )
    warning = fields.Text(readonly=True)

    # Datos leídos (editables antes de guardar)
    policy_number = fields.Char(string="Número de póliza")
    ramo_id = fields.Many2one("insurance.policy.type", string="Ramo")
    plan_basic = fields.Char(string="Plan básico")
    offer_id = fields.Many2one(
        "insurance.offer",
        string="Oferta",
        domain="[('ramo_id', '=', ramo_id), ('parent_id', '=', False)]",
    )
    sub_offer_id = fields.Many2one(
        "insurance.offer",
        string="Sub-oferta",
        domain="[('parent_id', '=', offer_id)]",
    )
    sub_offer_text = fields.Char(string="Sub-oferta (texto libre)")
    policy_kind = fields.Char(string="Tipo de póliza")
    contractor_name = fields.Char(string="Contratante")
    contractor_partner_id = fields.Many2one(
        "res.partner",
        string="Contacto del contratante",
        help="Vacío = se crea un contacto nuevo con los datos de la carátula.",
    )
    insured_name = fields.Char(string="Asegurado")
    insured_partner_id = fields.Many2one(
        "res.partner",
        string="Contacto del asegurado",
        help="Vacío = se usa el contratante si es la misma persona, o se crea un contacto nuevo.",
    )
    residence = fields.Char(string="Residencia")
    emission_date = fields.Date(string="Fecha de emisión")
    maturity_date = fields.Date(string="Fecha de vencimiento")
    premium_type = fields.Selection(
        [
            ("monthly", "Mensual"),
            ("quarterly", "Trimestral"),
            ("semiannual", "Semestral"),
            ("yearly", "Anual"),
        ],
        string="Forma de pago",
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Moneda",
        domain="[('name', 'in', ('MXN', 'USD', 'UDI'))]",
    )
    birth_date = fields.Date(string="Fecha de nacimiento")
    age = fields.Integer(string="Edad")
    gender = fields.Selection([("female", "Femenino"), ("male", "Masculino")], string="Sexo")
    address = fields.Text(string="Domicilio completo")
    street = fields.Char(string="Calle y colonia")
    zip = fields.Char(string="C.P.")
    city = fields.Char(string="Ciudad")
    state_id = fields.Many2one("res.country.state", string="Estado")
    settlement_option = fields.Char(string="Opción de liquidación")
    premium_total = fields.Monetary(string="Prima emitida", currency_field="currency_id")
    line_ids = fields.One2many("insurance.policy.pdf.import.line", "wizard_id", string="Beneficios")

    # ------------------------------------------------------------------
    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "name": _("Cargar carátula PDF"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _find_partner(self, name):
        if not name:
            return self.env["res.partner"]
        return self.env["res.partner"].search([("name", "=ilike", name.strip())], limit=1)

    def _find_state(self, state_name):
        if not state_name:
            return self.env["res.country.state"]
        mexico = self.env.ref("base.mx", raise_if_not_found=False)
        domain = [("country_id", "=", mexico.id)] if mexico else []
        target = normalize(state_name)
        for state in self.env["res.country.state"].search(domain):
            if normalize(state.name) == target or normalize(state.code) == target:
                return state
        return self.env["res.country.state"]

    def _match_offer(self, ramo, plan):
        """Plan básico → oferta del ramo (la de nombre más largo contenido al inicio del plan)."""
        Offer = self.env["insurance.offer"]
        if not ramo or not plan:
            return Offer, Offer
        plan_norm = normalize(plan)
        best = Offer
        for offer in Offer.search([("ramo_id", "=", ramo.id), ("parent_id", "=", False)]):
            name = normalize(offer.name)
            if plan_norm.startswith(name) and len(name) > len(normalize(best.name or "")):
                best = offer
        sub = Offer
        if best and best.child_ids:
            for child in best.child_ids:
                if normalize(child.name) in plan_norm:
                    sub = child
                    break
        return best, sub

    def _detect_ramo(self, data):
        PolicyType = self.env["insurance.policy.type"]
        title = normalize(data.get("title"))
        code = False
        if "GASTOS MEDICOS" in title:
            code = "SEGGMM"
        elif "VIDA" in title or normalize(data.get("policy_number", "")).startswith("VI"):
            code = "SEGVIDA"
        elif "AUTO" in title:
            code = "SEGAUT"
        elif "ACCIDENTE" in title:
            code = "SEGACC"
        return PolicyType.search([("code", "=", code)], limit=1) if code else PolicyType

    # ------------------------------------------------------------------
    def action_read_pdf(self):
        self.ensure_one()
        if not self.pdf_file:
            raise UserError(_("Adjunte el PDF de la carátula."))
        try:
            data = parse_caratula(base64.b64decode(self.pdf_file))
        except CaratulaParseError as err:
            raise UserError(str(err)) from err
        except Exception as err:
            _logger.exception("Error leyendo carátula PDF")
            raise UserError(_("No se pudo leer el PDF: %s") % err) from err

        ramo = self._detect_ramo(data)
        offer, sub_offer = self._match_offer(ramo, data.get("plan_basic"))
        sub_offer_text = False
        if offer and offer.sub_offer_free_text and normalize(data.get("plan_basic")) != normalize(offer.name):
            sub_offer_text = data.get("plan_basic")
        currency_code = CURRENCY_ALIASES.get(normalize(data.get("currency")), False)
        currency = (
            self.env["res.currency"].with_context(active_test=False).search([("name", "=", currency_code)], limit=1)
            if currency_code else self.env["res.currency"]
        )
        gender_norm = normalize(data.get("gender"))
        gender = "female" if gender_norm.startswith("FEM") else ("male" if gender_norm.startswith("MAS") else False)
        contractor = self._find_partner(data.get("contractor"))
        insured = self._find_partner(data.get("insured"))
        existing = self.policy_id or self.env["insurance.policy"].search(
            [("name", "=", data["policy_number"])], limit=1
        )
        warnings = []
        if existing and not self.policy_id:
            warnings.append(_("Ya existe la póliza %s: se actualizará con los datos de esta carátula.") % existing.name)
        if not ramo:
            warnings.append(_("No se identificó el ramo; selecciónelo."))
        if data.get("plan_basic") and not offer:
            warnings.append(_("El plan básico «%s» no coincide con ninguna oferta del ramo; selecciónela o créela.") % data["plan_basic"])
        if data.get("currency") and not currency:
            warnings.append(_("No se reconoció la moneda «%s».") % data["currency"])
        if not data.get("benefits"):
            warnings.append(_("No se encontraron beneficios en la tabla de la carátula."))

        self.write({
            "state": "preview",
            "policy_id": existing.id if existing else False,
            "warning": "\n".join(warnings) or False,
            "policy_number": data.get("policy_number"),
            "ramo_id": ramo.id if ramo else False,
            "plan_basic": data.get("plan_basic"),
            "offer_id": offer.id if offer else False,
            "sub_offer_id": sub_offer.id if sub_offer else False,
            "sub_offer_text": sub_offer_text,
            "policy_kind": data.get("policy_kind"),
            "contractor_name": data.get("contractor"),
            "contractor_partner_id": contractor.id if contractor else False,
            "insured_name": data.get("insured"),
            "insured_partner_id": insured.id if insured else False,
            "residence": data.get("residence"),
            "emission_date": data.get("emission_date") or False,
            "maturity_date": data.get("maturity_date") or False,
            "premium_type": PAYMENT_FORMS.get(normalize(data.get("payment_form")), False),
            "currency_id": currency.id if currency else False,
            "birth_date": data.get("birth_date") or False,
            "age": data.get("age") or 0,
            "gender": gender,
            "address": data.get("address"),
            "street": data.get("street"),
            "zip": data.get("zip"),
            "city": (data.get("city") or "").title() or False,
            "state_id": self._find_state(data.get("state")).id or False,
            "settlement_option": data.get("settlement_option"),
            "premium_total": data.get("premium_total") or 0.0,
            "line_ids": [(5, 0, 0)] + [
                (0, 0, {
                    "sequence": index * 10,
                    "section": benefit["section"],
                    "code": benefit["code"],
                    "insured_amount": benefit["insured_amount"] or 0.0,
                    "annex": benefit["annex"],
                    "effective_date": benefit["effective_date"] or False,
                    "coverage_years": benefit["coverage_years"],
                    "payment_years": benefit["payment_years"],
                    "premium": benefit["premium"],
                    "no_cost": benefit["no_cost"],
                })
                for index, benefit in enumerate(data.get("benefits") or [])
            ],
        })
        return self._reopen()

    def action_back(self):
        self.ensure_one()
        self.state = "upload"
        return self._reopen()

    # ------------------------------------------------------------------
    def _partner_address_vals(self):
        mexico = self.env.ref("base.mx", raise_if_not_found=False)
        return {
            "street": self.street or False,
            "zip": self.zip or False,
            "city": self.city or False,
            "state_id": self.state_id.id or False,
            "country_id": mexico.id if mexico else False,
        }

    def _get_contractor(self):
        partner = self.contractor_partner_id
        if not partner:
            if not self.contractor_name:
                raise UserError(_("Indique el contratante."))
            partner = self.env["res.partner"].create(dict(
                self._partner_address_vals(), name=self.contractor_name.strip(),
            ))
        elif not partner.street:
            partner.write(self._partner_address_vals())
        return partner

    def _get_insured(self, contractor):
        partner = self.insured_partner_id
        if not partner:
            if not self.insured_name or normalize(self.insured_name) == normalize(contractor.name):
                partner = contractor
            else:
                partner = self.env["res.partner"].create(dict(
                    self._partner_address_vals(), name=self.insured_name.strip(),
                ))
        if self.birth_date and not partner.mx_birth_date:
            partner.mx_birth_date = self.birth_date
        return partner

    def _current_policy_year_start(self, emission):
        """Aniversario vigente: inicio del año póliza en curso (las pólizas de vida son multianuales)."""
        today = fields.Date.context_today(self)
        start = emission
        years = 0
        while True:
            candidate = emission + relativedelta(years=years + 1)
            if candidate > today:
                break
            years += 1
            start = candidate
        return start

    def action_create_policy(self):
        self.ensure_one()
        if not self.ramo_id:
            raise UserError(_("Seleccione el ramo."))
        if not self.policy_number:
            raise UserError(_("Indique el número de póliza."))
        contractor = self._get_contractor()
        insured = self._get_insured(contractor)
        Benefit = self.env["insurance.benefit"]
        benefit_commands = [(5, 0, 0)] + [
            (0, 0, {
                "sequence": line.sequence,
                "section": line.section,
                "benefit_id": Benefit._get_or_create(line.code, self.ramo_id).id,
                "insured_amount": line.insured_amount,
                "annex": line.annex,
                "effective_date": line.effective_date,
                "coverage_years": line.coverage_years,
                "payment_years": line.payment_years,
                "premium": line.premium,
                "no_cost": line.no_cost,
            })
            for line in self.line_ids.sorted("sequence")
            if line.code
        ]
        main_amount = next((l.insured_amount for l in self.line_ids.sorted("sequence") if l.insured_amount), 0.0)
        caratula_vals = {
            "plan_basic": self.plan_basic,
            "insured_partner_id": insured.id,
            "residence": self.residence,
            "policy_kind": self.policy_kind,
            "maturity_date": self.maturity_date,
            "insured_birth_date": self.birth_date,
            "insured_age": self.age,
            "insured_gender": self.gender,
            "insured_address": self.address,
            "settlement_option": self.settlement_option,
            "benefit_ids": benefit_commands,
        }
        core_vals = {
            "name": self.policy_number.strip(),
            "partner_id": contractor.id,
            "policy_type_id": self.ramo_id.id,
            "offer_id": self.offer_id.id or False,
            "sub_offer_id": self.sub_offer_id.id or False,
            "sub_offer_text": self.sub_offer_text or False,
            "policy_amount": self.premium_total,
            "insured_amount": main_amount,
        }
        if self.emission_date:
            core_vals.update({
                "emission_date": self.emission_date,
                "coverage_start_date": self._current_policy_year_start(self.emission_date),
            })
        if self.premium_type:
            core_vals["premium_type"] = self.premium_type
        if self.currency_id:
            core_vals["currency_id"] = self.currency_id.id

        policy = self.policy_id
        if policy:
            vals = dict(caratula_vals)
            if policy.state == "draft":
                vals.update(core_vals)
            policy.write(vals)
        else:
            policy = self.env["insurance.policy"].create(dict(caratula_vals, **core_vals))

        # Asegurado en la póliza
        member = policy.member_ids.filtered(lambda m: m.partner_id == insured)[:1]
        member_vals = {
            "partner_id": insured.id,
            "name": insured.name,
            "insured_role": "insured",
            "relationship": "holder",
            "birth_date": self.birth_date,
            "gender": self.gender,
        }
        if member:
            member.write(member_vals)
        else:
            policy.member_ids = [(0, 0, member_vals)]

        # PDF al expediente del asegurado
        attachment = self.env["ir.attachment"].create({
            "name": self.pdf_filename or "%s_caratula.pdf" % policy.name,
            "datas": self.pdf_file,
            "res_model": "insurance.policy",
            "res_id": policy.id,
            "mimetype": "application/pdf",
        })
        doc_type = self.env.ref("insurance_management.document_type_contract", raise_if_not_found=False) or \
            self.env["insurance.document.type"].search([("code", "=", "CON")], limit=1)
        if doc_type:
            document = policy.document_ids.filtered(
                lambda d: d.document_type_id == doc_type and not d.claim_id
            )[:1]
            if document:
                document.write({
                    "attachment_ids": [(4, attachment.id)],
                    "partner_id": insured.id,
                    "state": "uploaded" if document.state == "pending" else document.state,
                })
                document._sync_documents_workspace()
            else:
                document = self.env["insurance.document"].create({
                    "partner_id": insured.id,
                    "policy_id": policy.id,
                    "document_type_id": doc_type.id,
                    "attachment_ids": [(6, 0, attachment.ids)],
                    "state": "uploaded",
                })
            policy.caratula_document_id = document.id
        policy.message_post(
            body=_("Datos cargados desde la carátula PDF %s.") % (self.pdf_filename or ""),
            attachment_ids=attachment.ids,
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Póliza"),
            "res_model": "insurance.policy",
            "res_id": policy.id,
            "view_mode": "form",
            "target": "current",
        }


class InsurancePolicyPdfImportLine(models.TransientModel):
    _name = "insurance.policy.pdf.import.line"
    _description = "Beneficio leído de la carátula"
    _order = "sequence, id"

    wizard_id = fields.Many2one("insurance.policy.pdf.import", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    section = fields.Char(string="Sección")
    code = fields.Char(string="Beneficio", required=True)
    insured_amount = fields.Monetary(string="Suma asegurada inicial", currency_field="currency_id")
    annex = fields.Char(string="Anexo")
    effective_date = fields.Date(string="Fecha de efectividad")
    coverage_years = fields.Char(string="Cobertura (años)")
    payment_years = fields.Char(string="Periodo de pago (años)")
    premium = fields.Monetary(string="Prima inicial", currency_field="currency_id")
    no_cost = fields.Boolean(string="Sin costo")
    currency_id = fields.Many2one(related="wizard_id.currency_id")
