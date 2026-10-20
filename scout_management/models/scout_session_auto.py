# -*- coding: utf-8 -*-
"""Junta semanal automática: cada sección tiene su día y horario y el sistema deja creadas sus sesiones
en el calendario (con sus jefes como asistentes) para que puedan pasar lista."""
import logging
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

WEEKDAYS = [('0', 'Lunes'), ('1', 'Martes'), ('2', 'Miércoles'), ('3', 'Jueves'), ('4', 'Viernes'),
            ('5', 'Sábado'), ('6', 'Domingo')]
DEFAULT_WEEKS_AHEAD = 8


class ScoutSectionSessions(models.Model):
    _inherit = 'scout.section'

    session_auto = fields.Boolean(
        string='Junta semanal automática',
        help='Deja creada en el calendario la junta de cada semana (las próximas semanas) para pasar lista.')
    session_weekday = fields.Selection(WEEKDAYS, string='Día de la junta', default='5')
    session_hour_start = fields.Float(string='Inicio de la junta', default=15.75)
    session_hour_end = fields.Float(string='Fin de la junta', default=18.0)
    session_name = fields.Char(string='Nombre de la junta', default='Junta',
                               help='Aparece en el calendario como "<sección> · Junta".')
    session_location = fields.Char(string='Lugar de la junta')

    def _scout_session_to_utc(self, day, hour):
        tz = pytz.timezone(self.env.user.tz or self.env.context.get('tz') or 'America/Mexico_City')
        minutes = int(round(hour * 60))
        local = tz.localize(datetime.combine(day, time(minutes // 60, minutes % 60)))
        return local.astimezone(pytz.utc).replace(tzinfo=None)

    def _scout_generate_sessions(self, date_from=None, date_to=None):
        """Crea las juntas que falten entre las fechas (por defecto: las próximas semanas configuradas)."""
        Event = self.env['calendar.event'].sudo().with_context(
            no_mail_to_attendees=True, mail_create_nolog=True, mail_notrack=True)
        params = self.env['ir.config_parameter'].sudo()
        weeks = int(params.get_param('scout_management.session_weeks_ahead', DEFAULT_WEEKS_AHEAD) or DEFAULT_WEEKS_AHEAD)
        today = fields.Date.context_today(self)
        date_from = date_from or today
        date_to = date_to or today + timedelta(weeks=weeks)
        created = Event
        for section in self.filtered(lambda s: s.session_auto and not s.is_leadership and s.session_weekday):
            if section.session_hour_end <= section.session_hour_start:
                continue
            day = date_from
            while day.weekday() != int(section.session_weekday):
                day += timedelta(days=1)
            leaders = (section.leader_ids.partner_id | self.env['res.partner'].sudo().search([
                ('is_scout', '=', True), ('scout_type', '=', 'leader'), ('scout_state', '=', 'active'),
                ('scout_leading_section_ids', 'in', section.ids)]))
            while day <= date_to:
                exists = Event.with_context(active_test=False).search_count([
                    ('is_scout_event', '=', True), ('scout_date', '=', day),
                    ('scout_section_ids', 'in', section.ids)])
                if not exists:
                    created |= Event.create({
                        'name': section.session_name or _('Junta'),
                        'is_scout_event': True,
                        'scout_scope': 'section',
                        'scout_section_ids': [fields.Command.set(section.ids)],
                        'scout_activity_type': 'regular',
                        'location': section.session_location or False,
                        'start': section._scout_session_to_utc(day, section.session_hour_start),
                        'stop': section._scout_session_to_utc(day, section.session_hour_end),
                        'partner_ids': [fields.Command.set(leaders.ids)],
                        'user_id': section.leader_ids[:1].id or self.env.user.id,
                    })
                day += timedelta(days=7)
        if created:
            _logger.info('Scouts: %s juntas semanales creadas en el calendario', len(created))
        return created

    @api.model
    def _cron_generate_sessions(self):
        sections = self.search([('session_auto', '=', True), ('is_leadership', '=', False)])
        sections._scout_generate_sessions()
        # El día de la junta deja cargada la lista con los miembros y jefes de la sección.
        today = fields.Date.context_today(self)
        events = self.env['calendar.event'].sudo().search([
            ('is_scout_event', '=', True), ('scout_date', '>=', today), ('scout_date', '<=', today + timedelta(days=1)),
            ('scout_section_ids', 'in', sections.ids), ('scout_attendance_closed', '=', False),
            ('scout_attendance_line_ids', '=', False)])
        for event in events:
            try:
                event.action_scout_load_members()
            except Exception:  # noqa: BLE001 - no detener el cron por una actividad
                _logger.exception('No se pudo cargar la lista de %s', event.display_name)

    def action_scout_generate_sessions(self):
        created = self._scout_generate_sessions()
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Juntas semanales'), 'type': 'success',
                           'message': _('%s junta(s) nuevas en el calendario.', len(created)) if created
                           else _('Las juntas de las próximas semanas ya estaban creadas.'),
                           'next': {'type': 'ir.actions.act_window_close'}}}
