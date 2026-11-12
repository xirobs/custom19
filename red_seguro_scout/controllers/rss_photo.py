# -*- coding: utf-8 -*-
"""Foto del asegurado enviada desde el portal (cámara del celular o archivo)."""
import base64

from odoo import _
from odoo.http import request

MAX_PHOTO = 8 * 1024 * 1024
PHOTO_TYPES = ('image/jpeg', 'image/jpg', 'image/png', 'image/webp')


def photo_from_request(post):
    """Foto enviada desde el celular o la computadora: (base64 | None, error | None).
    El navegador la manda ya reducida a JPG (photo_data); si no hubo JavaScript, llega el archivo tal cual."""
    data_url = (post.get('photo_data') or '').strip()
    data = None
    if data_url.startswith('data:image/') and ';base64,' in data_url:
        try:
            data = base64.b64decode(data_url.split(';base64,', 1)[1], validate=True)
        except ValueError:
            data = None
    if data is None:
        for upload in request.httprequest.files.getlist('photo'):
            if upload and upload.filename:
                data = upload.read()
                if (upload.mimetype or '').lower() not in PHOTO_TYPES:
                    return None, _('Sube una foto JPG o PNG.')
                break
    if not data:
        return None, None
    if len(data) > MAX_PHOTO:
        return None, _('La foto pesa más de 8 MB.')
    try:
        from odoo.tools.image import image_process
        processed = image_process(data, size=(600, 600), verify_resolution=True)
    except Exception:  # noqa: BLE001 - imagen dañada o formato no soportado (p. ej. HEIC sin JavaScript)
        return None, _('No se pudo leer la foto; prueba con otra en JPG o PNG.')
    return base64.b64encode(processed or data), None
