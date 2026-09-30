# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class InsuranceOffer(models.Model):
    """Catálogo jerárquico Ramo → Oferta → Sub-oferta.

    Ejemplos:
      Gastos médicos mayores → Tradicional → Prácticos / Íntegros / Plenos
      Gastos médicos mayores → Flexible → FlexA / FlexB
      Seguro de vida → ORVI (sub-oferta en texto libre en la póliza)
    """

    _name = "insurance.offer"
    _description = "Oferta / sub-oferta por ramo"
    _order = "ramo_id, parent_id desc, sequence, name"

    name = fields.Char(string="Nombre", required=True, translate=True)
    sequence = fields.Integer(default=10)
    ramo_id = fields.Many2one(
        "insurance.policy.type",
        string="Ramo",
        required=True,
        ondelete="restrict",
        index=True,
        compute="_compute_ramo_id",
        store=True,
        readonly=False,
        precompute=True,
    )
    parent_id = fields.Many2one(
        "insurance.offer",
        string="Oferta principal",
        ondelete="cascade",
        index=True,
        domain="[('ramo_id', '=', ramo_id), ('parent_id', '=', False)]",
        help="Vacío = es una oferta del ramo. Con valor = es una sub-oferta de esa oferta.",
    )
    child_ids = fields.One2many("insurance.offer", "parent_id", string="Sub-ofertas")
    level = fields.Selection(
        [("offer", "Oferta"), ("sub_offer", "Sub-oferta")],
        string="Nivel",
        compute="_compute_level",
        store=True,
    )
    sub_offer_free_text = fields.Boolean(
        string="Sub-oferta en texto libre",
        help="Si está marcado, en la póliza la sub-oferta se captura como texto libre "
             "en lugar de elegirse de una lista.",
    )
    description = fields.Text(string="Descripción")
    active = fields.Boolean(default=True)

    @api.depends("parent_id")
    def _compute_ramo_id(self):
        for offer in self:
            if offer.parent_id:
                offer.ramo_id = offer.parent_id.ramo_id
            else:
                offer.ramo_id = offer.ramo_id

    @api.depends("parent_id")
    def _compute_level(self):
        for offer in self:
            offer.level = "sub_offer" if offer.parent_id else "offer"

    @api.depends("name", "parent_id.name")
    def _compute_display_name(self):
        for offer in self:
            if offer.parent_id:
                offer.display_name = "%s / %s" % (offer.parent_id.name, offer.name)
            else:
                offer.display_name = offer.name

    @api.constrains("parent_id", "ramo_id")
    def _check_hierarchy(self):
        for offer in self:
            if not offer.parent_id:
                continue
            if offer.parent_id.parent_id:
                raise ValidationError(_("Solo se permiten dos niveles: oferta y sub-oferta."))
            if offer.parent_id.ramo_id != offer.ramo_id:
                raise ValidationError(_("La sub-oferta debe pertenecer al mismo ramo que su oferta."))
