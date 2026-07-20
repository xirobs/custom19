# -*- coding: utf-8 -*-



from datetime import timedelta



from odoo import fields, models, _

from odoo.exceptions import UserError

from odoo.tools.mail import is_html_empty





class CrmLeadLost(models.TransientModel):

    _inherit = "crm.lead.lost"



    reactivation_date = fields.Date(

        string="Seguimiento de reactivación",

        default=lambda self: fields.Date.context_today(self) + timedelta(days=75),

        help="Fecha sugerida para reactivar el lead (60-90 días).",

    )



    def action_lost_reason_apply(self):

        self.ensure_one()

        if self.lost_reason_id.requires_detail_note and is_html_empty(self.lost_feedback):

            raise UserError(

                _('Indique el detalle en la nota interna para el motivo "%s".')

                % self.lost_reason_id.display_name

            )

        res = super().action_lost_reason_apply()

        for lead in self.lead_ids:

            body = _(

                "Motivo de pérdida: %(reason)s. "

                "Se deja programado seguimiento de reactivación en %(date)s (60-90 días)."

            ) % {

                "reason": self.lost_reason_id.display_name,

                "date": fields.Date.to_string(self.reactivation_date),

            }

            lead._creart_post_standard_internal_note(_("Cierre perdido"), body)

        return res

