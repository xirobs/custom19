# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

STATE_CODES = {
    'on_time': 'A',
    'late': 'R',
    'substitute': 'S',
    'excused': 'J',
    'absent': 'F',
    'pending': '-',
}


class BniAttendanceReportWizard(models.TransientModel):
    _name = 'bni.attendance.report.wizard'
    _description = 'Reporte de asistencia BNI por periodo'

    def _default_from(self):
        return fields.Date.context_today(self).replace(day=1)

    def _default_to(self):
        return fields.Date.context_today(self).replace(day=1) + relativedelta(months=1, days=-1)

    date_from = fields.Date('Desde', required=True, default=_default_from)
    date_to = fields.Date('Hasta', required=True, default=_default_to)
    modality = fields.Selection([
        ('all', 'Todas'),
        ('in_person', 'Presencial'),
        ('virtual', 'Virtual'),
    ], string='Modalidad', default='all', required=True)
    member_ids = fields.Many2many('bni.member', string='Miembros', help='Vacío = todos los miembros con registros en el periodo.')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for wiz in self:
            if wiz.date_from > wiz.date_to:
                raise ValidationError(_('La fecha inicial debe ser anterior a la final.'))

    def _event_domain(self):
        domain = [
            ('state', '=', 'closed'),
            ('event_date', '>=', self.date_from),
            ('event_date', '<=', self.date_to),
            ('company_id', '=', self.company_id.id),
        ]
        if self.modality != 'all':
            domain.append(('modality', '=', self.modality))
        return domain

    def get_report_data(self):
        """Estructura usada por el PDF mensual."""
        self.ensure_one()
        events = self.env['bni.event'].search(self._event_domain(), order='checkin_limit asc')
        lines = events.attendance_ids
        member_lines = lines.filtered(lambda l: l.attendee_type == 'member')
        if self.member_ids:
            member_lines = member_lines.filtered(lambda l: l.member_id in self.member_ids)
        members = member_lines.member_id.sorted(lambda m: (m.name or '').lower())
        Member = self.env['bni.member']

        rows = []
        for member in members:
            ml = member_lines.filtered(lambda l: l.member_id == member)
            by_event = {l.event_id.id: l for l in ml}
            cells = []
            for event in events:
                line = by_event.get(event.id)
                cells.append({
                    'code': STATE_CODES.get(line.state, '') if line else '',
                    'time': line.check_in_local if line else '',
                    'state': line.state if line else '',
                })
            stats = Member.stats_from_lines(ml)
            rows.append({'member': member, 'cells': cells, 'stats': stats})

        rows.sort(key=lambda r: (-r['stats']['rate'], r['member'].name.lower()))
        global_stats = Member.stats_from_lines(member_lines)
        movements = self.env['bni.member.movement'].search([
            ('date', '>=', self.date_from), ('date', '<=', self.date_to),
            ('company_id', '=', self.company_id.id),
        ], order='date asc')
        return {
            'events': events,
            'rows': rows,
            'global': global_stats,
            'guest_count': len(lines.filtered(lambda l: l.attendee_type == 'guest')),
            'movements': movements,
            'join_count': len(movements.filtered(lambda m: m.movement_type in ('join', 'reentry'))),
            'leave_count': len(movements.filtered(lambda m: m.movement_type == 'leave')),
        }

    def action_print(self):
        self.ensure_one()
        if not self.env['bni.event'].search_count(self._event_domain()):
            raise UserError(_('No hay eventos cerrados en el periodo seleccionado.'))
        return self.env.ref('bni_attendance.action_report_bni_monthly').report_action(self)

    def action_view_analysis(self):
        self.ensure_one()
        domain = [
            ('event_id.state', '=', 'closed'),
            ('event_date', '>=', self.date_from),
            ('event_date', '<=', self.date_to),
        ]
        if self.modality != 'all':
            domain.append(('modality', '=', self.modality))
        if self.member_ids:
            domain.append(('member_id', 'in', self.member_ids.ids))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Análisis de asistencia'),
            'res_model': 'bni.attendance',
            'view_mode': 'pivot,graph,list',
            'domain': domain,
            'context': {'search_default_filter_members': 1},
        }
