"""Automatizaciones CreArt 3DP por etapa e ingreso esperado."""

import logging

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

STAGE_PROPUESTA = {"Propuesta enviada", "Aprobada propuesta", "Cotización enviada", "Conceptualización / Propuesta Enviada"}
STAGE_NEGOCIACION = {"Negociación / Seguimiento"}
STAGE_GANADA = {"Ganada/Pendiente pago", "Ganado y pendiente de pago", "Ganado / adelanto recibido"}
STAGE_IMPRIMIENDO = {"Imprimiendo", "En la cola de impresión", "En la cola de impresion"}
STAGE_ENTREGADO = {"Entregado/Cerrado", "Entregado/cerrado"}
# Desde estas etapas la oportunidad debe tener precio (o marcarse "Sin cargo").
STAGE_REQUIERE_PRECIO = (
    {"Propuesta enviada", "Control de Calidad", "PostProcesado", "Listo para Entregar"}
    | STAGE_NEGOCIACION | STAGE_GANADA | STAGE_IMPRIMIENDO | STAGE_ENTREGADO
)


class CrmLeadCreartAutomation(models.Model):
    _inherit = "crm.lead"

    auto_sync_expected_revenue = fields.Boolean(
        string="Usar precio cotizado como Ingreso esperado",
        default=True,
        help="Copia automáticamente el precio calculado en Finanzas al campo Ingreso esperado del CRM.",
    )
    quote_price_hint = fields.Char(string="Estado cotización", compute="_compute_quote_price_hint")
    creart_no_charge = fields.Boolean(
        string="Sin cargo",
        help="Patrocinio, intercambio u obsequio: permite avanzar de etapa sin precio "
             "y excluye el trabajo de los indicadores de ventas.",
    )

    @api.depends("selected_total_price", "expected_revenue", "auto_sync_expected_revenue")
    def _compute_quote_price_hint(self):
        for lead in self:
            if lead.selected_total_price and lead.auto_sync_expected_revenue:
                if lead.expected_revenue != lead.selected_total_price:
                    lead.quote_price_hint = _("Precio cotizado: %s (pendiente de sincronizar)") % lead.selected_total_price
                else:
                    lead.quote_price_hint = _("Ingreso esperado sincronizado con la cotización")
            elif lead.selected_total_price:
                lead.quote_price_hint = _("Precio cotizado: %s") % lead.selected_total_price
            else:
                lead.quote_price_hint = _("Complete Finanzas para calcular el precio")

    def action_apply_quote_to_expected_revenue(self):
        self._sync_expected_revenue_from_quote(force=True)
        return True

    def _sync_expected_revenue_from_quote(self, force=False):
        for lead in self:
            if not force and not lead.auto_sync_expected_revenue:
                continue
            quote = lead.selected_total_price or 0.0
            if quote and lead.expected_revenue != quote:
                lead.with_context(creart_skip_automation=True).write({"expected_revenue": quote})

    @api.model
    def _creart_stage_key(self, stage):
        """Nombre base de la etapa: no depende del idioma del usuario."""
        return stage.with_context(lang="en_US").name if stage else ""

    def _creart_check_price_for_stage(self, vals):
        """Exige precio al entrar a Propuesta y etapas posteriores."""
        ctx = self.env.context
        if ctx.get("creart_skip_automation") or ctx.get("creart_skip_price_check"):
            return
        if vals.get("creart_no_charge"):
            return
        stage = self.env["crm.stage"].browse(vals["stage_id"])
        if self._creart_stage_key(stage) not in STAGE_REQUIERE_PRECIO:
            return
        missing = self.filtered(
            lambda l: not l.creart_no_charge
            and not vals.get("expected_revenue", l.expected_revenue)
            and not l.selected_total_price
        )
        if missing:
            raise UserError(_(
                "Captura el precio de «%(lead)s» antes de pasarla a %(stage)s, "
                "o márcala como «Sin cargo» si es patrocinio u obsequio.",
                lead=missing[0].name, stage=stage.name,
            ))

    def write(self, vals):
        if vals.get("stage_id"):
            self._creart_check_price_for_stage(vals)
        old_stages = {l.id: l.stage_id.id for l in self} if "stage_id" in vals else {}
        res = super().write(vals)
        if self.env.context.get("creart_skip_automation"):
            return res
        finance_fields = {
            "units", "filament_grams", "filament_id", "filament_waste_percent",
            "print_hours", "mo_concept_hours", "mo_design_hours", "accessory_ids", "cost_line_ids",
        }
        if finance_fields.intersection(vals.keys()):
            self._sync_expected_revenue_from_quote()
        if "stage_id" in vals:
            for lead in self:
                if old_stages.get(lead.id) != lead.stage_id.id:
                    lead._creart_run_stage_automation()
        return res

    def _creart_run_stage_automation(self):
        self.ensure_one()
        stage = self._creart_stage_key(self.stage_id)
        messages = []
        try:
            with self.env.cr.savepoint():
                self._creart_apply_stage_rules(stage, messages)
        except Exception:
            _logger.exception("Automatización CreArt falló en lead %s", self.id)
            self.message_post(
                body=_("La automatización de etapa no se completó. Revisa el registro del servidor."),
                message_type="comment",
            )
            return
        if messages:
            self.message_post(body=Markup("%s<br/>%s") % (_("Automatización CreArt:"), Markup("<br/>").join(messages)), message_type="comment")

    def _creart_apply_stage_rules(self, stage, messages):
        self.ensure_one()
        # Los pasos de producción nacen al ganar, no en cada solicitud.
        if (stage in STAGE_GANADA or stage in STAGE_IMPRIMIENDO) and not self.print_process_step_ids:
            self.action_create_default_process_steps()
            messages.append(_("Pasos de producción creados."))
        if stage in STAGE_PROPUESTA or stage in STAGE_GANADA:
            self._sync_expected_revenue_from_quote(force=True)
            messages.append(_("Precio aplicado al Ingreso esperado."))
        if stage in STAGE_PROPUESTA:
            self._creart_on_cotizacion_enviada()
            messages.append(_("Actividad «Confirmación día 1» programada para hoy."))
        if stage in STAGE_NEGOCIACION:
            today = fields.Date.context_today(self)
            self.with_context(creart_skip_automation=True).write({
                "creart_negotiation_since": today,
                "creart_stale_review_notified": False,
            })
        if stage in STAGE_GANADA and not self.project_id:
            self.action_create_planning_project()
            messages.append(_("Plan de producción generado."))
        if stage in STAGE_IMPRIMIENDO:
            self.action_update_materials_from_job()
            try:
                self.action_reserve_materials()
                messages.append(_("Materiales reservados."))
            except UserError as err:
                messages.append(str(err.args[0]))
        if stage in STAGE_ENTREGADO and self.material_line_ids.filtered(lambda l: l.state == "reserved"):
            try:
                self.action_consume_materials()
                messages.append(_("Materiales consumidos."))
            except UserError as err:
                messages.append(str(err.args[0]))
