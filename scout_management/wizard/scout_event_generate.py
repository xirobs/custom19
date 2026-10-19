# -*- coding: utf-8 -*-
from datetime import datetime, time, timedelta

import pytz

from odoo import _, fields, models
from odoo.exceptions import UserError

from ..models.scout_common import ACTIVITY_TYPES

WEEKDAYS = [('4', 'Viernes'), ('5', 'Sábado'), ('6', 'Domingo')]


class ScoutEventGenerate(models.TransientModel):
    _name = 'scout.event.generate'
    _description = 'Generar sesiones del trimestre en el calendario'

    section_ids = fields.Many2many('scout.section', string='Secciones', required=True,
                                   default=lambda self: self.env['scout.section'].search(
                                       [('is_leadership', '=', False), ('leader_ids', 'in', self.env.uid)])
                                   or self.env['scout.section'].search([('is_leadership', '=', False)]))
    date_from = fields.Date(string='Desde', required=True, default=fields.Date.context_today)
    date_to = fields.Date(string='Hasta', required=True)
    weekday = fields.Selection(WEEKDAYS, string='Día', required=True, default='5')
    hour_start = fields.Float(string='Hora de inicio', default=9.0)
    hour_end = fields.Float(string='Hora de término', default=13.0)
    name = fields.Char(string='Nombre', default=lambda self: _('Sesión'), required=True)
    activity_type = fields.Selection(ACTIVITY_TYPES, string='Tipo', default='regular', required=True)
    theme = fields.Char(string='Mística / temática')
    location = fields.Char(string='Lugar')
    skip_existing = fields.Boolean(string='Omitir días que ya tienen actividad de la sección', default=True)

    def _to_utc(self, day, hour):
        tz = pytz.timezone(self.env.user.tz or 'America/Mexico_City')
        local = tz.localize(datetime.combine(day, time(int(hour), int(round((hour % 1) * 60)))))
        return local.astimezone(pytz.utc).replace(tzinfo=None)

    def action_generate(self):
        self.ensure_one()
        if self.date_to < self.date_from or self.hour_end <= self.hour_start:
            raise UserError(_('Revisa las fechas y el horario.'))
        Event = self.env['calendar.event'].with_context(no_mail_to_attendees=True, mail_create_nolog=True)
        day = self.date_from
        while day.weekday() != int(self.weekday):
            day += timedelta(days=1)
        created = Event
        while day <= self.date_to:
            for section in self.section_ids:
                if self.skip_existing and Event.search_count([
                        ('is_scout_event', '=', True), ('scout_date', '=', day),
                        ('scout_section_ids', 'in', section.ids)]):
                    continue
                created |= Event.create({
                    'name': f'{self.name} {section.display_name}',
                    'is_scout_event': True,
                    'scout_scope': 'section',
                    'scout_section_ids': [fields.Command.set(section.ids)],
                    'scout_activity_type': self.activity_type,
                    'scout_theme': self.theme,
                    'location': self.location,
                    'start': self._to_utc(day, self.hour_start),
                    'stop': self._to_utc(day, self.hour_end),
                    'partner_ids': [fields.Command.set([])],
                })
            day += timedelta(days=7)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Actividades generadas'),
            'res_model': 'calendar.event',
            'view_mode': 'list,calendar,form',
            'views': [(self.env.ref('scout_management.view_scout_event_list').id, 'list'),
                      (False, 'calendar'), (False, 'form')],
            'domain': [('id', 'in', created.ids)],
        }
