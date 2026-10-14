# -*- coding: utf-8 -*-
from collections import OrderedDict

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import format_date

from ..models.scout_common import ATTENDED_STATUSES, COUNTED_STATUSES, STATUS_CODES

MONTHS_ES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto',
             'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']


def _quarter_bounds(day):
    start_month = 3 * ((day.month - 1) // 3) + 1
    start = day.replace(month=start_month, day=1)
    return start, start + relativedelta(months=3, days=-1)


class ScoutAttendanceReportWizard(models.TransientModel):
    _name = 'scout.attendance.report.wizard'
    _description = 'Reporte de asistencia por periodo'

    section_ids = fields.Many2many('scout.section', string='Secciones', required=True,
                                   default=lambda self: self.env['scout.section'].search([]))
    date_from = fields.Date(string='Desde', required=True,
                            default=lambda self: _quarter_bounds(fields.Date.context_today(self))[0])
    date_to = fields.Date(string='Hasta', required=True,
                          default=lambda self: _quarter_bounds(fields.Date.context_today(self))[1])
    include_inactive = fields.Boolean(string='Incluir bajas', default=False)

    def action_print(self):
        self.ensure_one()
        if self.date_to < self.date_from:
            raise UserError(_('La fecha final debe ser posterior a la inicial.'))
        return self.env.ref('scout_management.action_report_attendance').report_action(
            self, data={'wizard_id': self.id})


class ReportScoutAttendance(models.AbstractModel):
    _name = 'report.scout_management.report_attendance_matrix'
    _description = 'Reporte: matriz de asistencia'

    @api.model
    def _build_section(self, section, date_from, date_to, include_inactive):
        all_lines = self.env['scout.attendance.line'].sudo().search([
            ('section_id', '=', section.id), ('date', '>=', date_from), ('date', '<=', date_to),
            ('event_id.scout_attendance_closed', '=', True)])
        sessions = all_lines.event_id.sorted(lambda e: (e.scout_date, e.start))
        months = OrderedDict()
        for session in sessions:
            key = (session.scout_date.year, session.scout_date.month)
            months.setdefault(key, {'label': MONTHS_ES[session.scout_date.month - 1], 'sessions': []})
            months[key]['sessions'].append(session)

        member_domain = [('is_scout', '=', True), ('scout_section_id', '=', section.id)]
        if not include_inactive:
            member_domain.append(('scout_state', 'in', ('trial', 'active')))
        members = self.env['res.partner'].sudo().search(member_domain)
        members |= all_lines.member_id.filtered(lambda m: include_inactive or m.scout_state != 'inactive')
        members = members.sorted(lambda m: (m.scout_type != 'youth', m.scout_patrol_id.sequence or 0,
                                            m.scout_patrol_id.id or 0, m.scout_patrol_role != 'leader', m.name or ''))

        lines_by_key = {(l.event_id.id, l.member_id.id): l for l in all_lines}
        rows = []
        for member in members:
            cells, attended, counted = [], 0, 0
            for session in sessions:
                line = lines_by_key.get((session.id, member.id))
                status = line.status if line else False
                cells.append({'code': STATUS_CODES.get(status, ''), 'status': status or ''})
                attended += status in ATTENDED_STATUSES
                counted += status in COUNTED_STATUSES
            rows.append({
                'member': member,
                'cells': cells,
                'attended': attended,
                'counted': counted,
                'pct': round(attended / counted * 100) if counted else None,
            })
        totals = []
        for session in sessions:
            totals.append(sum(1 for l in all_lines if l.event_id == session and l.status in ATTENDED_STATUSES))
        counted_all = sum(r['counted'] for r in rows)
        attended_all = sum(r['attended'] for r in rows)
        return {
            'section': section,
            'months': list(months.values()),
            'sessions': sessions,
            'rows': rows,
            'totals': totals,
            'pct_all': round(attended_all / counted_all * 100) if counted_all else None,
        }

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        wizard = self.env['scout.attendance.report.wizard'].browse(data.get('wizard_id') or docids)
        if not wizard.exists():
            raise UserError(_('Vuelve a generar el reporte desde el asistente.'))
        sections = [self._build_section(s, wizard.date_from, wizard.date_to, wizard.include_inactive)
                    for s in wizard.section_ids.sorted('sequence')]
        return {
            'doc_ids': wizard.ids,
            'doc_model': wizard._name,
            'docs': wizard,
            'company': self.env.company,
            'sections': sections,
            'date_from': format_date(self.env, wizard.date_from),
            'date_to': format_date(self.env, wizard.date_to),
            'legend': [('SI', 'Asistió'), ('NO', 'No asistió'), ('CA', 'Campamento'), ('B', 'Baja'),
                       ('N/A', 'No aplica'), ('/', 'No se tomó lista'), ('M', 'Museo / salida')],
        }
