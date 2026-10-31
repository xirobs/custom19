# -*- coding: utf-8 -*-
"""Consejo en vivo: orden del día por puntos con reloj compartido, transcripción de la videollamada y acta con IA.

* El orden del día son renglones con su duración. Quien modera (preside, secretaría o la jefatura de grupo) da
  "Iniciar" y "Siguiente"; todos los que tienen la pantalla abierta (o están en la videollamada de Odoo) ven el
  mismo reloj: verde al empezar, amarillo a la mitad del tiempo y rojo en el último minuto.
* La transcripción llega en trozos de ~1 minuto: audio de la videollamada (todos los participantes) que se
  transcribe con la IA configurada (Gemini u OpenAI), o texto del dictado del navegador. Cada trozo queda ligado
  al punto del orden del día que se estaba tratando.
* "Generar acta" resume cada punto, saca los acuerdos con su responsable y llena el acta (queda en borrador para
  revisarla y enviarla a aprobación como siempre).
"""
import base64
import datetime
import json
import logging
import re
import unicodedata

import pytz
import requests

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import html_escape, html2plaintext

_logger = logging.getLogger(__name__)

DEFAULT_TZ = 'America/Mexico_City'
LIVE_STATES = [('idle', 'Sin iniciar'), ('running', 'Alguien tiene la palabra'), ('paused', 'En pausa'),
               ('waiting', 'Esperando quién toma la palabra'), ('done', 'Terminado')]

# Orden del día habitual del consejo de grupo (se puede cambiar en cada acta)
COUNCIL_TEMPLATE = [
    ('Inicio de sesión y bienvenida', 3), ('Oración inicial', 4), ('Pase de lista', 5),
    ('Lectura del acta anterior', 8), ('Informe conjunto de Tesorería y Secretaría', 20),
    ('Informe de Castores', 7), ('Informe de Gacelas', 7), ('Informe de Lobatos', 7),
    ('Informe de Tropa Femenina', 7), ('Informe de Tropa Masculina', 7),
    ('Informe conjunto de Clanes (Precursoras y Rovers)', 7), ('Puntos varios y acuerdos', 28),
    ('Oración final', 3), ('Revisión, conformidad y firma del acta', 7),
]

AGENDA_PROMPT = """La imagen o el documento es el ORDEN DEL DÍA de una reunión (consejo de grupo scout).
Devuelve SOLO este JSON:
{"puntos": [{"hora": "9:00", "actividad": "Inicio de sesión y bienvenida", "minutos": 3}], "inicio": "9:00"}
- Un elemento por punto, en orden. "minutos" = duración (si no viene, calcúlala con la hora de inicio y fin).
- Copia el nombre del punto tal cual, sin la hora ni la duración."""

TRANSCRIBE_PROMPT = """Transcribe en español, palabra por palabra, el audio de una reunión de dirigentes scouts
(consejo de grupo). Punto que se está tratando: "{point}".
{speakers}
Reglas: no resumas ni inventes; si no se entiende algo escribe [inaudible]; si hay silencio o solo ruido regresa
texto vacío. Si sabes quién habla, empieza su intervención con "Nombre: ". Responde SOLO este JSON:
{{"texto": "..."}}"""

MINUTES_PROMPT = """Eres la secretaría de un grupo scout en México. Con la transcripción de la reunión redacta el acta.
Reunión: {title} — {date}.
Asistentes: {attendees}.

Orden del día (número. punto):
{agenda}

Intervenciones cronometradas (quién tuvo la palabra y cuánto tiempo):
{turns}

Transcripción (cada bloque indica el punto que se trataba):
{transcript}

Responde SOLO este JSON:
{{"resumen": "resumen ejecutivo de 3 a 6 renglones",
 "puntos": [{{"n": 1, "resumen": "lo que se informó, discutió y decidió en ese punto, en tercera persona, claro y \
formal; vacío si no se trató"}}],
 "acuerdos": [{{"acuerdo": "qué se acordó", "responsable": "nombre de la persona o cargo, o vacío", \
"fecha": "AAAA-MM-DD o vacío"}}],
 "proxima_reunion": "AAAA-MM-DD HH:MM o vacío"}}
No inventes datos que no estén en la transcripción. Usa los nombres de los asistentes cuando se mencionen."""


def _plain(text):
    text = unicodedata.normalize('NFKD', text or '')
    return ''.join(c for c in text if not unicodedata.combining(c)).lower().strip()


def _hhmm(text):
    m = re.match(r'^\s*(\d{1,2})[:.](\d{2})', text or '')
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_agenda_text(text):
    """Renglones de texto ("9:00 – 9:03 Inicio de sesión 3 min", "Oración inicial - 4", "Pase de lista") →
    [{'name', 'minutes'}]."""
    points = []
    for raw in (text or '').splitlines():
        line = re.sub(r'\s+', ' ', raw.replace('\t', ' ')).strip(' -–—•*·|')
        if not line or re.match(r'^(hora|actividad|duraci[oó]n|orden del d[ií]a)\b', _plain(line)) \
                and len(line) < 40:
            continue
        if '|' in line:
            parts = [x.strip() for x in line.split('|')]
            name = parts[0]
            mins = next((int(re.match(r'\d+', x).group(0)) for x in parts[1:2] if re.match(r'^\d+', x)), None)
            who = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 and mins is None else '')
            if name and not re.fullmatch(r'[\d\s:.]+', name):
                points.append({'name': name[:200], 'minutes': mins if mins and 0 < mins <= 600 else 5,
                               'responsible': who[:120]})
                continue
        minutes = None
        times = re.findall(r'(\d{1,2})[:.](\d{2})', line)
        m = re.search(r'(\d{1,3})\s*(?:min(?:utos?|s)?\.?|\')\s*$', line, re.I)
        if m:
            minutes = int(m.group(1))
            line = line[:m.start()].strip(' -–—|')
        elif len(times) >= 2:
            (h1, m1), (h2, m2) = times[0], times[1]
            minutes = (int(h2) * 60 + int(m2)) - (int(h1) * 60 + int(m1))
        else:
            m = re.search(r'[-–—|(]\s*(\d{1,3})\s*\)?$', line)
            if m:
                minutes = int(m.group(1))
                line = line[:m.start()].strip(' -–—|(')
        # quita las horas del principio ("9:00 – 9:03", "9:00 a 9:03")
        line = re.sub(r'^\d{1,2}[:.]\d{2}\s*(?:(?:[-–—]|a)\s*\d{1,2}[:.]\d{2})?\s*(?:hrs?\.?|h)?\s*[-–—:|]?\s*', '',
                      line, flags=re.I).strip()
        line = re.sub(r'^\d{1,2}[.)]\s+', '', line).strip()  # "1. Bienvenida"
        if not line or re.fullmatch(r'[\d\s:.\-–—]+', line):
            continue
        if minutes is not None and not 0 < minutes <= 600:
            minutes = None
        points.append({'name': line[:200], 'minutes': minutes or 5})
    return points


class ScoutMeetingAgendaLine(models.Model):
    _name = 'scout.meeting.agenda.line'
    _description = 'Punto del orden del día'
    _order = 'meeting_id, sequence, id'

    meeting_id = fields.Many2one('scout.meeting', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Punto', required=True)
    duration = fields.Integer(string='Minutos', default=5, required=True)
    extra_minutes = fields.Integer(string='Minutos extra', help='Tiempo agregado durante la reunión.')
    responsible = fields.Char(string='Expone')
    spent_seconds = fields.Integer(string='Tiempo usado (s)', readonly=True)
    spent_display = fields.Char(string='Tiempo usado', compute='_compute_spent_display')
    summary = fields.Text(string='Resumen del punto')

    @api.depends('spent_seconds')
    def _compute_spent_display(self):
        for line in self:
            s = line.spent_seconds or 0
            line.spent_display = '%d:%02d' % (s // 60, s % 60) if s else ''


class ScoutMeetingTranscript(models.Model):
    _name = 'scout.meeting.transcript'
    _description = 'Transcripción de la reunión (trozo)'
    _order = 'meeting_id, time, id'

    meeting_id = fields.Many2one('scout.meeting', required=True, ondelete='cascade', index=True)
    agenda_line_id = fields.Many2one('scout.meeting.agenda.line', string='Punto', ondelete='set null')
    time = fields.Datetime(string='Hora', default=fields.Datetime.now)
    speakers = fields.Char(string='Hablaron')
    text = fields.Text(string='Texto')
    source = fields.Selection([('call', 'Videollamada'), ('mic', 'Micrófono'), ('browser', 'Dictado del navegador'),
                               ('file', 'Archivo de audio'), ('manual', 'Escrito / pegado')], default='call')
    user_id = fields.Many2one('res.users', string='Capturó', default=lambda self: self.env.user)


class ScoutMeetingTurn(models.Model):
    _name = 'scout.meeting.turn'
    _description = 'Turno de palabra en la reunión'
    _order = 'meeting_id, start, id'

    meeting_id = fields.Many2one('scout.meeting', required=True, ondelete='cascade', index=True)
    agenda_line_id = fields.Many2one('scout.meeting.agenda.line', string='Punto', ondelete='set null')
    partner_id = fields.Many2one('res.partner', string='Persona')
    speaker_name = fields.Char(string='Habló')
    start = fields.Datetime(string='Inicio')
    planned_seconds = fields.Integer(string='Tiempo dado (s)')
    seconds = fields.Integer(string='Tiempo usado (s)')
    used_display = fields.Char(string='Usó', compute='_compute_display')
    planned_display = fields.Char(string='Tenía', compute='_compute_display')
    over = fields.Boolean(string='Se pasó', compute='_compute_display')

    @api.depends('seconds', 'planned_seconds')
    def _compute_display(self):
        for t in self:
            t.used_display = '%d:%02d' % divmod(t.seconds or 0, 60)
            t.planned_display = '%d:%02d' % divmod(t.planned_seconds or 0, 60)
            t.over = (t.seconds or 0) > (t.planned_seconds or 0)


class ScoutMeetingAgreementAI(models.Model):
    _inherit = 'scout.meeting.agreement'

    ai_generated = fields.Boolean(string='Generado con IA', copy=False)


class ScoutMeetingLive(models.Model):
    _inherit = 'scout.meeting'

    agenda_line_ids = fields.One2many('scout.meeting.agenda.line', 'meeting_id', string='Puntos del orden del día',
                                      copy=True)
    agenda_minutes = fields.Integer(string='Duración planeada (min)', compute='_compute_agenda_minutes')
    transcript_ids = fields.One2many('scout.meeting.transcript', 'meeting_id', string='Transcripción')
    transcript_text = fields.Text(string='Transcripción completa', compute='_compute_transcript_text')
    ai_summary = fields.Html(string='Resumen de la sesión', sanitize=True)
    live_state = fields.Selection(LIVE_STATES, string='Reloj', default='idle', copy=False)
    live_line_id = fields.Many2one('scout.meeting.agenda.line', string='Punto en curso', copy=False)
    live_started = fields.Datetime(copy=False, help='Cuándo empezó a correr el turno en curso (último arranque).')
    live_elapsed = fields.Float(copy=False, help='Segundos acumulados del turno en curso antes del último arranque.')
    live_turn_seconds = fields.Integer(string='Tiempo del turno (s)', copy=False)
    live_speaker_id = fields.Many2one('res.partner', string='Tiene la palabra', copy=False)
    live_speaker_name = fields.Char(string='Nombre de quien habla', copy=False)
    live_turn_key = fields.Integer(copy=False, help='Número de turno (cambia cada vez que se da la palabra).')
    live_turn_notified = fields.Boolean(copy=False, help='Ya se avisó en el chat que terminó el tiempo del turno.')
    live_auto_mute = fields.Boolean(string='Silenciar el micrófono al terminar el tiempo', default=True)
    live_grace = fields.Integer(string='Segundos de gracia antes de silenciar', default=10)
    live_chat_notices = fields.Boolean(string='Avisos en el chat de la videollamada', default=True)
    turn_ids = fields.One2many('scout.meeting.turn', 'meeting_id', string='Turnos de palabra')
    live_session_start = fields.Datetime(string='Inicio real', copy=False)
    live_session_end = fields.Datetime(string='Fin real', copy=False)
    live_recorder_id = fields.Many2one('res.users', string='Grabando', copy=False)
    live_recording_since = fields.Datetime(copy=False)
    videocall_url = fields.Char(string='Videollamada', compute='_compute_videocall_url')

    @api.depends('agenda_line_ids.duration', 'agenda_line_ids.extra_minutes')
    def _compute_agenda_minutes(self):
        for meeting in self:
            meeting.agenda_minutes = sum(l.duration + l.extra_minutes for l in meeting.agenda_line_ids)

    @api.depends('transcript_ids.text')
    def _compute_transcript_text(self):
        for meeting in self:
            meeting.transcript_text = meeting._scout_transcript_plain()

    @api.depends('event_id', 'location')
    def _compute_videocall_url(self):
        for meeting in self:
            url = meeting.event_id.sudo().videocall_location if meeting.event_id else False
            if not url and meeting.location and re.match(r'^https?://', meeting.location.strip()):
                url = meeting.location.strip()
            meeting.videocall_url = url or False

    def write(self, vals):
        res = super().write(vals)
        if 'agenda_line_ids' in vals or 'date' in vals:
            self.filtered('agenda_line_ids')._scout_sync_agenda_html()
        return res

    # ------------------------------------------------------------------ permisos
    def _scout_tz(self):
        return pytz.timezone(self.env.user.tz or DEFAULT_TZ)

    def _scout_can_view(self, user=None):
        """Dirigentes del grupo (portal) y usuarios internos de la plataforma scout."""
        self.ensure_one()
        user = user or self.env.user
        if self.company_id not in (user.company_ids | user.company_id):
            return False
        if not user.share and user.has_group('scout_management.group_scout_leader'):
            return True
        return bool(user._scout_leader_sections()) or user.partner_id in (
            self.attendee_ids | self.chair_id | self.secretary_id)

    def _scout_can_moderate(self, user=None):
        """Modera: quien preside, la secretaría o la jefatura de grupo."""
        self.ensure_one()
        user = user or self.env.user
        if not self._scout_can_view(user):
            return False
        if user.partner_id in (self.chair_id | self.secretary_id):
            return True
        if not user.share and user.has_group('scout_management.group_scout_officer'):
            return True
        return bool(user._scout_leader_sections().filtered('is_leadership'))

    def _scout_check(self, moderate=False):
        self.ensure_one()
        if not (self._scout_can_moderate() if moderate else self._scout_can_view()):
            raise AccessError(_('Solo quien preside, la secretaría o la jefatura de grupo pueden hacer esto.')
                              if moderate else _('No tienes acceso a esta reunión.'))
        return self.sudo()

    # ------------------------------------------------------------------ orden del día
    def _scout_set_agenda(self, points, replace=True):
        """points: [{'name', 'minutes', 'responsible'}]"""
        self.ensure_one()
        if self.live_state in ('running', 'paused', 'waiting') and replace:
            raise UserError(_('El reloj ya está corriendo; termina o reinicia la reunión para cambiar el orden del día.'))
        commands = [(5, 0, 0)] if replace else []
        start = max(self.agenda_line_ids.mapped('sequence') or [0]) if not replace else 0
        for k, p in enumerate(points, 1):
            name = str(p.get('name') or '').strip()
            if not name:
                continue
            try:
                minutes = max(1, min(600, int(p.get('minutes') or 5)))
            except (TypeError, ValueError):
                minutes = 5
            commands.append((0, 0, {'sequence': start + k * 10, 'name': name[:200], 'duration': minutes,
                                    'responsible': str(p.get('responsible') or '')[:120] or False}))
        self.write({'agenda_line_ids': commands, 'live_state': 'idle', 'live_line_id': False})
        self._scout_sync_agenda_html()

    def _scout_sync_agenda_html(self):
        """El campo de texto "Orden del día" (el que sale en el PDF del acta) se arma con los puntos."""
        for meeting in self.filtered('agenda_line_ids'):
            starts = meeting._scout_planned_starts()
            rows = ''.join(
                '<li>%s%s <small>(%s min)</small></li>' % (
                    ('<b>%s</b> · ' % starts[line.id]) if starts.get(line.id) else '', html_escape(line.name),
                    line.duration + line.extra_minutes)
                for line in meeting.agenda_line_ids)
            meeting.with_context(scout_meeting_force=True).agenda = '<ol>%s</ol>' % rows

    def _scout_planned_starts(self):
        """{line_id: 'H:MM'} desde la hora de la reunión."""
        self.ensure_one()
        if not self.date:
            return {}
        local = pytz.utc.localize(self.date).astimezone(self._scout_tz())
        out, cursor = {}, local
        for line in self.agenda_line_ids:
            out[line.id] = '%d:%02d' % (cursor.hour, cursor.minute)
            cursor += datetime.timedelta(minutes=line.duration)
        return out

    def action_scout_load_template(self):
        for meeting in self:
            meeting._scout_set_agenda([{'name': n, 'minutes': m} for n, m in COUNCIL_TEMPLATE])
        return True

    def _scout_agenda_from_file(self, content, mimetype):
        """Lee el orden del día de una imagen o PDF (IA) o del texto del PDF (sin IA)."""
        self.ensure_one()
        Event = self.env['calendar.event']
        if Event._scout_ai_ready():
            provider, key, model = Event._scout_ai_config()
            try:
                raw = getattr(Event, f'_scout_ai_{provider}')(key, model, AGENDA_PROMPT, content, mimetype)
            except UserError:
                raise
            except Exception as e:  # noqa: BLE001
                _logger.exception('No se pudo leer el orden del día con IA')
                raise UserError(_('No se pudo leer el archivo con la inteligencia artificial: %s', e)) from e
            data = self._scout_json(raw)
            items = data.get('puntos') if isinstance(data, dict) else data
            points = [{'name': i.get('actividad') or i.get('name'), 'minutes': i.get('minutos') or i.get('minutes')}
                      for i in (items or []) if isinstance(i, dict)]
            if points:
                return points
        if mimetype == 'application/pdf':
            from .scout_pdf_layout import pdf_items
            items = pdf_items(content, max_pages=3)
            lines, current = [], None
            for item in sorted(items, key=lambda i: (i['page'], -round(i['y']), i['x'])):
                key = (item['page'], round(item['y'] / 3))
                if current and current[0] == key:
                    current[1].append(item['text'])
                else:
                    current = [key, [item['text']]]
                    lines.append(current)
            points = parse_agenda_text('\n'.join(' '.join(l[1]) for l in lines))
            points = [p for p in points if re.search(r'[a-záéíóúñ]{3}', p['name'], re.I)]
            if points:
                return points
        if mimetype.startswith('text/'):
            return parse_agenda_text(content.decode('utf-8', errors='replace'))
        raise UserError(_('Para leer una imagen del orden del día se necesita la IA (Datos del grupo → Inteligencia '
                          'artificial). Mientras tanto puedes pegar el texto o usar la plantilla del consejo.'))

    @api.model
    def _scout_json(self, raw):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', (raw or '').strip())
        match = re.search(r'\{.*\}|\[.*\]', text, re.S)
        try:
            return json.loads(match.group(0) if match else text)
        except (ValueError, AttributeError):
            return {}

    # ------------------------------------------------------------------ reloj de turnos de palabra
    def _scout_elapsed(self, now=None):
        """Segundos que lleva quien tiene la palabra."""
        self.ensure_one()
        now = now or fields.Datetime.now()
        elapsed = self.live_elapsed or 0.0
        if self.live_state == 'running' and self.live_started:
            elapsed += max(0.0, (now - self.live_started).total_seconds())
        return elapsed

    def _scout_close_turn(self, now):
        """Cierra el turno en curso: se anota cuánto habló y se suma al punto del orden del día."""
        if self.live_state not in ('running', 'paused') or not self.live_started:
            return
        seconds = int(round(self._scout_elapsed(now)))
        if seconds >= 2:
            self.env['scout.meeting.turn'].sudo().create({
                'meeting_id': self.id, 'agenda_line_id': self.live_line_id.id or False,
                'partner_id': self.live_speaker_id.id or False,
                'speaker_name': self.live_speaker_name or self.live_speaker_id.name or False,
                'planned_seconds': self.live_turn_seconds, 'seconds': seconds,
                'start': now - datetime.timedelta(seconds=seconds)})
            if self.live_line_id:
                self.live_line_id.spent_seconds += seconds
        self.write({'live_elapsed': 0.0, 'live_started': False})

    def _scout_turn_vals(self, line, now, speaker=None, name=None, seconds=None, state='running'):
        line = line or self.env['scout.meeting.agenda.line']
        if seconds is None:
            seconds = (line.duration + line.extra_minutes) * 60 if line else 180
        if speaker is None and not name and line and line.responsible:
            name = line.responsible
        return {'live_state': state, 'live_line_id': line.id or False, 'live_started': now, 'live_elapsed': 0.0,
                'live_turn_key': (self.live_turn_key or 0) + 1, 'live_turn_notified': False,
                'live_turn_seconds': max(30, int(seconds)), 'live_speaker_id': speaker.id if speaker else False,
                'live_speaker_name': (name or (speaker.name if speaker else '') or '')[:120] or False}

    def _scout_live_control(self, op, line_id=None, minutes=None, partner_id=None, name=None, cfg=None):
        """Botones de quien modera. El reloj cuenta el TURNO de quien tiene la palabra; el orden del día solo
        propone a quién le toca y cuántos minutos."""
        meeting = self._scout_check(moderate=True)
        now = fields.Datetime.now()
        lines = meeting.agenda_line_ids
        current = meeting.live_line_id
        index = list(lines.ids).index(current.id) if current in lines else -1
        session = {} if meeting.live_session_start and meeting.live_state != 'done' else {
            'live_session_start': now, 'live_session_end': False}
        if op == 'start':
            if meeting.live_state in ('idle', 'done'):
                lines.write({'spent_seconds': 0, 'extra_minutes': 0})
                meeting.write(dict(meeting._scout_turn_vals(lines[:1], now), live_session_start=now,
                                   live_session_end=False))
            elif meeting.live_state == 'paused':
                meeting.write({'live_state': 'running', 'live_started': now})
            elif meeting.live_state == 'waiting':
                meeting.write(dict(meeting._scout_turn_vals(current, now)))
        elif op == 'pause' and meeting.live_state == 'running':
            meeting.write({'live_state': 'paused', 'live_elapsed': meeting._scout_elapsed(now), 'live_started': now})
        elif op == 'floor':
            # dar la palabra a alguien (participante de la llamada o del pase de lista)
            speaker = self.env['res.partner'].sudo().browse(int(partner_id or 0)).exists()
            if not speaker and not (name or '').strip():
                raise UserError(_('Elige a quién le das la palabra.'))
            try:
                seconds = max(0.5, float(minutes or 3)) * 60
            except (TypeError, ValueError):
                seconds = 180
            meeting._scout_close_turn(now)
            meeting.write(dict(meeting._scout_turn_vals(current or lines[:1], now, speaker=speaker or False,
                                                        name=(name or '').strip(), seconds=seconds), **session))
            meeting._scout_chat_notice(_('🎤 Tiene la palabra: %(who)s · %(min)s min%(point)s', who=meeting.live_speaker_name
                                         or '—', min=('%g' % (seconds / 60)),
                                         point=(' · %s' % meeting.live_line_id.name) if meeting.live_line_id else ''))
        elif op == 'settings':
            vals = {}
            cfg = cfg or {}
            if cfg:
                if 'auto_mute' in cfg:
                    vals['live_auto_mute'] = bool(cfg['auto_mute'])
                if 'grace' in cfg:
                    vals['live_grace'] = max(0, min(120, int(cfg['grace'] or 0)))
                if 'chat_notices' in cfg:
                    vals['live_chat_notices'] = bool(cfg['chat_notices'])
            meeting.write(vals)
        elif op == 'end_turn':
            meeting._scout_close_turn(now)
            meeting.write({'live_state': 'waiting', 'live_speaker_id': False, 'live_speaker_name': False})
        elif op in ('next', 'prev', 'goto'):
            meeting._scout_close_turn(now)
            if op == 'goto':
                target = lines.filtered(lambda l: l.id == int(line_id or 0))[:1]
            elif op == 'next':
                target = lines[index + 1] if index + 1 < len(lines) else lines.browse()
            else:
                target = lines[max(0, index - 1)] if index >= 0 else lines[:1]
            if not target:
                meeting.write({'live_state': 'done', 'live_line_id': False, 'live_speaker_id': False,
                               'live_speaker_name': False, 'live_session_end': now})
            else:
                meeting.write(dict(meeting._scout_turn_vals(target, now), **session))
        elif op == 'add' and meeting.live_state in ('running', 'paused'):
            meeting.live_turn_seconds = max(30, meeting.live_turn_seconds + int(float(minutes or 1) * 60))
        elif op == 'restart_turn' and meeting.live_state in ('running', 'paused'):
            meeting.write({'live_elapsed': 0.0, 'live_started': now})
        elif op == 'finish':
            meeting._scout_close_turn(now)
            meeting.write({'live_state': 'done', 'live_line_id': False, 'live_speaker_id': False,
                           'live_speaker_name': False, 'live_session_end': now, 'live_recorder_id': False})
        elif op == 'reset':
            meeting.turn_ids.unlink()
            lines.write({'spent_seconds': 0, 'extra_minutes': 0})
            meeting.write({'live_state': 'idle', 'live_line_id': False, 'live_elapsed': 0.0, 'live_started': False,
                           'live_speaker_id': False, 'live_speaker_name': False,
                           'live_session_start': False, 'live_session_end': False})
            meeting._scout_sync_agenda_html()
        return meeting._scout_live_payload()

    def _scout_videocall_channel(self):
        self.ensure_one()
        event = self.sudo().event_id
        return event.videocall_channel_id if event and 'videocall_channel_id' in event._fields else False

    def _scout_chat_notice(self, text):
        """Mensaje en el chat de la videollamada del consejo (lo ven todos los participantes)."""
        meeting = self.sudo()
        channel = meeting._scout_videocall_channel()
        if not channel or not meeting.live_chat_notices:
            return
        try:
            channel.message_post(body=text, message_type='comment', subtype_xmlid='mail.mt_comment',
                                 author_id=self.env.ref('base.partner_root').id)
        except Exception:  # noqa: BLE001 - el aviso nunca debe frenar el reloj
            _logger.info('No se pudo avisar en el chat de la videollamada', exc_info=True)

    def _scout_turn_expired(self, key):
        """Algún participante detectó que el turno se acabó: se avisa UNA vez en el chat de la llamada."""
        meeting = self._scout_check()
        if meeting.live_state != 'running' or meeting.live_turn_key != int(key or 0) or meeting.live_turn_notified:
            return {'ok': False}
        if meeting._scout_elapsed() < (meeting.live_turn_seconds or 0) - 2:
            return {'ok': False}
        meeting.live_turn_notified = True
        who = meeting.live_speaker_name or meeting.live_speaker_id.name or _('Quien tiene la palabra')
        mute = _(' Su micrófono se silenciará en %s s.', meeting.live_grace) if meeting.live_auto_mute else ''
        meeting._scout_chat_notice(_('⏱ Terminó el tiempo de %(who)s (%(min)s min).%(mute)s', who=who,
                                     min='%g' % ((meeting.live_turn_seconds or 0) / 60), mute=mute))
        return {'ok': True}

    def _scout_live_payload(self, full=True):
        """Estado del reloj para la pantalla y para la videollamada."""
        self.ensure_one()
        meeting = self.sudo()
        now = fields.Datetime.now()
        lines = meeting.agenda_line_ids
        current = meeting.live_line_id
        index = list(lines.ids).index(current.id) if current in lines else -1
        planned_total = sum(l.duration + l.extra_minutes for l in lines) * 60
        session_elapsed = 0
        if meeting.live_session_start:
            session_elapsed = ((meeting.live_session_end or now) - meeting.live_session_start).total_seconds()
        active = meeting.live_state in ('running', 'paused')
        turn_elapsed = meeting._scout_elapsed(now) if active else 0
        payload = {
            'id': meeting.id,
            'title': meeting.title,
            'state': meeting.live_state,
            'running': meeting.live_state == 'running',
            'can_moderate': meeting._scout_can_moderate(),
            'turn': active and {
                'speaker': meeting.live_speaker_name or meeting.live_speaker_id.name or '',
                'partner_id': meeting.live_speaker_id.id or False,
                'duration': meeting.live_turn_seconds or 180,
                'elapsed': round(turn_elapsed, 1),
                'key': meeting.live_turn_key,
            } or None,
            'auto_mute': meeting.live_auto_mute,
            'grace': max(0, meeting.live_grace or 0),
            'chat_notices': meeting.live_chat_notices,
            'point': current and {
                'id': current.id, 'name': current.name, 'responsible': current.responsible or '',
                'planned': (current.duration + current.extra_minutes) * 60,
                'spent': int(current.spent_seconds + turn_elapsed),
            } or None,
            'next': None,
            'index': index,
            'count': len(lines),
            'session_elapsed': int(session_elapsed),
            'planned_total': planned_total,
            'drift': int(session_elapsed - sum((l.duration + l.extra_minutes) * 60 for l in lines[:index])
                         - (current.spent_seconds + turn_elapsed if current else 0))
            if meeting.live_session_start and index >= 0 and meeting.live_state != 'done' else None,
            'recording': meeting.live_recorder_id.name or False,
            'recording_mine': meeting.live_recorder_id == self.env.user,
            'transcript_count': len(meeting.transcript_ids),
            'videocall_url': meeting.videocall_url or False,
            'live_url': '/scouts/consejo/%s' % meeting.id,
            'checkins': len(meeting.checkin_ids),
            'my_checkin': self._scout_my_checkin() or False,
        }
        if index >= 0 and index < len(lines) - 1:
            nxt = lines[index + 1]
            payload['next'] = {'name': nxt.name, 'responsible': nxt.responsible or '',
                               'duration': (nxt.duration + nxt.extra_minutes) * 60}
        elif meeting.live_state == 'idle' and lines:
            payload['next'] = {'name': lines[0].name, 'responsible': lines[0].responsible or '',
                               'duration': (lines[0].duration + lines[0].extra_minutes) * 60}
        if full:
            starts = meeting._scout_planned_starts()
            payload['lines'] = [{
                'id': l.id, 'name': l.name, 'duration': l.duration + l.extra_minutes, 'extra': l.extra_minutes,
                'responsible': l.responsible or '', 'start': starts.get(l.id, ''),
                'spent': int(l.spent_seconds + (turn_elapsed if l == current else 0)),
                'status': 'current' if l == current else ('done' if (index >= 0 and k < index) or (
                    meeting.live_state == 'done' and l.spent_seconds) else 'pending'),
            } for k, l in enumerate(lines)]
            payload['turns'] = [{
                'speaker': t.speaker_name or t.partner_id.name or '', 'seconds': t.seconds,
                'planned': t.planned_seconds, 'over': t.seconds > t.planned_seconds,
                'point': t.agenda_line_id.name or '', 'time': self._scout_hhmm(t.start),
            } for t in meeting.turn_ids.sorted(lambda t: (t.start or now, t.id), reverse=True)[:30]]
            per = {}
            for t in meeting.turn_ids:
                key = t.speaker_name or t.partner_id.name or _('Sin nombre')
                per[key] = per.get(key, 0) + t.seconds
            payload['speakers'] = sorted(({'name': k, 'seconds': v} for k, v in per.items()),
                                         key=lambda x: -x['seconds'])
            last = meeting.transcript_ids.sorted(lambda t: (t.time, t.id))[-3:]
            payload['last_text'] = [{'time': self._scout_hhmm(t.time), 'text': (t.text or '')[-400:],
                                     'point': t.agenda_line_id.name or ''} for t in last if t.text]
        return payload

    def _scout_hhmm(self, dt):
        if not dt:
            return ''
        local = pytz.utc.localize(dt).astimezone(self._scout_tz())
        return '%d:%02d' % (local.hour, local.minute)

    # ------------------------------------------------------------------ pase de lista
    def _scout_roster(self):
        """Dirigentes del grupo para el pase de lista del consejo."""
        self.ensure_one()
        Partner = self.env['res.partner'].sudo()
        leaders = Partner.search([('is_scout', '=', True), ('scout_type', '=', 'leader'),
                                  ('scout_state', 'in', ('trial', 'active')),
                                  ('company_id', 'in', [self.company_id.id, False])])
        return (leaders | self.attendee_ids | self.chair_id | self.secretary_id).sorted(
            lambda p: (p.scout_position_id.sequence if 'sequence' in p.scout_position_id._fields else 0,
                       p.name or ''))

    def _scout_mark_attendance(self, partner_id, present):
        meeting = self._scout_check(moderate=True)
        partner = self.env['res.partner'].sudo().browse(int(partner_id)).exists()
        if not partner:
            return False
        if present:
            meeting.with_context(scout_meeting_force=True).write(
                {'attendee_ids': [(4, partner.id)], 'absent_ids': [(3, partner.id)]})
        else:
            meeting.with_context(scout_meeting_force=True).write(
                {'attendee_ids': [(3, partner.id)], 'absent_ids': [(4, partner.id)]})
        return True

    # ------------------------------------------------------------------ transcripción
    def _scout_recording(self, on):
        meeting = self._scout_check(moderate=True)
        if on:
            if meeting.live_recorder_id and meeting.live_recorder_id != self.env.user and meeting.live_recording_since \
                    and (fields.Datetime.now() - meeting.live_recording_since).total_seconds() < 150:
                raise UserError(_('%s ya está grabando la reunión.', meeting.live_recorder_id.name))
            meeting.write({'live_recorder_id': self.env.user.id, 'live_recording_since': fields.Datetime.now()})
        elif meeting.live_recorder_id == self.env.user or meeting._scout_can_moderate():
            meeting.write({'live_recorder_id': False, 'live_recording_since': False})
        return meeting._scout_live_payload(full=False)

    def _scout_add_text(self, text, source='manual', speakers='', when=None, line=None):
        meeting = self.sudo()
        text = (text or '').strip()
        if not text:
            return False
        line = line or meeting.live_line_id
        if not line and when and meeting.agenda_line_ids:
            line = meeting.agenda_line_ids[-1] if meeting.live_state == 'done' else meeting.agenda_line_ids.browse()
        return self.env['scout.meeting.transcript'].sudo().create({
            'meeting_id': meeting.id, 'agenda_line_id': line.id if line else False, 'text': text[:20000],
            'source': source, 'speakers': (speakers or '')[:250] or False, 'time': when or fields.Datetime.now(),
            'user_id': self.env.user.id})

    def _scout_transcribe(self, content, mimetype='audio/wav', speakers=None, source='call'):
        """Transcribe un trozo de audio y lo agrega a la transcripción. speakers: [{'name', 'from', 'to'}] (s)."""
        meeting = self._scout_check(moderate=True)
        if meeting.live_recorder_id == self.env.user:
            meeting.live_recording_since = fields.Datetime.now()
        if not content or len(content) < 2000:
            return {'text': ''}
        Event = self.env['calendar.event']
        provider, key, model = Event._scout_ai_config()
        if not key:
            raise UserError(_('Para transcribir el audio configura la IA (Gemini u OpenAI) en Datos del grupo. '
                              'Sin IA usa "Dictado del navegador" (Chrome).'))
        if provider == 'anthropic':
            raise UserError(_('La IA configurada (Claude) no transcribe audio. Cambia a Gemini u OpenAI en Datos del '
                              'grupo, o usa "Dictado del navegador" (Chrome).'))
        names = []
        for s in speakers or []:
            name = str(s.get('name') or '').strip()
            if name and name not in names:
                names.append(name)
        timeline = '; '.join('%s del segundo %s al %s' % (s.get('name'), int(s.get('from') or 0), int(s.get('to') or 0))
                             for s in (speakers or [])[:60] if s.get('name'))
        point = meeting.live_line_id.name or meeting.title
        holder = meeting.live_speaker_name or meeting.live_speaker_id.name
        if holder and meeting.live_state == 'running':
            point = '%s (tiene la palabra: %s)' % (point, holder)
            if holder not in names:
                names.insert(0, holder)
        try:
            if provider == 'openai':
                text = meeting._scout_transcribe_openai(key, content, mimetype, names, point)
            else:
                prompt = TRANSCRIBE_PROMPT.format(
                    point=point, speakers=('Quién habló (según la videollamada): %s.' % timeline) if timeline else '')
                raw = Event._scout_ai_gemini(key, model, prompt, content, mimetype)
                data = self._scout_json(raw)
                text = data.get('texto') if isinstance(data, dict) else ''
                text = text if isinstance(text, str) else ''
        except UserError:
            raise
        except requests.exceptions.Timeout as e:
            raise UserError(_('La transcripción tardó demasiado; se intentará con el siguiente trozo.')) from e
        except requests.exceptions.ConnectionError as e:
            raise UserError(_('El servidor no pudo conectarse con la IA; se intentará con el siguiente trozo.')) from e
        except Exception as e:  # noqa: BLE001
            _logger.exception('No se pudo transcribir el audio de la reunión')
            raise UserError(_('No se pudo transcribir: %s', e)) from e
        text = (text or '').strip()
        if re.fullmatch(r'\[?(inaudible|silencio|m[uú]sica|ruido)\]?\.?', _plain(text)):
            text = ''
        if text:
            meeting._scout_add_text(text, source=source, speakers=', '.join(names))
        return {'text': text}

    def _scout_transcribe_openai(self, key, content, mimetype, names, point):
        ext = {'audio/wav': 'wav', 'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mpeg': 'mp3', 'audio/mp4': 'm4a',
               'audio/x-m4a': 'm4a', 'video/mp4': 'mp4'}.get(mimetype, 'wav')
        prompt = 'Reunión de dirigentes scouts en español. Tema: %s. %s' % (
            point, ('Participantes: %s.' % ', '.join(names)) if names else '')
        for model in ('gpt-4o-mini-transcribe', 'whisper-1'):
            response = requests.post(
                'https://api.openai.com/v1/audio/transcriptions', timeout=120,
                headers={'Authorization': f'Bearer {key}'},
                files={'file': ('audio.%s' % ext, content, mimetype)},
                data={'model': model, 'language': 'es', 'prompt': prompt[:800], 'response_format': 'json'})
            if response.status_code == 404 or (response.status_code == 400 and 'model' in response.text.lower()):
                continue
            data = self.env['calendar.event']._scout_ai_check(response)
            return data.get('text') or ''
        return ''

    def _scout_transcript_plain(self, by_point=False):
        self.ensure_one()
        chunks = self.sudo().transcript_ids.sorted(lambda t: (t.time or fields.Datetime.now(), t.id))
        if not by_point:
            return '\n'.join('[%s] %s' % (self._scout_hhmm(t.time), t.text) for t in chunks if t.text)
        blocks, last = [], None
        for t in chunks:
            if not t.text:
                continue
            if t.agenda_line_id != last or not blocks:
                last = t.agenda_line_id
                n = (list(self.agenda_line_ids.ids).index(last.id) + 1) if last in self.agenda_line_ids else 0
                blocks.append(['%s' % ('Punto %s. %s' % (n, last.name) if n else 'Sin punto asignado'), []])
            blocks[-1][1].append(t.text)
        return '\n\n'.join('### %s\n%s' % (head, '\n'.join(texts)) for head, texts in blocks)

    # ------------------------------------------------------------------ acta
    def action_scout_generate_minutes(self):
        """Llena el acta (desarrollo por punto, acuerdos y resumen) a partir de la transcripción."""
        for meeting in self:
            meeting._scout_generate_minutes()
        return True

    def _scout_generate_minutes(self):
        meeting = self._scout_check(moderate=True)
        if meeting.state not in ('draft',):
            raise UserError(_('El acta ya se envió o está aprobada; para regenerarla primero regrésala a borrador.'))
        if not meeting.transcript_ids.filtered('text'):
            raise UserError(_('Todavía no hay transcripción de esta reunión.'))
        Event = self.env['calendar.event']
        lines = meeting.agenda_line_ids
        if not Event._scout_ai_ready():
            meeting._scout_minutes_without_ai()
            return {'ai': False}
        provider, key, model = Event._scout_ai_config()
        transcript = meeting._scout_transcript_plain(by_point=True)
        if len(transcript) > 180000:
            transcript = transcript[:90000] + '\n[…]\n' + transcript[-90000:]
        agenda = '\n'.join('%s. %s' % (k, l.name) for k, l in enumerate(lines, 1)) or '(sin orden del día)'
        local = pytz.utc.localize(meeting.date).astimezone(self._scout_tz()) if meeting.date else None
        prompt = MINUTES_PROMPT.format(
            title=meeting.title, date=local.strftime('%d/%m/%Y %H:%M') if local else '',
            attendees=', '.join(meeting.attendee_ids.mapped('name')) or 'no registrados', agenda=agenda,
            turns='\n'.join('- %s: %s min (%s)' % (t.speaker_name or t.partner_id.name or '?', t.used_display,
                                                    t.agenda_line_id.name or '') for t in meeting.turn_ids)
            or '(no se cronometraron turnos)',
            transcript=transcript)
        try:
            if provider == 'anthropic':
                raw = Event.with_context(scout_ai_max_tokens=8000)._scout_ai_anthropic(key, model, prompt, None, None)
            else:
                raw = getattr(Event, f'_scout_ai_{provider}')(key, model, prompt, None, None)
        except UserError:
            raise
        except Exception as e:  # noqa: BLE001
            _logger.exception('No se pudo generar el acta con IA')
            raise UserError(_('No se pudo generar el acta con la inteligencia artificial: %s', e)) from e
        data = self._scout_json(raw)
        if not isinstance(data, dict) or not (data.get('puntos') or data.get('resumen') or data.get('acuerdos')):
            raise UserError(_('La inteligencia artificial no devolvió un acta legible. Intenta de nuevo.'))
        by_n = {}
        for item in data.get('puntos') or []:
            if isinstance(item, dict):
                try:
                    by_n[int(item.get('n'))] = str(item.get('resumen') or '').strip()
                except (TypeError, ValueError):
                    continue
        for k, line in enumerate(lines, 1):
            line.summary = by_n.get(k) or False
        body = ''.join('<h5>%s. %s</h5><p>%s</p>' % (k, html_escape(l.name), html_escape(l.summary).replace(
            '\n', '<br/>') if l.summary else '<i>Sin comentarios registrados.</i>') for k, l in enumerate(lines, 1))
        if not lines:
            body = ''.join('<p>%s</p>' % html_escape(v) for v in by_n.values())
        vals = {'minutes': body or False,
                'ai_summary': '<p>%s</p>' % html_escape(str(data.get('resumen') or '')).replace('\n', '<br/>')
                if data.get('resumen') else False}
        nxt = str(data.get('proxima_reunion') or '').strip()
        m = re.match(r'(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2}))?', nxt)
        if m:
            try:
                local_dt = self._scout_tz().localize(datetime.datetime(
                    int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4) or 19), int(m.group(5) or 0)))
                vals['next_meeting'] = local_dt.astimezone(pytz.utc).replace(tzinfo=None)
            except ValueError:
                pass
        meeting.agreement_ids.filtered('ai_generated').unlink()
        agreements = []
        for k, item in enumerate(data.get('acuerdos') or [], 1):
            if not isinstance(item, dict) or not str(item.get('acuerdo') or '').strip():
                continue
            due = False
            if re.match(r'^\d{4}-\d{2}-\d{2}$', str(item.get('fecha') or '')):
                try:
                    due = fields.Date.to_date(item['fecha'])
                except ValueError:
                    due = False
            agreements.append((0, 0, {'sequence': 100 + k, 'name': str(item['acuerdo']).strip(), 'ai_generated': True,
                                      'responsible_id': meeting._scout_match_person(item.get('responsable')),
                                      'due_date': due}))
        vals['agreement_ids'] = agreements
        meeting.with_context(scout_meeting_force=True).write(vals)
        meeting.message_post(body=_('Acta generada con IA a partir de la transcripción (%s trozos). Revísala antes de '
                                    'enviarla a aprobación.', len(meeting.transcript_ids)))
        return {'ai': True, 'agreements': len(agreements)}

    def _scout_minutes_without_ai(self):
        """Sin IA: el desarrollo de cada punto es la transcripción tal cual."""
        lines = self.agenda_line_ids
        parts = []
        for k, line in enumerate(lines, 1):
            texts = self.transcript_ids.filtered(lambda t: t.agenda_line_id == line and t.text).mapped('text')
            parts.append('<h5>%s. %s</h5>%s' % (k, html_escape(line.name), ''.join(
                '<p>%s</p>' % html_escape(t) for t in texts) or '<p><i>Sin comentarios registrados.</i></p>'))
        loose = self.transcript_ids.filtered(lambda t: not t.agenda_line_id and t.text).mapped('text')
        if loose:
            parts.append('<h5>%s</h5>%s' % (_('Otros comentarios'), ''.join('<p>%s</p>' % html_escape(t)
                                                                           for t in loose)))
        self.with_context(scout_meeting_force=True).write({'minutes': ''.join(parts)})
        self.message_post(body=_('Desarrollo de la sesión llenado con la transcripción (sin IA). Revísalo y '
                                 'escribe los acuerdos.'))

    def _scout_match_person(self, text):
        text = _plain(text)
        if not text:
            return False
        people = self._scout_roster()
        for person in people:
            names = [_plain(person.name), _plain(person.scout_name or ''), _plain(person.scout_position_id.name or '')]
            if any(n and (n == text or n in text or (len(text) > 3 and text in n)) for n in names):
                return person.id
        first = text.split()[0] if text.split() else ''
        matches = people.filtered(lambda p: first and _plain(p.name).split()[:1] == [first])
        return matches.id if len(matches) == 1 else False

    # ------------------------------------------------------------------ videollamada
    def _scout_ensure_videocall(self):
        """Crea (si falta) el evento del consejo con videollamada de Odoo y regresa la liga."""
        meeting = self._scout_check(moderate=True)
        event = meeting.event_id.sudo()
        if not event:
            start = meeting.date or fields.Datetime.now()
            minutes = meeting.agenda_minutes or 120
            event = self.env['calendar.event'].sudo().create({
                'name': meeting.title, 'start': start, 'stop': start + datetime.timedelta(minutes=minutes),
                'is_scout_event': True, 'scout_activity_type': 'council', 'scout_scope': 'group',
                'user_id': self.env.user.id if not self.env.user.share else self.env.ref('base.user_admin').id,
            })
            meeting.event_id = event
        if not event.videocall_location:
            event._set_discuss_videocall_location()
        return event.videocall_location

    @api.model
    def _scout_for_channel(self, channel_id):
        """Acta ligada a la videollamada (canal de Discuss) del evento del consejo."""
        events = self.env['calendar.event'].sudo().search([('videocall_channel_id', '=', int(channel_id))], limit=5)
        if not events:
            return self.browse()
        return self.sudo().search([('event_id', 'in', events.ids), ('state', '!=', 'cancel')], limit=1,
                                  order='date desc')

    @api.model
    def _scout_for_event(self, event):
        """Acta del evento; si no existe se crea con el orden del día habitual del consejo."""
        event = event.sudo()
        meeting = self.sudo().search([('event_id', '=', event.id), ('state', '!=', 'cancel')], limit=1)
        if meeting:
            return meeting
        company = self.env.user.company_id
        Partner = self.env['res.partner'].sudo()
        base = [('is_scout', '=', True), ('scout_type', '=', 'leader'), ('scout_state', 'in', ('trial', 'active')),
                ('company_id', 'in', [company.id, False])]
        secretary = Partner.search(base + [('scout_position_id.kind', '=', 'secretary')], limit=1)
        chief = Partner.search(base + [('scout_position_id.kind', '=', 'group_chief')], limit=1)
        meeting = self.sudo().create({
            'title': event.name or _('Consejo de Grupo'), 'event_id': event.id, 'date': event.start,
            'mode': 'online' if event.videocall_location else 'onsite', 'location': event.location or False,
            'company_id': company.id, 'secretary_id': secretary.id or False, 'chair_id': chief.id or False,
        })
        if event.scout_activity_type == 'council':
            meeting.action_scout_load_template()
        return meeting

    def action_scout_open_live(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': '/scouts/consejo/%s' % self.id, 'target': 'new'}

    def action_scout_videocall(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': self._scout_ensure_videocall(), 'target': 'new'}

    def action_scout_clear_transcript(self):
        self._scout_check(moderate=True).transcript_ids.unlink()
        return True


# ---------------------------------------------------------------------- asistencia firmada al entrar
class ScoutMeetingCheckin(models.Model):
    """Firma de asistencia: se registra sola cuando la persona entra a la videollamada de Odoo del consejo
    (o desde la pantalla del consejo / el pase de lista). Guarda quién, a qué hora entró y salió, y desde dónde."""
    _name = 'scout.meeting.checkin'
    _description = 'Firma de asistencia a la reunión'
    _order = 'meeting_id, time_in, id'

    meeting_id = fields.Many2one('scout.meeting', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one('res.partner', string='Persona', index=True)
    guest_name = fields.Char(string='Invitado')
    name = fields.Char(string='Nombre', compute='_compute_name', store=True)
    time_in = fields.Datetime(string='Entró', default=fields.Datetime.now, required=True)
    time_out = fields.Datetime(string='Salió')
    minutes = fields.Integer(string='Minutos en la reunión')
    source = fields.Selection([('call', 'Videollamada de Odoo'), ('portal', 'Pantalla del consejo'),
                               ('roll', 'Pase de lista')], default='call', required=True, string='Firmó en')
    ip = fields.Char(string='IP')
    signature = fields.Char(string='Firma', readonly=True,
                            help='Huella de la firma electrónica (persona, reunión, hora e IP).')

    _partner_unique = models.Constraint('UNIQUE(meeting_id, partner_id)', 'La persona ya firmó su asistencia.')

    @api.depends('partner_id', 'guest_name')
    def _compute_name(self):
        for rec in self:
            rec.name = rec.partner_id.name or rec.guest_name or _('Invitado')

    @api.model
    def _scout_sign(self, meeting, partner=None, guest_name=None, source='call', ip=None):
        """Firma la asistencia (una vez por persona; si vuelve a entrar se conserva la primera hora)."""
        import hashlib
        meeting = meeting.sudo()
        if not meeting or meeting.state == 'cancel':
            return self.browse()
        domain = [('meeting_id', '=', meeting.id)]
        domain += [('partner_id', '=', partner.id)] if partner else [('guest_name', '=', guest_name or '')]
        existing = self.sudo().search(domain, limit=1)
        if existing:
            if existing.time_out:
                existing.time_out = False
            return existing
        now = fields.Datetime.now()
        digest = hashlib.sha256(('%s|%s|%s|%s|%s' % (meeting.id, partner.id if partner else guest_name, now, ip or '',
                                                     self.env.registry.db_name)).encode()).hexdigest()[:16].upper()
        rec = self.sudo().create({'meeting_id': meeting.id, 'partner_id': partner.id if partner else False,
                                  'guest_name': False if partner else (guest_name or _('Invitado')),
                                  'time_in': now, 'source': source, 'ip': (ip or '')[:60] or False,
                                  'signature': digest})
        if partner and meeting.state == 'draft':
            meeting.with_context(scout_meeting_force=True).write(
                {'attendee_ids': [(4, partner.id)], 'absent_ids': [(3, partner.id)]})
        return rec

    def _scout_leave(self):
        now = fields.Datetime.now()
        for rec in self.filtered(lambda r: not r.time_out):
            rec.write({'time_out': now,
                       'minutes': rec.minutes + int((now - rec.time_in).total_seconds() // 60)})


class ScoutMeetingCheckins(models.Model):
    _inherit = 'scout.meeting'

    checkin_ids = fields.One2many('scout.meeting.checkin', 'meeting_id', string='Firmas de asistencia')

    def _scout_my_checkin(self):
        self.ensure_one()
        rec = self.sudo().checkin_ids.filtered(lambda c: c.partner_id == self.env.user.partner_id)[:1]
        return rec and {'time': self._scout_hhmm(rec.time_in), 'signature': rec.signature,
                        'source': dict(rec._fields['source'].selection).get(rec.source)}


class DiscussRtcSessionScout(models.Model):
    """Al entrar a la videollamada de un consejo se firma la asistencia; al salir se anota la hora."""
    _inherit = 'discuss.channel.rtc.session'

    @api.model_create_multi
    def create(self, vals_list):
        sessions = super().create(vals_list)
        try:
            Meeting = self.env['scout.meeting']
            ip = None
            try:
                from odoo.http import request
                ip = request.httprequest.remote_addr if request else None
            except Exception:  # noqa: BLE001
                ip = None
            for session in sessions:
                meeting = Meeting._scout_for_channel(session.channel_id.id)
                if not meeting:
                    continue
                self.env['scout.meeting.checkin']._scout_sign(
                    meeting, partner=session.partner_id or None,
                    guest_name=None if session.partner_id else (session.guest_id.name or _('Invitado')),
                    source='call', ip=ip)
        except Exception:  # noqa: BLE001 - la firma nunca debe impedir entrar a la llamada
            _logger.exception('No se pudo firmar la asistencia al entrar a la videollamada')
        return sessions

    def unlink(self):
        try:
            Checkin = self.env['scout.meeting.checkin'].sudo()
            for session in self:
                meeting = self.env['scout.meeting']._scout_for_channel(session.channel_id.id)
                if not meeting:
                    continue
                domain = [('meeting_id', '=', meeting.id)]
                domain += [('partner_id', '=', session.partner_id.id)] if session.partner_id else \
                    [('guest_name', '=', session.guest_id.name or '')]
                Checkin.search(domain)._scout_leave()
        except Exception:  # noqa: BLE001
            _logger.exception('No se pudo anotar la salida de la videollamada')
        return super().unlink()
