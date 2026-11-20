# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac
import io
import logging
import re
import time

_logger = logging.getLogger(__name__)

try:
    import qrcode
except ImportError:  # pragma: no cover
    qrcode = None
    _logger.warning("La librería python 'qrcode' no está instalada; los QR no se generarán.")

# El QR dinámico cambia cada minuto.
QR_WINDOW_SECONDS = 60


def generate_qr_base64(value):
    """Devuelve un PNG en base64 con el QR del valor indicado."""
    if not value or not qrcode:
        return False
    qr = qrcode.QRCode(box_size=10, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(value)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    return base64.b64encode(buffer.getvalue())


def normalize_phone(phone):
    """Últimos 10 dígitos del teléfono (formato MX)."""
    digits = re.sub(r'\D', '', phone or '')
    return digits[-10:]


def current_window():
    return int(time.time() // QR_WINDOW_SECONDS)


def rotating_code(secret, window):
    return hmac.new(secret.encode(), str(window).encode(), hashlib.sha256).hexdigest()[:12]


def check_rotating_code(secret, code, max_windows):
    """True si el código corresponde a la ventana actual o a alguna de las `max_windows` anteriores."""
    if not secret or not code:
        return False
    now = current_window()
    return any(hmac.compare_digest(str(code), rotating_code(secret, now - i)) for i in range(max_windows + 1))
