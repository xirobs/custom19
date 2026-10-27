# -*- coding: utf-8 -*-
"""Cargar el calendario del grupo (consejos, campamentos, eventos) desde PDF, Excel o imagen.

* PDF en cuadrícula (como los calendarios trimestrales del grupo): se lee por posición, sin IA.
* Excel: columnas Fecha, Fin (opcional), Actividad, Tipo (opcional), Notas (opcional).
* Imagen o PDF escaneado: con la IA configurada en la empresa.
Primero se muestra la lista de lo encontrado; se agrega al calendario solo lo que se marque.
"""
import datetime
import io
import json
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import scout_pdf_layout as layout
from .scout_common import ACTIVITY_TYPES
from .scout_plan_import import IMAGE_TYPES, MAX_FILE_MB

TYPE_WORDS = [
    ('none', ('sin actividad', 'semana mayor', 'vacaciones', 'suspend')),
    ('council', ('consejo', 'asamblea', 'junta de jefes', 'junta de dirigentes')),
    ('training', ('insignia de madera', 'indaba', 'formacion', 'curso', 'capacitacion', 'taller de dirigentes')),
    ('camp', ('campamento', 'acampada', 'ukuqala', 'brownsea', 'rally', 'jamboree', 'campori')),
    ('outing', ('excursion', 'salida', 'caminata', 'visita')),
]
TENTATIVE_WORDS = ('propuesta', 'opcion', 'por definir', 'pendiente', 'tentativ')

CAL_PROMPT = """Eres asistente de un grupo scout de México. El archivo es el calendario de actividades del grupo.
Devuelve SOLO un JSON: {"events": [{"date": "AAAA-MM-DD", "date_end": "AAAA-MM-DD", "title": "...", "notes": "..."}]}
- Una entrada por actividad (consejos, campamentos, eventos, aniversarios, días sin actividades, etc.).
- Si dura varios días seguidos usa date y date_end; si es un solo día, date_end igual a date.
- No incluyas las actividades ordinarias de cada sábado si solo dicen "actividad ordinaria".
- "notes" es el texto pequeño de apoyo (ej. "en línea", "propuesta", "sede pendiente")."""


def _norm(text):
    return layout._plain(text)


def classify(title, notes=''):
    low = _norm('%s %s' % (title, notes))
    for key, words in TYPE_WORDS:
        if any(w in low for w in words):
            return key
    return 'group'


def is_tentative(title, notes=''):
    low = _norm('%s %s' % (title, notes))
    return any(w in low for w in TENTATIVE_WORDS)


class CalendarEventImport(models.Model):
    _inherit = 'calendar.event'

    @api.model
    def _scout_calendar_parse(self, filename, content):
        """{'events': [...], 'notes': [...], 'source': '...'} sin crear nada."""
        if not content:
            raise UserError(_('El archivo está vacío.'))
        if len(content) > MAX_FILE_MB * 1024 * 1024:
            raise UserError(_('El archivo pesa más de %s MB.', MAX_FILE_MB))
        ext = (filename or '').rsplit('.', 1)[-1].lower() if '.' in (filename or '') else ''
        if content[:4] == b'%PDF':
            ext = 'pdf'
        Event = self.env['calendar.event']
        notes, source = [], ''
        if ext == 'pdf':
            raw, notes = layout.calendar_events(layout.pdf_items(content))
            events = layout.merge_consecutive(raw)
            source = _('PDF')
            if not events:
                events = self._scout_calendar_ai(content, 'application/pdf')
                source = _('PDF (leído con IA)')
        elif ext in ('xlsx', 'xlsm', 'csv'):
            events = self._scout_calendar_from_sheet(content, ext)
            source = _('Excel')
        elif ext in IMAGE_TYPES:
            events = self._scout_calendar_ai(content, IMAGE_TYPES[ext])
            source = _('imagen (leída con IA)')
        else:
            raise UserError(_('Sube el calendario en PDF, Excel (.xlsx) o imagen.'))
        if not events:
            raise UserError(_('No encontré actividades con fecha en el archivo.'))
        result = []
        for ev in events:
            title = (ev.get('title') or '').strip()
            if not title or not ev.get('date'):
                continue
            date_end = ev.get('date_end') or ev['date']
            if date_end < ev['date']:
                date_end = ev['date']
            existing = Event.sudo().search_count([
                ('is_scout_event', '=', True), ('scout_date', '=', ev['date']),
                '|', ('name', '=ilike', title), ('name', '=ilike', title + ' (propuesta)')], limit=1)
            note = (ev.get('notes') or '').strip()
            result.append({
                'date': ev['date'].isoformat(), 'date_end': date_end.isoformat(), 'title': title[:120],
                'notes': note[:400], 'type': ev.get('type') or classify(title, note),
                'tentative': is_tentative(title, note), 'existing': bool(existing),
            })
        return {'events': result, 'notes': notes, 'source': source}

    @api.model
    def _scout_calendar_from_sheet(self, content, ext):
        rows = self.env['calendar.event']._scout_plan_sheet_rows(content, ext)[0][1] if content else []
        header, idx = None, 0
        for idx, row in enumerate(rows[:30]):
            labels = [_norm(str(c or '')) for c in row]
            if any(lab.startswith('fecha') or lab in ('inicio', 'dia') for lab in labels) \
                    and any(lab.startswith(('actividad', 'evento', 'nombre', 'titulo')) for lab in labels):
                header = labels
                break
        if not header:
            raise UserError(_('El Excel necesita columnas "Fecha" y "Actividad" (opcionales: "Fin", "Tipo", "Notas").'))

        def col(*names):
            return next((k for k, lab in enumerate(header) if lab.startswith(names)), None)
        c_date, c_end = col('fecha', 'inicio', 'dia'), col('fin', 'hasta', 'termina')
        c_title, c_type, c_notes = col('actividad', 'evento', 'nombre', 'titulo'), col('tipo'), \
            col('nota', 'observa', 'descrip', 'lugar')
        types = {_norm(label): key for key, label in ACTIVITY_TYPES}
        events = []
        for row in rows[idx + 1:]:
            def val(c):
                return row[c] if c is not None and c < len(row) else None
            day, end = self._scout_cell_date(val(c_date)), self._scout_cell_date(val(c_end))
            title = str(val(c_title) or '').strip()
            if not day or not title:
                continue
            typ = types.get(_norm(str(val(c_type) or '')))
            events.append({'date': day, 'date_end': end or day, 'title': title,
                           'notes': str(val(c_notes) or '').strip(), 'type': typ})
        return events

    @api.model
    def _scout_cell_date(self, value):
        if not value:
            return None
        if isinstance(value, datetime.datetime):
            return value.date()
        if isinstance(value, datetime.date):
            return value
        text = str(value).strip()
        for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d/%m/%y', '%d.%m.%Y'):
            try:
                return datetime.datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    @api.model
    def _scout_calendar_ai(self, content, mimetype):
        Event = self.env['calendar.event']
        provider, key, model = Event._scout_ai_config()
        if not key:
            raise UserError(_('No pude leer las fechas de este archivo sin inteligencia artificial. Súbelo como PDF '
                              'de cuadrícula o Excel, o configura la IA en Datos del grupo.'))
        raw = getattr(Event, f'_scout_ai_{provider}')(key, model, CAL_PROMPT, content, mimetype)
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', (raw or '').strip())
        match = re.search(r'\{.*\}', text, re.S)
        try:
            data = json.loads(match.group(0) if match else text)
        except (ValueError, AttributeError) as e:
            raise UserError(_('La inteligencia artificial no devolvió un calendario legible. Intenta de nuevo.')) from e
        events = []
        for item in data.get('events') or []:
            day = self._scout_cell_date(item.get('date'))
            if day:
                events.append({'date': day, 'date_end': self._scout_cell_date(item.get('date_end')) or day,
                               'title': str(item.get('title') or ''), 'notes': str(item.get('notes') or '')})
        return events

    @api.model
    def _scout_calendar_create(self, events):
        """Crea las actividades de todo el grupo (día completo). Regresa los eventos creados."""
        Event = self.env['calendar.event'].sudo().with_context(no_mail_to_attendees=True, mail_create_nolog=True)
        created = Event
        valid_types = dict(ACTIVITY_TYPES)
        for ev in events:
            day = fields.Date.to_date(ev['date'])
            end = fields.Date.to_date(ev.get('date_end') or ev['date'])
            title = (ev.get('title') or '').strip()
            if not title or not day:
                continue
            if Event.search_count([('is_scout_event', '=', True), ('scout_date', '=', day),
                                   '|', ('name', '=ilike', title), ('name', '=ilike', title + ' (propuesta)')],
                                  limit=1):
                continue
            notes = (ev.get('notes') or '').strip()
            created |= Event.create({
                'name': title + (' (propuesta)' if ev.get('tentative') and 'propuesta' not in title.lower() else ''),
                'is_scout_event': True,
                'scout_scope': 'group',
                'scout_activity_type': ev.get('type') if ev.get('type') in valid_types else 'group',
                'allday': True,
                'start_date': day, 'stop_date': max(end, day),
                'start': datetime.datetime.combine(day, datetime.time(8, 0)),
                'stop': datetime.datetime.combine(max(end, day), datetime.time(18, 0)),
                'description': notes or False,
                'user_id': self.env.user.id,
            })
        return created


class ScoutCalendarImportWizard(models.TransientModel):
    _name = 'scout.calendar.import.wizard'
    _description = 'Cargar calendario del grupo'

    file = fields.Binary(string='Archivo (PDF, Excel o imagen)', required=True)
    filename = fields.Char()
    line_ids = fields.One2many('scout.calendar.import.wizard.line', 'wizard_id', string='Actividades encontradas')
    notes = fields.Text(string='Notas del calendario', readonly=True)
    state = fields.Selection([('upload', 'Subir'), ('review', 'Revisar')], default='upload')

    def action_read(self):
        self.ensure_one()
        import base64
        data = self.env['calendar.event']._scout_calendar_parse(self.filename, base64.b64decode(self.file or b''))
        self.line_ids = [fields.Command.clear()] + [fields.Command.create({
            'selected': not ev['existing'], 'date': ev['date'], 'date_end': ev['date_end'], 'name': ev['title'],
            'notes': ev['notes'], 'activity_type': ev['type'], 'tentative': ev['tentative'],
            'existing': ev['existing']}) for ev in data['events']]
        self.write({'state': 'review', 'notes': '\n'.join(data['notes']) or False})
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form',
                'target': 'new', 'name': _('Revisa el calendario')}

    def action_import(self):
        self.ensure_one()
        created = self.env['calendar.event']._scout_calendar_create([{
            'date': line.date, 'date_end': line.date_end, 'title': line.name, 'notes': line.notes,
            'type': line.activity_type, 'tentative': line.tentative} for line in self.line_ids if line.selected])
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Calendario del grupo'), 'type': 'success',
                           'message': _('%s actividad(es) agregadas al calendario.', len(created)),
                           'next': {'type': 'ir.actions.act_window_close'}}}


class ScoutCalendarImportWizardLine(models.TransientModel):
    _name = 'scout.calendar.import.wizard.line'
    _description = 'Actividad encontrada en el calendario'
    _order = 'date, id'

    wizard_id = fields.Many2one('scout.calendar.import.wizard', ondelete='cascade')
    selected = fields.Boolean(string='Agregar', default=True)
    date = fields.Date(string='Desde', required=True)
    date_end = fields.Date(string='Hasta')
    name = fields.Char(string='Actividad', required=True)
    notes = fields.Char(string='Notas')
    activity_type = fields.Selection(ACTIVITY_TYPES, string='Tipo')
    tentative = fields.Boolean(string='Propuesta')
    existing = fields.Boolean(string='Ya existe', readonly=True)
