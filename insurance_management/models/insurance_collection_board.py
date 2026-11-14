# -*- coding: utf-8 -*-
"""Cobranza por ramo → categoría → producto (especificación «Modificaciones al módulo de Cobranza»).

* insurance.collection.node: árbol de botones (ramos, categorías y productos) con palabras clave
  para clasificar cada póliza en UN solo producto (sin duplicados).
* En la póliza: fecha del próximo pago, color/estado de cobranza, confirmación de pago y avisos.
* insurance.collection.notice: historial de avisos de cobranza (preparado / enviado / entregado).
"""

import re
import unicodedata
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .insurance_policy import PREMIUM_PERIODS

ICP_DAYS = "insurance_management.coll_notice_days"
ICP_TEMPLATE = "insurance_management.coll_notice_template"
DEFAULT_DAYS = 7
DEFAULT_TEMPLATE = (
    "*AVISO ANTICIPADO DE COBRANZA*\n\n"
    "Buen día, {cliente}.\n"
    "Le saludamos de RP Asesores para recordarle que su próximo pago de la póliza {poliza}, "
    "correspondiente a {producto}, tiene como fecha de vencimiento el {fecha}.\n"
    "El importe correspondiente es de {importe}.\n"
    "Le agradecemos tomar las previsiones necesarias para mantener su póliza al corriente.\n"
    "Quedamos atentos a cualquier duda o aclaración.\n"
    "RP Asesores"
)
OVERDUE_TEMPLATE = (
    "Buen día, {cliente}.\n"
    "Le saludamos de RP Asesores. Su pago de la póliza {poliza} ({producto}) venció el {fecha} "
    "y aún no lo tenemos registrado. El importe es de {importe}.\n"
    "Si ya lo realizó, por favor compártanos su comprobante. Quedamos atentos.\n"
    "RP Asesores"
)

STATUS = [
    ("overdue", "Vencido"),
    ("urgent", "Urgente (5 días o menos)"),
    ("month", "Pago del mes"),
    ("normal", "Normal"),
    ("paid", "Pagado"),
    ("none", "Sin fecha de pago"),
]
STATUS_RANK = {"overdue": 0, "urgent": 1, "month": 2, "normal": 3, "none": 5, "paid": 9}
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]


def norm(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def date_es(value):
    return "%d de %s de %d" % (value.day, MONTHS_ES[value.month - 1], value.year) if value else ""


class InsuranceCollectionNode(models.Model):
    _name = "insurance.collection.node"
    _description = "Cobranza: ramo, categoría o producto"
    _order = "sequence, id"
    _parent_store = True

    name = fields.Char(string="Nombre", required=True, translate=False)
    parent_id = fields.Many2one("insurance.collection.node", string="Dentro de", index=True, ondelete="cascade")
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many("insurance.collection.node", "parent_id", string="Botones")
    sequence = fields.Integer(default=10)
    icon = fields.Char(string="Ícono", default="fa-folder-open", help="Clase Font Awesome, p. ej. fa-heartbeat")
    color = fields.Char(string="Color", default="#5B47CC")
    note = fields.Char(string="Descripción")
    keywords = fields.Char(
        string="Palabras clave",
        help="Separadas por coma. Se buscan como palabras completas en el producto/plan de la póliza. "
             "Gana la palabra clave más larga, así una póliza solo cae en un producto.")
    branch = fields.Selection(
        [("medical", "Gastos médicos"), ("life", "Vida"), ("other", "Otro")],
        string="Ramo de la póliza", help="Limita el producto a pólizas de este tipo (GM… / VI…).")
    in_development = fields.Boolean(string="En desarrollo")
    active = fields.Boolean(default=True)
    level = fields.Integer(compute="_compute_level")
    policy_count = fields.Integer(compute="_compute_counts", string="Pólizas")
    urgent_count = fields.Integer(compute="_compute_counts", string="Urgentes o vencidas")
    month_count = fields.Integer(compute="_compute_counts", string="Del mes")

    @api.depends("parent_path")
    def _compute_level(self):
        for node in self:
            node.level = (node.parent_path or "").count("/")

    def _leaf_ids(self):
        self.ensure_one()
        return self.search([("id", "child_of", self.id)]).ids

    def _compute_counts(self):
        Policy = self.env["insurance.policy"]
        base = Policy._coll_domain()
        groups = Policy._read_group(base, ["coll_node_id", "coll_status"], ["__count"])
        per = {}
        for node, status, count in groups:
            per.setdefault(node.id, {}).setdefault(status, 0)
            per[node.id][status] += count
        for node in self:
            ids = self.search([("id", "child_of", node.id)]).ids if node.id else []
            stats = {}
            for nid in ids:
                for status, count in per.get(nid, {}).items():
                    stats[status] = stats.get(status, 0) + count
            node.policy_count = sum(stats.values())
            node.urgent_count = stats.get("overdue", 0) + stats.get("urgent", 0)
            node.month_count = stats.get("month", 0)

    def write(self, vals):
        res = super().write(vals)
        if {"keywords", "branch", "parent_id", "active"} & set(vals):
            self.env["insurance.policy"]._coll_reclassify()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        nodes = super().create(vals_list)
        if any(v.get("keywords") for v in vals_list):
            self.env["insurance.policy"]._coll_reclassify()
        return nodes

    # ---------------------------------------------------------------- navegación (plataforma)
    def action_open(self):
        self.ensure_one()
        if self.child_ids:
            return {
                "type": "ir.actions.act_window",
                "name": self.name,
                "res_model": "insurance.collection.node",
                "view_mode": "kanban,list,form",
                "views": [(self.env.ref("insurance_management.view_collection_node_kanban").id, "kanban"),
                          (False, "list"), (False, "form")],
                "domain": [("parent_id", "=", self.id)],
                "context": {"default_parent_id": self.id},
            }
        return self.env["insurance.policy"]._coll_action(self)


class InsuranceCollectionNotice(models.Model):
    _name = "insurance.collection.notice"
    _description = "Aviso de cobranza"
    _order = "prepared_at desc, id desc"

    policy_id = fields.Many2one("insurance.policy", string="Póliza", required=True, index=True, ondelete="cascade")
    partner_id = fields.Many2one(related="policy_id.partner_id", store=True, string="Cliente")
    due_date = fields.Date(string="Vencimiento avisado", required=True, index=True)
    kind = fields.Selection(
        [("advance", "Aviso anticipado"), ("overdue", "Pago vencido"), ("custom", "Mensaje personalizado")],
        string="Tipo de mensaje", required=True, default="advance")
    channel = fields.Selection([("whatsapp", "WhatsApp")], string="Medio", default="whatsapp", required=True)
    state = fields.Selection(
        [("prepared", "Preparado"), ("sent", "Enviado"), ("delivered", "Entregado")],
        string="Estado del mensaje", default="prepared", required=True, index=True)
    user_id = fields.Many2one("res.users", string="Responsable", default=lambda self: self.env.user)
    prepared_at = fields.Datetime(string="Preparado el", default=fields.Datetime.now)
    sent_at = fields.Datetime(string="Enviado el")
    message = fields.Text(string="Mensaje")
    note = fields.Text(string="Observaciones de cobranza")

    _uniq_notice = models.Constraint(
        "UNIQUE(policy_id, due_date, kind)",
        "Ya existe un aviso de ese tipo para este vencimiento.",
    )

    def action_mark_sent(self):
        self.filtered(lambda n: n.state == "prepared").write(
            {"state": "sent", "sent_at": fields.Datetime.now(), "user_id": self.env.user.id})
        return True

    def action_mark_delivered(self):
        for notice in self:
            notice.write({"state": "delivered", "sent_at": notice.sent_at or fields.Datetime.now()})
        return True


class InsurancePolicyCollection(models.Model):
    _inherit = "insurance.policy"

    collection_channel = fields.Selection(group_expand=True)
    coll_node_id = fields.Many2one(
        "insurance.collection.node", string="Producto (cobranza)", compute="_compute_coll_node",
        store=True, index=True, readonly=False,
        help="Botón de Cobranza donde aparece la póliza. Se asigna solo por palabras clave; puede corregirse a mano.")
    coll_product_text = fields.Char(compute="_compute_coll_product_text", string="Producto contratado")
    coll_next_date = fields.Date(
        string="Próximo pago", compute="_compute_coll_next_date", store=True, readonly=False, index=True,
        help="Se toma de la siguiente cuota pendiente; si no hay cuotas, se calcula con la forma de pago.")
    coll_paid_on = fields.Date(string="Último pago confirmado", copy=False)
    coll_paid_for = fields.Date(string="Vencimiento pagado", copy=False)
    coll_status = fields.Selection(STATUS, string="Estado de cobranza", compute="_compute_coll_status",
                                   store=True, index=True)
    coll_rank = fields.Integer(compute="_compute_coll_status", store=True, index=True)
    coll_amount = fields.Monetary(string="Importe del pago", compute="_compute_coll_amount",
                                  currency_field="currency_id")
    coll_phone = fields.Char(string="Teléfono del cliente", compute="_compute_coll_phone")
    coll_note = fields.Text(string="Observaciones de cobranza")
    coll_notice_ids = fields.One2many("insurance.collection.notice", "policy_id", string="Avisos de cobranza")
    coll_last_notice_id = fields.Many2one("insurance.collection.notice", compute="_compute_coll_notice",
                                          string="Último aviso")
    coll_notice_state = fields.Selection(
        [("none", "Sin aviso"), ("prepared", "Preparado"), ("sent", "Enviado"), ("delivered", "Entregado")],
        string="Aviso de este vencimiento", compute="_compute_coll_notice")
    coll_needs_notice = fields.Boolean(
        string="Falta avisar", compute="_compute_coll_notice", search="_search_coll_needs_notice",
        help="Le toca aviso (está dentro de los días de anticipación o vencido) y todavía no se le envía.")

    # ------------------------------------------------------------------ utilidades
    @api.model
    def _coll_domain(self):
        return [("state", "=", "confirmed")]

    @api.model
    def _coll_days(self):
        try:
            return int(self.env["ir.config_parameter"].sudo().get_param(ICP_DAYS, DEFAULT_DAYS))
        except (TypeError, ValueError):
            return DEFAULT_DAYS

    def _coll_today(self):
        return fields.Date.context_today(self)

    # ------------------------------------------------------------------ clasificación
    @api.depends("offer_id", "sub_offer_id", "sub_offer_text", "plan_basic")
    def _compute_coll_product_text(self):
        for policy in self:
            parts = [policy.plan_basic, policy.sub_offer_id.name, policy.sub_offer_text, policy.offer_id.name]
            policy.coll_product_text = next((p for p in parts if p), "") or ""

    def _coll_branch(self):
        self.ensure_one()
        name = (self.name or "").upper()
        kind = norm(self.policy_type_id.name)
        if name.startswith("GM") or "medic" in kind:
            return "medical"
        if name.startswith("VI") or "vida" in kind:
            return "life"
        return "other"

    @api.model
    def _coll_leaves(self):
        leaves = []
        for node in self.env["insurance.collection.node"].sudo().search([("keywords", "!=", False)]):
            for kw in (node.keywords or "").split(","):
                kw = norm(kw)
                if kw:
                    leaves.append((node, kw, re.compile(r"(?:^| )%s(?: |$)" % re.escape(kw))))
        return leaves

    @api.depends("offer_id", "sub_offer_id", "sub_offer_text", "plan_basic", "policy_type_id", "name")
    def _compute_coll_node(self):
        leaves = self._coll_leaves()
        for policy in self:
            branch = policy._coll_branch()
            text = norm(" ".join(filter(None, [
                policy.plan_basic, policy.sub_offer_id.name, policy.sub_offer_text,
                policy.offer_id.name, policy.policy_type_id.name])))
            best, best_len = False, 0
            for node, kw, rx in leaves:
                if node.branch and node.branch != branch:
                    continue
                if len(kw) > best_len and rx.search(text):
                    best, best_len = node, len(kw)
            policy.coll_node_id = best

    @api.model
    def _coll_reclassify(self):
        policies = self.sudo().search([])
        self.env.add_to_compute(self._fields["coll_node_id"], policies)
        policies._recompute_recordset(["coll_node_id"])

    # ------------------------------------------------------------------ fechas e importe
    def _coll_months(self):
        return PREMIUM_PERIODS.get(self.premium_type, 1)

    def _coll_adjust_day(self, value):
        if value and self.collection_date_mode == "fixed" and self.collection_day:
            return value + relativedelta(day=self.collection_day)
        return value

    @api.depends("installment_ids.due_date", "installment_ids.state", "coverage_start_date", "emission_date",
                 "premium_type", "collection_day", "collection_date_mode", "coll_paid_for")
    def _compute_coll_next_date(self):
        today = self._coll_today()
        for policy in self:
            pending = policy.installment_ids.filtered(
                lambda l: l.state not in ("paid", "cancelled") and l.due_date).sorted("due_date")
            if pending:
                policy.coll_next_date = pending[0].due_date
                continue
            months = policy._coll_months()
            if policy.coll_paid_for:
                policy.coll_next_date = policy._coll_adjust_day(policy.coll_paid_for + relativedelta(months=months))
                continue
            start = policy.coverage_start_date or policy.emission_date
            if not start:
                policy.coll_next_date = False
                continue
            # sin cuotas ni pagos registrados: el siguiente periodo a partir de hoy
            candidate = start
            guard = 0
            while policy._coll_adjust_day(candidate) < today and guard < 600:
                candidate += relativedelta(months=months)
                guard += 1
            policy.coll_next_date = policy._coll_adjust_day(candidate)

    @api.depends("coll_next_date", "coll_paid_on", "state")
    def _compute_coll_status(self):
        today = self._coll_today()
        days_notice = self._coll_days()
        for policy in self:
            status = policy._coll_status_for(today, days_notice)
            policy.coll_status = status
            policy.coll_rank = STATUS_RANK[status]

    def _coll_status_for(self, today, days_notice):
        self.ensure_one()
        nxt = self.coll_next_date
        if not nxt:
            return "none"
        delta = (nxt - today).days
        # verde mientras no llega el momento de volver a cobrar (solo con pago confirmado)
        if self.coll_paid_on and delta > max(days_notice, 5):
            return "paid"
        if delta < 0:
            return "overdue"
        if delta <= 5:
            return "urgent"
        if (nxt.year, nxt.month) == (today.year, today.month):
            return "month"
        return "normal"

    def _compute_coll_amount(self):
        for policy in self:
            line = policy.installment_ids.filtered(
                lambda l: l.state not in ("paid", "cancelled") and l.due_date == policy.coll_next_date)[:1]
            amount = line.amount if line else policy.installment_amount
            if not amount and policy.policy_amount:
                amount = policy.policy_amount / max(1, 12 // policy._coll_months())
            policy.coll_amount = amount or 0.0

    def _compute_coll_phone(self):
        for policy in self:
            partner = policy.partner_id
            phone = partner._insurance_phone() if partner and hasattr(partner, "_insurance_phone") else (partner.phone or "")
            policy.coll_phone = phone or ""

    def _coll_current_notices(self):
        self.ensure_one()
        return self.coll_notice_ids.filtered(lambda n: n.due_date == self.coll_next_date)

    @api.depends("coll_notice_ids.state", "coll_notice_ids.due_date", "coll_next_date", "coll_status")
    def _compute_coll_notice(self):
        days = self._coll_days()
        today = self._coll_today()
        order = {"prepared": 1, "sent": 2, "delivered": 3}
        for policy in self:
            policy.coll_last_notice_id = policy.coll_notice_ids[:1]
            current = policy._coll_current_notices()
            best = max(current.mapped("state"), key=lambda s: order[s], default="none")
            policy.coll_notice_state = best
            in_window = (policy.coll_next_date and policy.coll_status not in ("paid", "none")
                         and (policy.coll_next_date - today).days <= days)
            policy.coll_needs_notice = bool(in_window and best not in ("sent", "delivered"))

    def _search_coll_needs_notice(self, operator, value):
        today = self._coll_today()
        limit = today + timedelta(days=self._coll_days())
        candidates = self.sudo().search(self._coll_domain() + [
            ("coll_next_date", "!=", False), ("coll_next_date", "<=", limit),
            ("coll_status", "not in", ["paid", "none"])])
        ids = candidates.filtered("coll_needs_notice").ids
        positive = (operator == "=" and value) or (operator == "!=" and not value)
        return [("id", "in" if positive else "not in", ids)]

    @api.model
    def _cron_collection_refresh(self):
        """Diario: los colores cambian con el calendario aunque nadie toque la póliza."""
        policies = self.sudo().search([("coll_next_date", "!=", False)])
        # la fecha NO se recorre sola: si pasa sin pago confirmado, queda VENCIDO
        self.env.add_to_compute(self._fields["coll_status"], policies)
        self.env.add_to_compute(self._fields["coll_rank"], policies)
        policies._recompute_recordset(["coll_status", "coll_rank"])

    # ------------------------------------------------------------------ mensaje y avisos
    def _coll_message(self, kind=None):
        self.ensure_one()
        today = self._coll_today()
        kind = kind or ("overdue" if self.coll_next_date and self.coll_next_date < today else "advance")
        template = (self.env["ir.config_parameter"].sudo().get_param(ICP_TEMPLATE) or DEFAULT_TEMPLATE
                    if kind == "advance" else OVERDUE_TEMPLATE)
        amount = self.coll_amount
        currency = self.currency_id
        amount_txt = ("%s %s" % ("{:,.2f}".format(amount), currency.name or "")).strip() if amount else _("(por confirmar)")
        values = {
            "cliente": self.partner_id.name or "",
            "poliza": self.name or "",
            "producto": self.coll_node_id.name or self.coll_product_text or "",
            "fecha": date_es(self.coll_next_date),
            "importe": amount_txt,
        }
        try:
            return template.format(**values), kind
        except (KeyError, IndexError, ValueError):
            return DEFAULT_TEMPLATE.format(**values), kind

    def _coll_prepare_notice(self, kind=None):
        """Crea (o reutiliza) el aviso del vencimiento actual: nunca dos del mismo tipo y fecha."""
        self.ensure_one()
        if not self.coll_next_date:
            raise UserError(_("La póliza %s no tiene fecha de próximo pago.") % self.name)
        text, kind = self._coll_message(kind)
        Notice = self.env["insurance.collection.notice"].sudo()
        notice = Notice.search([("policy_id", "=", self.id), ("due_date", "=", self.coll_next_date),
                                ("kind", "=", kind)], limit=1)
        if notice:
            if notice.state == "prepared":
                notice.write({"message": text, "prepared_at": fields.Datetime.now(), "user_id": self.env.user.id})
        else:
            notice = Notice.create({"policy_id": self.id, "due_date": self.coll_next_date, "kind": kind,
                                    "message": text, "user_id": self.env.user.id})
        return notice

    def coll_whatsapp_url(self, kind=None):
        self.ensure_one()
        notice = self._coll_prepare_notice(kind)
        return self.env["insurance.team.portal"].wa_link(self.partner_id, notice.message), notice

    def action_coll_whatsapp(self):
        self.ensure_one()
        url, _notice = self.coll_whatsapp_url()
        if not url:
            raise UserError(_("El cliente %s no tiene teléfono registrado (pendiente de captura).")
                            % (self.partner_id.name or ""))
        return {"type": "ir.actions.act_url", "url": url, "target": "new"}

    def action_coll_mark_sent(self):
        for policy in self:
            current = policy._coll_current_notices()
            if not current:
                current = policy._coll_prepare_notice()
            current.action_mark_sent()
        return True

    def action_coll_confirm_payment(self):
        """Pago confirmado: se marca la cuota (si hay) y se calcula el siguiente cobro."""
        today = self._coll_today()
        for policy in self:
            due = policy.coll_next_date
            if not due:
                raise UserError(_("La póliza %s no tiene fecha de próximo pago.") % policy.name)
            line = policy.installment_ids.filtered(
                lambda l: l.state not in ("paid", "cancelled") and l.due_date == due)[:1]
            if line:
                line.action_mark_paid()
            policy.write({"coll_paid_on": today, "coll_paid_for": due})
            policy.message_post(body=_("Cobranza: pago confirmado del vencimiento %s.") % date_es(due))
        return True

    def action_coll_undo_payment(self):
        for policy in self:
            policy.write({"coll_paid_on": False, "coll_paid_for": False})
        return True

    @api.model
    def _coll_action(self, node):
        return {
            "type": "ir.actions.act_window",
            "name": node.name,
            "res_model": "insurance.policy",
            "view_mode": "kanban,list,form",
            "views": [(self.env.ref("insurance_management.view_policy_collection_kanban").id, "kanban"),
                      (self.env.ref("insurance_management.view_policy_collection_list").id, "list"),
                      (False, "form")],
            "domain": self._coll_domain() + [("coll_node_id", "in", node._leaf_ids())],
            "context": {"group_by": "collection_channel", "search_default_pending": 0},
        }

    # ---------------------------------------------------------------- portal / pantalla
    def coll_board(self):
        """Dos columnas (directo / domiciliado), ordenadas: vencidas → urgentes → mes → normales → pagadas."""
        cols = {"direct": [], "domiciled": []}
        for policy in self.sorted(lambda p: (p.coll_rank, p.coll_next_date or fields.Date.today())):
            cols.setdefault(policy.collection_channel or "direct", []).append(policy)
        return cols


class ResConfigSettingsCollection(models.TransientModel):
    _inherit = "res.config.settings"

    insurance_coll_notice_days = fields.Integer(
        string="Días de anticipación del aviso de cobranza", config_parameter=ICP_DAYS, default=DEFAULT_DAYS)
