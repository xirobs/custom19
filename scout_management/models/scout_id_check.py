# -*- coding: utf-8 -*-
"""Identificación del padre, madre o tutor: solo INE, FM3 / tarjeta de residente o pasaporte.

La foto se revisa al tomarla (y otra vez al enviar la ficha):
1. Con la IA configurada en Datos del grupo: reconoce el tipo de documento y si se lee.
2. Si no hay IA pero el servidor tiene OCR (tesseract): busca los textos propios de cada documento.
3. Sin ninguno de los dos solo se revisa que sea una imagen nítida de tamaño razonable y la ficha queda marcada
   para que la jefatura la revise a mano.
"""
import base64
import io
import json
import logging
import re
import shutil
import subprocess
import tempfile
import unicodedata

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

ID_TYPES = [('ine', 'INE'), ('fm3', 'FM3 / Tarjeta de residente'), ('passport', 'Pasaporte')]
ID_LABELS = dict(ID_TYPES)
ID_KEYWORDS = {
    'ine': ('instituto nacional electoral', 'credencial para votar', 'clave de elector', 'idmex', 'curp',
            'instituto federal electoral'),
    'fm3': ('residente', 'instituto nacional de migracion', 'inm', 'fm3', 'fm 3', 'migratorio', 'tarjeta de residente'),
    'passport': ('pasaporte', 'passport', 'p<mex', 'p<', 'secretaria de relaciones exteriores', 'nationality'),
}
ID_PROMPT = """Revisa la imagen. Debe ser una identificación oficial del padre, madre o tutor de un scout en México:
INE (credencial para votar), FM3 / tarjeta de residente del INM, o pasaporte (de cualquier país).
Responde SOLO este JSON: {"tipo": "ine|fm3|pasaporte|otro", "lado": "frente|reverso|datos|otro",
"legible": true, "motivo": "explicación corta en español"}
- "otro" si es cualquier otra cosa (selfie, paisaje, otra credencial, licencia, recibo, captura de pantalla, etc.).
- "legible": false si está borrosa, cortada o muy oscura para leer los datos."""


def _plain(text):
    text = unicodedata.normalize('NFKD', text or '')
    return ''.join(c for c in text if not unicodedata.combining(c)).lower()


class ScoutRegistrationId(models.Model):
    _inherit = 'scout.registration'

    guardian_id_type = fields.Selection(ID_TYPES, string='Identificación del tutor', default='ine')
    guardian_id_check = fields.Char(string='Revisión de la identificación', copy=False,
                                    help='Resultado de la revisión automática de la foto de la identificación.')

    @api.model
    def _scout_decode_upload(self, data):
        """(bytes, mimetype) de un data URL o de bytes."""
        if isinstance(data, str) and data.startswith('data:'):
            head, _sep, body = data.partition(',')
            mimetype = head[5:].split(';')[0] or 'application/octet-stream'
            try:
                return base64.b64decode(body), mimetype
            except (ValueError, TypeError):
                return b'', mimetype
        content = data or b''
        mimetype = 'application/pdf' if content[:4] == b'%PDF' else 'image/jpeg'
        return content, mimetype

    @api.model
    def _scout_check_id_document(self, content, mimetype, doc_type='ine', side='front'):
        """{'level': 'ok'|'warn'|'error', 'message': str, 'detected': tipo} de la foto de la identificación."""
        doc_type = doc_type if doc_type in ID_LABELS else 'ine'
        label = ID_LABELS[doc_type]
        if not content:
            return {'level': 'error', 'message': _('No llegó la foto. Vuelve a tomarla.'), 'detected': ''}
        if len(content) > 15 * 1024 * 1024:
            return {'level': 'error', 'message': _('La foto pesa más de 15 MB.'), 'detected': ''}
        image_bytes = content
        if mimetype != 'application/pdf':
            try:
                from PIL import Image, ImageOps
                img = ImageOps.exif_transpose(Image.open(io.BytesIO(content)))
                img.load()
            except Exception:  # noqa: BLE001
                return {'level': 'error', 'message': _('El archivo no es una foto válida. Toma una foto de la %s.', label),
                        'detected': ''}
            w, h = img.size
            if min(w, h) < 280 or max(w, h) < 420:
                return {'level': 'error', 'detected': '',
                        'message': _('La foto es muy pequeña para leer la %s. Tómala de cerca y con buena luz.', label)}
            # Imagen reducida para la revisión (más rápida y barata)
            img = img.convert('RGB')
            img.thumbnail((1600, 1600))
            out = io.BytesIO()
            img.save(out, 'JPEG', quality=85)
            image_bytes, mimetype = out.getvalue(), 'image/jpeg'
        Event = self.env['calendar.event']
        if Event._scout_ai_ready():
            return self._scout_check_id_ai(image_bytes, mimetype, doc_type, side)
        text = self._scout_id_text(image_bytes, mimetype)
        if text is not None:
            return self._scout_check_id_text(text, doc_type)
        return {'level': 'warn', 'detected': '',
                'message': _('Foto recibida. No se pudo verificar automáticamente que sea %s; la jefatura la '
                             'revisará.', label)}

    @api.model
    def _scout_check_id_ai(self, content, mimetype, doc_type, side):
        label = ID_LABELS[doc_type]
        Event = self.env['calendar.event']
        provider, key, model = Event._scout_ai_config()
        try:
            raw = getattr(Event, f'_scout_ai_{provider}')(key, model, ID_PROMPT, content, mimetype)
            match = re.search(r'\{.*\}', re.sub(r'^```(?:json)?\s*|\s*```$', '', (raw or '').strip()), re.S)
            data = json.loads(match.group(0)) if match else {}
        except Exception:  # noqa: BLE001 - si la IA falla no se detiene la inscripción
            _logger.info('No se pudo revisar la identificación con IA', exc_info=True)
            return {'level': 'warn', 'detected': '',
                    'message': _('Foto recibida. No se pudo verificar en este momento; la jefatura la revisará.')}
        kind = str(data.get('tipo') or '').lower()
        kind = {'pasaporte': 'passport', 'residente': 'fm3'}.get(kind, kind)
        reason = str(data.get('motivo') or '').strip()
        if kind not in ID_LABELS:
            return {'level': 'error', 'detected': 'otro',
                    'message': _('Esta foto no parece una INE, FM3 o pasaporte%s. Toma la foto de la identificación '
                                 'del tutor.', (' (%s)' % reason) if reason else '')}
        if data.get('legible') is False:
            return {'level': 'error', 'detected': kind,
                    'message': _('La %s no se alcanza a leer (borrosa, oscura o cortada). Tómala otra vez de cerca y '
                                 'con buena luz.', ID_LABELS[kind])}
        if kind != doc_type:
            return {'level': 'error', 'detected': kind,
                    'message': _('Elegiste %(chosen)s pero la foto parece %(found)s. Cambia el tipo de identificación '
                                 'o toma la foto correcta.', chosen=label, found=ID_LABELS[kind])}
        side_found = str(data.get('lado') or '').lower()
        note = ''
        if doc_type == 'ine' and side == 'back' and side_found == 'frente':
            note = _(' Ojo: parece el frente; aquí va el reverso.')
        elif doc_type == 'ine' and side == 'front' and side_found == 'reverso':
            note = _(' Ojo: parece el reverso; aquí va el frente.')
        return {'level': 'warn' if note else 'ok', 'detected': kind,
                'message': _('Identificación verificada: %s.', label) + note}

    @api.model
    def _scout_id_text(self, content, mimetype):
        """Texto de la identificación sin IA: del PDF o con OCR si el servidor tiene tesseract."""
        if mimetype == 'application/pdf':
            from .scout_pdf_layout import pdf_items
            text = ' '.join(i['text'] for i in pdf_items(content, max_pages=2))
            return text if text.strip() else None  # PDF escaneado: sin texto que revisar
        binary = shutil.which('tesseract')
        if not binary:
            return None  # sin OCR en el servidor
        with tempfile.NamedTemporaryFile(suffix='.jpg') as tmp:
            tmp.write(content)
            tmp.flush()
            try:
                out = subprocess.run([binary, tmp.name, 'stdout', '-l', 'spa+eng'], capture_output=True, timeout=40,
                                     check=False)
                return out.stdout.decode('utf-8', errors='replace')
            except Exception:  # noqa: BLE001
                return ''

    @api.model
    def _scout_check_id_text(self, text, doc_type):
        low = _plain(text)
        found = [kind for kind, words in ID_KEYWORDS.items() if any(w in low for w in words)]
        label = ID_LABELS[doc_type]
        if doc_type in found:
            return {'level': 'ok', 'detected': doc_type, 'message': _('Identificación verificada: %s.', label)}
        if found:
            return {'level': 'error', 'detected': found[0],
                    'message': _('Elegiste %(chosen)s pero la foto parece %(found)s.', chosen=label,
                                 found=ID_LABELS[found[0]])}
        if len(low.strip()) < 25:
            return {'level': 'error', 'detected': '',
                    'message': _('No se ve ninguna identificación en la foto (o está muy borrosa). Toma la foto de la '
                                 '%s de frente, de cerca y con buena luz.', label)}
        return {'level': 'error', 'detected': 'otro',
                'message': _('Esta foto no parece una INE, FM3 o pasaporte. Toma la foto de la identificación del '
                             'tutor.')}

    @api.model
    def _scout_verify_id_uploads(self, doc_type, uploads):
        """Revisa las fotos al guardar la ficha. uploads: [((base64, nombre) | None, 'front'|'back')].
        Regresa (tipo, texto de la revisión) o lanza UserError si alguna no es la identificación pedida."""
        from odoo.exceptions import UserError
        doc_type = doc_type if doc_type in ID_LABELS else 'ine'
        notes = []
        for upload, side in uploads:
            if not upload:
                continue
            content = base64.b64decode(upload[0])
            mimetype = 'application/pdf' if content[:4] == b'%PDF' else 'image/jpeg'
            result = self._scout_check_id_document(content, mimetype, doc_type, side)
            if result['level'] == 'error':
                raise UserError(result['message'])
            notes.append(result['message'])
        return doc_type, ' '.join(notes)[:250] or False

