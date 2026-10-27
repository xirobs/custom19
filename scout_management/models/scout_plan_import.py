# -*- coding: utf-8 -*-
"""Cargar el programa de la sesión desde un archivo (Excel, PDF o imagen).

* Excel / CSV: se leen las columnas del formato del grupo (HORA, ETAPAS, TIPO, TIEMPO, ACTIVIDAD, OBJETIVO,
  MATERIAL, RESPONSABLE, OBSERVACIONES y las 8 casillas de programa y método) o cualquier tabla con
  encabezados parecidos. No necesita inteligencia artificial.
* PDF: si hay IA configurada, la IA lee el documento; si no, se extrae su texto y lo interpreta el mismo
  intérprete del dictado.
* Imagen (foto o imagen hecha con IA): la lee la IA configurada en la empresa (Gemini, ChatGPT o Claude).

El resultado NO se guarda solo: llega al editor para que el jefe lo revise y toque "Guardar programa".
"""
import base64
import csv
import datetime
import io
import json
import logging
import re
import shutil
import subprocess
import tempfile

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import scout_pdf_layout as layout
from . import scout_plan_parser as parser

_logger = logging.getLogger(__name__)

MAX_FILE_MB = 15
AI_TIMEOUT = 90
AI_PROVIDERS = [('gemini', 'Google Gemini'), ('openai', 'OpenAI (ChatGPT)'), ('anthropic', 'Anthropic (Claude)')]
AI_DEFAULT_MODELS = {'gemini': 'gemini-2.5-flash', 'openai': 'gpt-4o-mini', 'anthropic': 'claude-sonnet-5-5'}
IMAGE_TYPES = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'webp': 'image/webp',
               'gif': 'image/gif', 'heic': 'image/heic', 'heif': 'image/heif'}

# Columnas reconocidas (texto del encabezado sin acentos). Las casillas van primero porque
# "ACTIVIDAD AL AIRE LIBRE" también contiene la palabra "actividad".
FLAG_HEADERS = [
    ('outdoor', ('aire libre',)), ('symbolic', ('simbolic',)), ('progress', ('adelanto', 'progresion')),
    ('promise', ('compromiso', 'promesa')), ('groups', ('grupos naturales', 'pequenos grupos', 'grupos peq')),
    ('doing', ('aprender haciendo',)), ('adult', ('adulto', 'estimulante')), ('structure', ('estructura',)),
]
FIELD_HEADERS = [
    ('end', ('hora fin', 'hora de fin', 'termino', 'hora final', 'fin')),
    ('start', ('hora inicio', 'hora de inicio', 'horario', 'hora', 'inicio')),
    ('stage', ('etapa',)), ('kind', ('tipo',)), ('duration', ('tiempo', 'duracion', 'minutos', 'min')),
    ('objective', ('objetivo',)), ('material', ('material',)), ('responsible', ('responsable', 'encargado', 'quien')),
    ('description', ('observacion', 'descripcion', 'desarrollo', 'detalle', 'notas')),
    ('name', ('actividad', 'nombre', 'dinamica', 'juego')),
]
STAGE_WORDS = [('opening', ('apertura', 'inicio', 'entrada', 'formacion')), ('first', ('primera', '1')),
               ('second', ('segunda', '2')), ('closing', ('cierre', 'final', 'despedida'))]

AI_PROMPT = """Eres asistente de un grupo scout de México. El archivo adjunto es el programa de UNA junta scout \
(sesión) del día {day}, que empieza a las {start} y termina a las {end}. Lee TODAS las actividades en orden y \
devuelve SOLO un JSON con esta forma, sin texto adicional:
{{"activities": [{{"start": "HH:MM", "end": "HH:MM", "minutes": 15, "stage": "opening|first|second|closing", \
"kind": "Ceremonia|Desfogue|Calentamiento|Mediano|Técnica|Reflexión|Marco simbólico|Avisos", "name": "...", \
"objective": "...", "material": "...", "responsible": "...", "description": "...", \
"flags": {{"structure": false, "symbolic": false, "progress": false, "promise": false, "groups": false, \
"doing": false, "outdoor": false, "adult": false}}}}]}}
Reglas:
- Horas en formato de 24 horas ("3:45 pm" es "15:45"). Si una hora no aparece deja "" (no la inventes) y pon \
"minutes" si el archivo dice cuánto dura.
- "name" es el nombre corto de la actividad; lo demás (desarrollo, reglas, notas) va en "description".
- stage: opening = honores, formación, bienvenida; closing = reflexión final, avisos, despedida; el resto first \
o second según vaya en la primera o segunda mitad.
- flags: márcalos en true solo si el archivo tiene la casilla marcada (X, ✓) o si la actividad claramente lo \
cumple (outdoor = al aire libre, groups = por patrullas/seisenas/equipos, doing = actividad práctica, \
symbolic = historia, ambientación, gritos o canciones de la sección, progress = adelantos o especialidades, \
promise = promesa o ley, adult = acompañamiento del dirigente, structure = formación o ceremonias).
- Si el archivo trae varios días, usa solo el del {day}; si no se distingue, el primero.
- Escribe en español y respeta los nombres tal como vienen.{extra}"""


def _norm(value):
    return parser.norm(str(value or '')).strip()


def _cell_text(value):
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _times_in(value, ref_start=None):
    """Lista de horas (minutos del día) en una celda: time, datetime, fracción de día o texto '3:45 pm a 4'."""
    if value is None or value == '':
        return []
    if isinstance(value, datetime.datetime):
        return [value.hour * 60 + value.minute]
    if isinstance(value, datetime.time):
        return [value.hour * 60 + value.minute]
    if isinstance(value, (int, float)) and 0 < value < 1:
        minutes = int(round(value * 24 * 60))
        return [minutes]
    text = _norm(value).replace('.', ':')
    pm = bool(re.search(r'\bp\s*:?\s*m\b|pm\b|tarde', text))
    found = []
    for match in re.finditer(r'(?<!\d)(\d{1,2})(?::(\d{2}))?(?!\d)', text):
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        if not match.group(2) and not re.search(r'\b(?:a|hasta|-|–)\s*$', text[:match.start()]) and found:
            continue
        if hour > 23 or minute > 59:
            continue
        if hour < 12 and (pm or hour < 8 or (ref_start and ref_start >= 12 * 60
                                             and abs((hour + 12) * 60 - ref_start) < abs(hour * 60 - ref_start))):
            hour += 12
        found.append(hour * 60 + minute)
    return found


def _minutes_in(value):
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) if 0 < value <= 300 else None
    text = _norm(value)
    hours = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:h|hr|hora)', text)
    mins = re.search(r'(\d+)\s*(?:m|min)', text)
    total = 0
    if hours:
        total += int(float(hours.group(1).replace(',', '.')) * 60)
    if mins:
        total += int(mins.group(1))
    if not total:
        bare = re.fullmatch(r'\s*(\d{1,3})\s*', text)
        total = int(bare.group(1)) if bare else 0
    return total or None


def _stage(value):
    text = _norm(value)
    for key, words in STAGE_WORDS:
        if any(w == text or (len(w) > 2 and w in text) for w in words):
            return key
    return ''


def _checked(value):
    raw = str(value if value is not None else '').strip()
    text = _norm(value)
    if not raw:
        return False
    if any(mark in raw for mark in ('✔', '✓', '☑', '✅', '●', '•')):
        return True
    return bool(text) and text not in ('no', '0', '-', 'n/a', 'na', 'false', 'falso')


def _header_map(row):
    """{campo: índice de columna} si la fila parece encabezado del programa."""
    mapping = {}
    for idx, value in enumerate(row):
        text = _norm(value)
        if not text or len(text) > 60:
            continue
        key = next((f'flag:{flag}' for flag, words in FLAG_HEADERS if any(w in text for w in words)), None)
        if not key:
            key = next((name for name, words in FIELD_HEADERS
                        if any(re.search(r'(?<![a-z])' + re.escape(w), text) for w in words)), None)
        if key and key not in mapping:
            mapping[key] = idx
    return mapping if 'name' in mapping and len(mapping) >= 2 else None


class CalendarEventPlanImport(models.Model):
    _inherit = 'calendar.event'

    # ------------------------------------------------------------------ entrada
    def scout_plan_from_file(self, filename, content):
        """{'lines', 'messages', 'warnings'} a partir del archivo subido (no guarda nada)."""
        self.ensure_one()
        if not content:
            raise UserError(_('El archivo está vacío.'))
        if len(content) > MAX_FILE_MB * 1024 * 1024:
            raise UserError(_('El archivo pesa más de %s MB. Súbelo más ligero (por ejemplo una foto normal, '
                              'no de máxima calidad).', MAX_FILE_MB))
        ext = (filename or '').rsplit('.', 1)[-1].lower() if '.' in (filename or '') else ''
        if content[:4] == b'%PDF':
            ext = 'pdf'
        elif content[:2] == b'PK' and ext not in ('xlsx', 'xlsm'):
            ext = 'xlsx'
        if ext in ('xlsx', 'xlsm', 'csv'):
            result = self._scout_plan_from_sheet(content, ext)
        elif ext == 'pdf':
            result = self._scout_plan_from_pdf(content)
        elif ext in IMAGE_TYPES:
            result = self._scout_plan_from_image(content, IMAGE_TYPES[ext])
        elif ext == 'xls':
            raise UserError(_('Ese Excel es del formato antiguo (.xls). Ábrelo y guárdalo como .xlsx para subirlo.'))
        else:
            raise UserError(_('Sube el programa en Excel (.xlsx), PDF o imagen (JPG, PNG).'))
        if not result['lines']:
            raise UserError(_('No encontré actividades en el archivo. Revisa que sea el programa de la junta.'))
        return result

    # ------------------------------------------------------------------ Excel / CSV
    def _scout_plan_sheet_rows(self, content, ext):
        """[(nombre de hoja, filas)]"""
        if ext == 'csv':
            text = content.decode('utf-8-sig', errors='replace')
            dialect = csv.Sniffer().sniff(text[:2048], delimiters=',;\t') if text.strip() else csv.excel
            return [('CSV', list(csv.reader(io.StringIO(text), dialect)))]
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        except Exception as e:  # noqa: BLE001
            raise UserError(_('No pude abrir el Excel: %s', e)) from e
        return [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]

    def _scout_plan_pick_sheet(self, sheets):
        """La hoja de la fecha de esta junta (si el libro trae varias); si no, la primera con encabezados."""
        day = self.scout_date or (self.start and self.start.date())
        keys = []
        if day:
            keys = [day.strftime(f) for f in ('%d-%m-%Y', '%d/%m/%Y', '%d-%m-%y', '%d/%m/%y', '%d-%m', '%d/%m',
                                             '%Y-%m-%d')] + [str(day.day)]
        with_header = [(name, rows) for name, rows in sheets
                       if any(_header_map(r) for r in rows[:30])]
        for key in keys:
            for name, rows in with_header:
                if key in name:
                    return name, rows
        return (with_header or sheets)[0]

    def _scout_plan_from_sheet(self, content, ext):
        ref, location, leaders = self._scout_plan_context()
        sheets = self._scout_plan_sheet_rows(content, ext)
        if not sheets:
            raise UserError(_('El Excel no tiene hojas.'))
        sheet_name, rows = self._scout_plan_pick_sheet(sheets)
        return self._scout_plan_from_rows(rows, _('Excel (hoja "%s")', sheet_name), _('el Excel'))

    def _scout_plan_from_rows(self, rows, source, what):
        """Filas de una tabla (Excel o tabla de un PDF) → actividades."""
        ref, location, leaders = self._scout_plan_context()
        header_idx, mapping = None, None
        for idx, row in enumerate(rows[:40]):
            mapping = _header_map(row)
            if mapping:
                header_idx = idx
                break
        if mapping is None:
            # Tabla sin encabezados reconocibles: se interpreta como texto (o con IA si está configurada).
            text = '\n'.join(' | '.join(_cell_text(c) for c in row if _cell_text(c)) for row in rows)
            if self._scout_ai_ready():
                return self._scout_plan_ai(text=text, source=source)
            return self._scout_plan_from_text(text, what)
        lines = []
        for row in rows[header_idx + 1:]:
            get = lambda key: row[mapping[key]] if key in mapping and mapping[key] < len(row) else None  # noqa: E731
            name = re.sub(r'\s*Por confirmar:.*$', '', _cell_text(get('name'))).strip()
            if not name:
                continue
            if _norm(name) in ('actividad', 'actividades', 'total'):
                continue
            times = _times_in(get('start'), ref)
            end_times = _times_in(get('end'), ref)
            start = times[0] if times else None
            end = end_times[0] if end_times else (times[1] if len(times) > 1 else None)
            line = parser.new_line(
                start=parser.fmt(start), end=parser.fmt(end), stage=_stage(get('stage')),
                kind=_cell_text(get('kind')).capitalize(), name=name[:200],
                objective=_cell_text(get('objective')), material=_cell_text(get('material')),
                responsible=_cell_text(get('responsible')), description=_cell_text(get('description')))
            line['duration'] = _minutes_in(get('duration'))
            line['connector'] = start is None
            if any(k.startswith('flag:') for k in mapping):
                line['flags'] = {f: _checked(get(f'flag:{f}')) for f in parser.FLAGS}
            else:
                parser._reclassify(line, location, leaders)
            lines.append(line)
        return self._scout_plan_finish(lines, source)

    # ------------------------------------------------------------------ PDF e imagen
    def _scout_plan_from_pdf(self, content):
        # 1) Tabla del programa (formato del grupo u otra con encabezados): se lee por posición, sin IA.
        items = layout.pdf_items(content)
        rows = layout.table_rows(items, lambda row: len(_header_map(row) or {}) >= 3)
        if len(rows) > 1:
            try:
                result = self._scout_plan_from_rows(rows, _('el PDF'), _('el PDF'))
                if result['lines']:
                    return result
            except UserError:
                pass
        if layout.calendar_events(items)[0]:
            raise UserError(_('Este PDF es un calendario de actividades, no el programa de una junta. Súbelo en '
                              'Planificación → Calendario del grupo → "Subir calendario".'))
        if self._scout_ai_ready():
            return self._scout_plan_ai(content=content, mimetype='application/pdf', source=_('PDF'))
        text = self._scout_pdf_text(content)
        if len(text.strip()) < 15:
            raise UserError(_('Este PDF es una imagen escaneada. Para leerlo hay que configurar la inteligencia '
                              'artificial (lo hace la jefatura en Ajustes de la empresa → Inteligencia artificial).'))
        return self._scout_plan_from_text(text, _('el PDF'))

    def _scout_plan_from_image(self, content, mimetype):
        if self._scout_ai_ready():
            return self._scout_plan_ai(content=content, mimetype=mimetype, source=_('la imagen'))
        text = self._scout_ocr(content)
        if len(text.strip()) < 15:
            raise UserError(_('Para leer imágenes hay que configurar la inteligencia artificial (lo hace la jefatura '
                              'en Ajustes de la empresa → Inteligencia artificial). Mientras, sube el programa en '
                              'Excel o PDF.'))
        return self._scout_plan_from_text(text, _('la imagen'))

    @api.model
    def _scout_pdf_text(self, content):
        try:
            try:
                from pypdf import PdfReader
            except ImportError:
                from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(content))
            return '\n'.join((page.extract_text() or '') for page in reader.pages[:10])
        except Exception:  # noqa: BLE001
            _logger.info('No se pudo extraer texto del PDF', exc_info=True)
            return ''

    @api.model
    def _scout_ocr(self, content):
        """Lectura básica sin IA si el servidor tiene tesseract instalado."""
        binary = shutil.which('tesseract')
        if not binary:
            return ''
        with tempfile.NamedTemporaryFile(suffix='.img') as tmp:
            tmp.write(content)
            tmp.flush()
            try:
                out = subprocess.run([binary, tmp.name, 'stdout', '-l', 'spa+eng'], capture_output=True, timeout=60,
                                     check=False)
                return out.stdout.decode('utf-8', errors='replace')
            except Exception:  # noqa: BLE001
                return ''

    def _scout_plan_from_text(self, text, what):
        ref, location, leaders = self._scout_plan_context()
        # una línea por renglón para que el intérprete separe las actividades
        clean = '\n'.join(l.strip() for l in text.splitlines() if l.strip())
        clean = re.sub(r'(?<![.;:])\n', '. ', clean)
        result = parser.interpret(clean, [], ref_start=ref, location=location, leaders=leaders)
        result['lines'] = [l for l in result['lines'] if len(re.sub(r'[^a-zA-ZáéíóúñÁÉÍÓÚÑ]', '', l['name'])) >= 4]
        if not result['lines'] or (not self._scout_ai_ready() and len(result['lines']) < 2):
            raise UserError(_('No pude distinguir las actividades en %s. Súbelo en Excel, o pide a la jefatura que '
                              'active la inteligencia artificial para leer PDF e imágenes.', what))
        result['messages'] = [_('Leí %(n)s actividad(es) de %(what)s. Revisa nombres y horarios y toca '
                                '"Guardar programa".', n=len(result['lines']), what=what)]
        if not self._scout_ai_ready():
            result['warnings'] = (result.get('warnings') or []) + [
                _('Leído sin inteligencia artificial: puede que haya que acomodar algunas actividades.')]
        return result

    def _scout_plan_finish(self, lines, source):
        ref, _location, _leaders = self._scout_plan_context()
        for line in lines:
            low = parser.norm(line['name'] + ' ' + (line.get('description') or ''))
            if not line.get('stage'):
                if any(parser._has(low, w) for w in parser.OPENING_WORDS):
                    line['stage'] = 'opening'
                elif any(parser._has(low, w) for w in parser.CLOSING_WORDS):
                    line['stage'] = 'closing'
            if not line.get('kind'):
                line['kind'] = parser._kind(low)
        if lines and not lines[0]['start'] and ref is not None:
            lines[0]['start'] = parser.fmt(ref)
        if lines and lines[-1]['start'] and not lines[-1]['end'] and not lines[-1].get('duration'):
            _begin, end = self._scout_plan_window()
            if end > parser.parse_hhmm(lines[-1]['start']):
                lines[-1]['end'] = parser.fmt(end)
        lines, warnings = parser.finish(lines, ref)
        return {'lines': lines, 'warnings': warnings,
                'messages': [_('Leí %(n)s actividad(es) de %(src)s. Revisa y toca "Guardar programa".',
                               n=len(lines), src=source)]}

    # ------------------------------------------------------------------ inteligencia artificial
    def _scout_ai_config(self):
        company = self.env.company.sudo()
        key = (company.scout_ai_api_key or '').strip()
        provider = company.scout_ai_provider or 'gemini'
        return provider, key, (company.scout_ai_model or '').strip() or AI_DEFAULT_MODELS[provider]

    def _scout_ai_ready(self):
        return bool(self._scout_ai_config()[1])

    def _scout_plan_ai(self, content=None, mimetype=None, text=None, source=''):
        self.ensure_one()
        provider, key, model = self._scout_ai_config()
        begin, end = self._scout_plan_window()
        day = self.scout_date or (self.start and self.start.date())
        extra = ('\n\nContenido del archivo:\n' + text[:20000]) if text else ''
        prompt = AI_PROMPT.format(day=day.strftime('%d/%m/%Y') if day else 'de la junta', start=parser.fmt(begin),
                                  end=parser.fmt(end), extra=extra)
        try:
            raw = getattr(self, f'_scout_ai_{provider}')(key, model, prompt, content, mimetype)
        except UserError:
            raise
        except requests.exceptions.Timeout as e:
            raise UserError(_('La inteligencia artificial tardó demasiado. Intenta de nuevo.')) from e
        except Exception as e:  # noqa: BLE001
            _logger.exception('Error con la IA (%s)', provider)
            raise UserError(_('No se pudo leer el archivo con la inteligencia artificial: %s', e)) from e
        activities = self._scout_ai_parse(raw)
        ref, location, leaders = self._scout_plan_context()
        lines = []
        for item in activities:
            if not isinstance(item, dict) or not str(item.get('name') or '').strip():
                continue
            start = parser.parse_hhmm(item.get('start'))
            end = parser.parse_hhmm(item.get('end'))
            stage = item.get('stage') if item.get('stage') in dict(parser.STAGES) else ''
            line = parser.new_line(
                start=parser.fmt(start), end=parser.fmt(end), stage=stage, kind=str(item.get('kind') or '')[:60],
                name=str(item['name']).strip()[:200], objective=str(item.get('objective') or ''),
                material=str(item.get('material') or ''), responsible=str(item.get('responsible') or ''),
                description=str(item.get('description') or ''))
            flags = item.get('flags') if isinstance(item.get('flags'), dict) else None
            if flags:
                line['flags'] = {f: bool(flags.get(f)) for f in parser.FLAGS}
            else:
                parser._reclassify(line, location, leaders)
            try:
                line['duration'] = int(item.get('minutes') or 0) or None
            except (TypeError, ValueError):
                line['duration'] = None
            line['connector'] = start is None
            lines.append(line)
        result = self._scout_plan_finish(lines, _('%s (leído con IA)', source))
        return result

    @api.model
    def _scout_ai_parse(self, raw):
        text = (raw or '').strip()
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
        match = re.search(r'\{.*\}|\[.*\]', text, re.S)
        try:
            data = json.loads(match.group(0) if match else text)
        except (ValueError, AttributeError) as e:
            raise UserError(_('La inteligencia artificial no devolvió un programa legible. Intenta de nuevo.')) from e
        if isinstance(data, dict):
            data = data.get('activities') or data.get('actividades') or []
        return data if isinstance(data, list) else []

    @api.model
    def _scout_ai_check(self, response):
        if response.status_code in (401, 403):
            raise UserError(_('La clave de la inteligencia artificial no es válida o no tiene permiso. Revísala en '
                              'Ajustes de la empresa.'))
        if response.status_code == 429:
            raise UserError(_('Se alcanzó el límite de uso de la inteligencia artificial. Intenta más tarde.'))
        if response.status_code >= 400:
            raise UserError(_('La inteligencia artificial respondió con error %(code)s: %(msg)s',
                              code=response.status_code, msg=response.text[:300]))
        return response.json()

    @api.model
    def _scout_ai_gemini(self, key, model, prompt, content, mimetype):
        parts = [{'text': prompt}]
        if content:
            parts.append({'inline_data': {'mime_type': mimetype, 'data': base64.b64encode(content).decode()}})
        response = requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
            headers={'x-goog-api-key': key, 'Content-Type': 'application/json'}, timeout=AI_TIMEOUT,
            json={'contents': [{'role': 'user', 'parts': parts}],
                  'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0.1}})
        data = self._scout_ai_check(response)
        candidates = data.get('candidates') or []
        return ''.join(p.get('text', '') for p in (candidates[0].get('content', {}).get('parts') or [])) \
            if candidates else ''

    @api.model
    def _scout_ai_openai(self, key, model, prompt, content, mimetype):
        parts = [{'type': 'text', 'text': prompt}]
        if content:
            data_url = 'data:%s;base64,%s' % (mimetype, base64.b64encode(content).decode())
            if mimetype == 'application/pdf':
                parts.append({'type': 'file', 'file': {'filename': 'programa.pdf', 'file_data': data_url}})
            else:
                parts.append({'type': 'image_url', 'image_url': {'url': data_url}})
        response = requests.post(
            'https://api.openai.com/v1/chat/completions', timeout=AI_TIMEOUT,
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            json={'model': model, 'temperature': 0.1, 'response_format': {'type': 'json_object'},
                  'messages': [{'role': 'user', 'content': parts}]})
        data = self._scout_ai_check(response)
        return ((data.get('choices') or [{}])[0].get('message') or {}).get('content') or ''

    @api.model
    def _scout_ai_anthropic(self, key, model, prompt, content, mimetype):
        parts = []
        if content:
            encoded = base64.b64encode(content).decode()
            kind = 'document' if mimetype == 'application/pdf' else 'image'
            parts.append({'type': kind, 'source': {'type': 'base64', 'media_type': mimetype, 'data': encoded}})
        parts.append({'type': 'text', 'text': prompt})
        response = requests.post(
            'https://api.anthropic.com/v1/messages', timeout=AI_TIMEOUT,
            headers={'x-api-key': key, 'anthropic-version': '2023-06-01', 'Content-Type': 'application/json'},
            json={'model': model, 'max_tokens': 4096, 'messages': [{'role': 'user', 'content': parts}]})
        data = self._scout_ai_check(response)
        return ''.join(b.get('text', '') for b in data.get('content') or [] if b.get('type') == 'text')


class ResCompanyScoutAI(models.Model):
    _inherit = 'res.company'

    scout_ai_provider = fields.Selection(AI_PROVIDERS, string='Proveedor de IA', default='gemini',
                                         help='Se usa para leer programas en PDF o imagen.')
    scout_ai_api_key = fields.Char(string='Clave (API key) de la IA', groups='base.group_system',
                                   help='Gemini: aistudio.google.com/apikey · ChatGPT: platform.openai.com/api-keys · '
                                        'Claude: console.anthropic.com')
    scout_ai_model = fields.Char(string='Modelo de IA',
                                 help='Vacío = el recomendado (gemini-2.5-flash, gpt-4o-mini o claude-sonnet-5-5).')
    scout_ai_ready = fields.Boolean(string='IA configurada', compute='_compute_scout_ai_ready')

    def _compute_scout_ai_ready(self):
        for company in self:
            company.scout_ai_ready = bool(company.sudo().scout_ai_api_key)

    def action_scout_ai_test(self):
        """Prueba la clave con una pregunta mínima."""
        self.ensure_one()
        company = self.sudo()
        provider = company.scout_ai_provider or 'gemini'
        key = (company.scout_ai_api_key or '').strip()
        if not key:
            raise UserError(_('Primero escribe la clave de la IA.'))
        model = (company.scout_ai_model or '').strip() or AI_DEFAULT_MODELS[provider]
        raw = getattr(self.env['calendar.event'], f'_scout_ai_{provider}')(
            key, model, 'Responde solo este JSON: {"activities": [{"name": "Prueba"}]}', None, None)
        ok = 'Prueba' in (raw or '')
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Inteligencia artificial'), 'type': 'success' if ok else 'warning',
                           'message': _('Conexión correcta con %s.', dict(AI_PROVIDERS)[provider]) if ok
                           else _('Respondió, pero no como se esperaba: %s', (raw or '')[:200])}}
