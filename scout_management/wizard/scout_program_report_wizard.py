# -*- coding: utf-8 -*-
import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.scout_common import quarter_bounds, quarter_label


class ScoutProgramReportWizard(models.TransientModel):
    _name = 'scout.program.report.wizard'
    _description = 'Programa trimestral de sección (desde el calendario)'

    section_id = fields.Many2one('scout.section', string='Sección', required=True,
                                 default=lambda self: self.env['scout.section'].search(
                                     [('leader_ids', 'in', self.env.uid)], limit=1))
    date_from = fields.Date(string='Desde', required=True,
                            default=lambda self: quarter_bounds(fields.Date.context_today(self))[0])
    date_to = fields.Date(string='Hasta', required=True,
                          default=lambda self: quarter_bounds(fields.Date.context_today(self))[1])
    theme = fields.Char(string='Mística del trimestre', help='Ej. El Libro de las Tierras Vírgenes.')
    include_group_events = fields.Boolean(string='Incluir eventos del grupo', default=True)

    def action_print(self):
        self.ensure_one()
        if self.date_to < self.date_from:
            raise UserError(_('La fecha final debe ser posterior a la inicial.'))
        return self.env.ref('scout_management.action_report_program').report_action(
            self, data={'wizard_id': self.id})


class ReportScoutProgram(models.AbstractModel):
    _name = 'report.scout_management.report_program_document'
    _description = 'Reporte: programa trimestral'

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        wizard = self.env['scout.program.report.wizard'].browse(data.get('wizard_id') or docids)
        if not wizard.exists():
            raise UserError(_('Vuelve a generar el programa desde el asistente.'))
        Event = self.env['calendar.event'].sudo()
        base = [('is_scout_event', '=', True), ('scout_date', '>=', wizard.date_from),
                ('scout_date', '<=', wizard.date_to)]
        events = Event.search(base + [('scout_section_ids', 'in', wizard.section_id.ids)], order='start')
        group_events = Event.search(base + [('scout_scope', '=', 'group')], order='start') \
            if wizard.include_group_events else Event
        tz = pytz.timezone(self.env.user.tz or 'America/Mexico_City')

        def hours(event):
            if event.allday:
                return ''
            start = pytz.utc.localize(event.start).astimezone(tz)
            stop = pytz.utc.localize(event.stop).astimezone(tz)
            return f'{start:%H:%M}-{stop:%H:%M}'

        return {
            'doc_ids': wizard.ids,
            'doc_model': wizard._name,
            'docs': wizard,
            'company': self.env.company,
            'section': wizard.section_id,
            'period_label': quarter_label(wizard.date_from, wizard.date_to),
            'events': events,
            'group_events': group_events,
            'hours': hours,
            'activity_labels': dict(Event._fields['scout_activity_type'].selection),
        }
