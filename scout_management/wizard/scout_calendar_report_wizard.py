# -*- coding: utf-8 -*-
from datetime import timedelta

import pytz

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from ..models.scout_common import MONTHS, quarter_bounds


class ScoutCalendarReportWizard(models.TransientModel):
    _name = 'scout.calendar.report.wizard'
    _description = 'Calendario trimestral del grupo'

    date_from = fields.Date(string='Desde', required=True,
                            default=lambda self: quarter_bounds(fields.Date.context_today(self))[0])
    date_to = fields.Date(string='Hasta', required=True,
                          default=lambda self: quarter_bounds(fields.Date.context_today(self))[1])
    include_sections = fields.Boolean(string='Incluir actividades especiales de las secciones', default=False,
                                      help='Campamentos, salidas y actividades especiales de cada sección.')
    show_proposal_space = fields.Boolean(string='Dejar espacio para propuestas a mano', default=True)
    notes = fields.Text(string='Notas al pie',
                        help='Ej. Actividades ordinarias: sábados. UKUQALA no está programado este trimestre.')

    def action_print(self):
        self.ensure_one()
        if self.date_to < self.date_from:
            raise UserError(_('La fecha final debe ser posterior a la inicial.'))
        return self.env.ref('scout_management.action_report_group_calendar').report_action(
            self, data={'wizard_id': self.id})


class ReportScoutGroupCalendar(models.AbstractModel):
    _name = 'report.scout_management.report_group_calendar'
    _description = 'Reporte: calendario trimestral del grupo'

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        wizard = self.env['scout.calendar.report.wizard'].browse(data.get('wizard_id') or docids)
        if not wizard.exists():
            raise UserError(_('Vuelve a generar el calendario desde el asistente.'))
        domain = [('is_scout_event', '=', True), ('scout_date', '<=', wizard.date_to),
                  ('scout_date', '>=', wizard.date_from - timedelta(days=7))]
        if wizard.include_sections:
            domain += ['|', ('scout_scope', '=', 'group'),
                       ('scout_activity_type', 'in', ('camp', 'outing', 'special', 'none'))]
        else:
            domain.append(('scout_scope', '=', 'group'))
        activities = self.env['calendar.event'].sudo().search(domain, order='start')

        tz = pytz.timezone(self.env.user.tz or 'America/Mexico_City')

        def last_day(event):
            if event.allday and event.stop_date:
                return event.stop_date
            if event.stop:
                return max(event.scout_date, pytz.utc.localize(event.stop).astimezone(tz).date())
            return event.scout_date

        def events_on(day):
            return activities.filtered(lambda a: a.scout_date <= day <= last_day(a))

        months = []
        cursor = wizard.date_from.replace(day=1)
        while cursor <= wizard.date_to:
            next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
            # Semanas como viernes-sábado-domingo
            day = cursor
            while day.weekday() != 4:  # viernes
                day -= timedelta(days=1)
            weeks = []
            while day < next_month:
                cells = []
                for offset in range(3):
                    d = day + timedelta(days=offset)
                    in_month = cursor <= d < next_month
                    cells.append({'day': d.day if in_month else '', 'date': d,
                                  'events': events_on(d) if in_month else activities.browse()})
                if any(c['day'] for c in cells):
                    weeks.append(cells)
                day += timedelta(days=7)
            months.append({'label': f'{MONTHS[cursor.month - 1].upper()} {cursor.year}', 'weeks': weeks})
            cursor = next_month
        label = f'{MONTHS[wizard.date_from.month - 1].upper()} - {MONTHS[wizard.date_to.month - 1].upper()} {wizard.date_to.year}'
        return {
            'doc_ids': wizard.ids,
            'doc_model': wizard._name,
            'docs': wizard,
            'company': self.env.company,
            'months': months,
            'period_label': label,
            'activities': activities,
        }
