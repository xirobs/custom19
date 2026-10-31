# -*- coding: utf-8 -*-
"""Credencial scout en PDF dibujada con medidas exactas (reportlab), sin depender de cómo cada servidor convierte
HTML a PDF: así no se descuadra, el logo sale con su calidad original y siempre lleva frente y reverso.

* Tamaño credencial (85.6 × 54 mm, como una tarjeta bancaria): una página de frente y otra de reverso por miembro,
  lista para imprenta o impresora de PVC.
* Hoja carta: 8 credenciales por hoja; frentes en una página y reversos en la siguiente, en espejo, para imprimir a
  doble cara volteando por el lado largo. Lleva marcas de corte.
"""
import base64
import io
import logging

from odoo import _, api, fields, models
from odoo.tools import file_open

_logger = logging.getLogger(__name__)

MM = 72 / 25.4
CARD_W, CARD_H = 85.6, 54.0
NAVY = (15 / 255, 48 / 255, 73 / 255)
ORANGE = (243 / 255, 111 / 255, 69 / 255)
GREEN = (46 / 255, 158 / 255, 68 / 255)
RED = (184 / 255, 32 / 255, 47 / 255)
INK = (29 / 255, 39 / 255, 51 / 255)
MUTED = (92 / 255, 107 / 255, 122 / 255)
NETWORK_DEFAULT = 'Red de Grupos Scouts Independientes de México'


def _pil(data):
    """Imagen PIL desde base64/bytes (None si no se puede: SVG, vacía, dañada)."""
    if not data:
        return None
    try:
        from PIL import Image, ImageOps
        if isinstance(data, str):
            data = data.encode()
        raw = data
        if data[:3] not in (b'\x89PN', b'\xff\xd8\xff', b'GIF', b'RIF', b'II*', b'MM\x00'):
            try:
                raw = base64.b64decode(data, validate=True)
            except (ValueError, TypeError):
                raw = data
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        img.load()
        return img.convert('RGBA')
    except Exception:  # noqa: BLE001
        return None


def _reader(img):
    from reportlab.lib.utils import ImageReader
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    buf.seek(0)
    return ImageReader(buf)


def _faded(img, alpha=0.11):
    """Misma imagen (mismos colores) con transparencia, para la marca de agua."""
    img = img.copy()
    a = img.getchannel('A').point(lambda v: int(v * alpha))
    img.putalpha(a)
    return img


def _cover(img, w, h):
    """Recorta al centro para llenar w×h sin deformar (foto del miembro)."""
    from PIL import ImageOps
    ratio = w / h
    px_h = min(img.height, 900)
    px_w = int(px_h * ratio)
    return ImageOps.fit(img, (max(1, px_w), max(1, px_h)), centering=(0.5, 0.35))


def clean_signature(raw):
    """Firma lista para la credencial: fondo transparente, tinta azul marino, sin márgenes.
    Sirve para una foto o escaneo de la firma en papel y para la firma dibujada en el celular."""
    from PIL import Image, ImageOps
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    if img.mode in ('RGBA', 'LA', 'P'):
        img = img.convert('RGBA')
        bg = Image.new('RGBA', img.size, (255, 255, 255, 255))
        bg.alpha_composite(img)
        img = bg
    gray = ImageOps.autocontrast(img.convert('L'), cutoff=1)
    # tinta = lo oscuro; papel (claro, aunque tenga sombra) = transparente
    alpha = gray.point(lambda v: 0 if v > 185 else min(255, int((185 - v) * 255 / 120)))
    box = alpha.point(lambda v: 255 if v > 40 else 0).getbbox()
    if not box:
        raise ValueError('No se encontró la firma en la imagen.')
    pad = int(max(alpha.size) * 0.02)
    box = (max(0, box[0] - pad), max(0, box[1] - pad), min(alpha.width, box[2] + pad), min(alpha.height, box[3] + pad))
    alpha = alpha.crop(box)
    # trazo un poco más grueso para que se lea al tamaño de la credencial (≈ 2.5 cm de ancho)
    from PIL import ImageFilter
    alpha = alpha.filter(ImageFilter.MaxFilter(max(3, (int(alpha.width / 400) | 1))))
    out = Image.new('RGBA', alpha.size, (20, 33, 61, 0))
    out.putalpha(alpha)
    if out.width > 1200:
        out = out.resize((1200, max(1, int(out.height * 1200 / out.width))), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, 'PNG', optimize=True)
    return buf.getvalue()


class ResCompanyScoutCard(models.Model):
    _inherit = 'res.company'

    scout_card_group_label = fields.Char(string='Nombre del grupo en la credencial',
                                         help='Ej. "Grupo Scout 7 Ethnos". Vacío = nombre de la empresa.')
    scout_card_emergency_phone = fields.Char(string='Teléfono de emergencias del grupo',
                                             help='Sale en el reverso de la credencial.')
    scout_card_chief_name = fields.Char(string='Jefe de grupo (firma de credenciales)',
                                        help='Vacío = la persona con el cargo de jefe de grupo.')
    scout_card_chief_signature = fields.Binary(string='Firma del jefe de grupo', attachment=True,
                                               help='Imagen PNG de la firma (fondo transparente o blanco). También '
                                                    'se puede dibujar desde el portal: Administración → Credenciales.')
    scout_card_network = fields.Char(string='Red / asociación', default=NETWORK_DEFAULT)
    scout_card_network_logo = fields.Binary(string='Logo de la red', attachment=True)

    def _scout_clean_signature_vals(self, vals):
        data = vals.get('scout_card_chief_signature')
        if data:
            raw = base64.b64decode(data) if isinstance(data, (str, bytes)) else data
            try:
                vals['scout_card_chief_signature'] = base64.b64encode(clean_signature(raw))
            except Exception:  # noqa: BLE001 - si no se puede limpiar se guarda como venga
                _logger.info('No se pudo limpiar la firma', exc_info=True)
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._scout_clean_signature_vals(dict(v)) for v in vals_list])

    def write(self, vals):
        return super().write(self._scout_clean_signature_vals(dict(vals)))

    def _scout_card_config_company(self):
        """Empresa de donde se toman la firma y los datos del reverso. Si el miembro está en otra empresa del
        mismo grupo (servidor multiempresa) que no tiene la firma cargada, se usa la que sí la tiene."""
        self.ensure_one()
        company = self.sudo()
        if company.scout_card_chief_signature:
            return company
        candidates = company.parent_ids | company.child_ids | self.env.user.company_ids | self.env.company
        if 'root_id' in company._fields and company.root_id:
            candidates |= self.env['res.company'].sudo().search([]).filtered(lambda c: c.root_id == company.root_id)
        for other in candidates.sudo():
            if other.scout_card_chief_signature:
                return other
        # Último recurso: cualquier empresa del servidor que tenga los datos de la credencial cargados
        everyone = self.env['res.company'].sudo().search([])
        for other in everyone:
            if other.scout_card_chief_signature:
                return other
        for other in everyone:
            if other.scout_card_emergency_phone or other.scout_card_chief_name:
                return other
        return company

    @api.model
    def _scout_card_companies(self):
        """Empresas que tienen miembros scout (para guardar en todas los datos de la credencial)."""
        data = self.env['res.partner'].sudo()._read_group([('is_scout', '=', True), ('company_id', '!=', False)],
                                                          ['company_id'], ['__count'])
        return self.env['res.company'].browse([c.id for c, _n in data]).sudo()

    def _scout_card_chief(self):
        self.ensure_one()
        if self.scout_card_chief_name:
            return self.scout_card_chief_name
        chief = self.env['res.partner'].sudo().search([
            ('is_scout', '=', True), ('scout_position_id.kind', '=', 'group_chief'),
            ('scout_state', 'in', ('trial', 'active')), ('company_id', 'in', [self.id, False])], limit=1)
        return chief.name or ''


class ScoutCardPdf(models.AbstractModel):
    _name = 'scout.card.pdf'
    _description = 'Credencial scout en PDF'

    # ------------------------------------------------------------------ armado del PDF
    @api.model
    def _render(self, members, layout='sheet'):
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas as rl_canvas
        members = members.sudo()
        buf = io.BytesIO()
        assets = {}
        if layout == 'card':
            c = rl_canvas.Canvas(buf, pagesize=(CARD_W * MM, CARD_H * MM))
            c.setTitle(_('Credencial scout'))
            for m in members:
                self._front(c, m, 0, 0, assets)
                c.showPage()
                self._back(c, m, 0, 0, assets)
                c.showPage()
        else:
            c = rl_canvas.Canvas(buf, pagesize=letter)
            c.setTitle(_('Credenciales scout'))
            page_w, page_h = letter
            gap = 6
            total_w = CARD_W * 2 + gap
            x0 = (page_w / MM - total_w) / 2
            y_top = page_h / MM - 18
            slots = [(x0 + col * (CARD_W + gap), y_top - (row + 1) * CARD_H - row * gap)
                     for row in range(4) for col in range(2)]
            for start in range(0, len(members), 8):
                chunk = members[start:start + 8]
                self._label(c, page_w, page_h, _('Frente · imprime a doble cara volteando por el lado largo, en '
                                                 'tamaño real (100 %, sin "ajustar a la página")'))
                for k, m in enumerate(chunk):
                    x, y = slots[k]
                    self._crop_marks(c, x, y)
                    self._front(c, m, x, y, assets)
                c.showPage()
                self._label(c, page_w, page_h, _('Reverso'))
                for k, m in enumerate(chunk):
                    x, y = slots[k ^ 1]  # espejo: izquierda ↔ derecha
                    self._crop_marks(c, x, y)
                    self._back(c, m, x, y, assets)
                c.showPage()
        c.save()
        return buf.getvalue()

    def _label(self, c, page_w, page_h, text):
        c.setFont('Helvetica', 7)
        c.setFillColorRGB(0.6, 0.65, 0.7)
        c.drawCentredString(page_w / 2, page_h - 11 * MM, text)

    def _crop_marks(self, c, x, y):
        c.saveState()
        c.setStrokeColorRGB(0.75, 0.78, 0.82)
        c.setLineWidth(0.3)
        for cx in (x, x + CARD_W):
            for cy in (y, y + CARD_H):
                c.line((cx - 3 if cx == x else cx + 1) * MM, cy * MM, (cx - 1 if cx == x else cx + 3) * MM, cy * MM)
                c.line(cx * MM, (cy - 3 if cy == y else cy + 1) * MM, cx * MM, (cy - 1 if cy == y else cy + 3) * MM)
        c.restoreState()

    # ------------------------------------------------------------------ piezas
    def _assets(self, company, assets):
        key = company.id
        if key in assets:
            return assets[key]
        logo = _pil(company.logo)
        if not logo:
            try:
                with file_open('scout_management/static/img/logo_grupo.png', 'rb') as handle:
                    logo = _pil(handle.read())
            except OSError:
                logo = None
        emblem = _pil(getattr(company, 'scout_emblem', False))
        conf = company._scout_card_config_company()
        signature = _pil(conf.scout_card_chief_signature)
        network = _pil(conf.scout_card_network_logo or company.scout_card_network_logo)
        data = {
            'logo': _reader(logo) if logo else None,
            'logo_ratio': (logo.width / logo.height) if logo else 1,
            'wm': _reader(_faded(logo)) if logo else None,
            'emblem': _reader(emblem) if emblem else None,
            'emblem_ratio': (emblem.width / emblem.height) if emblem else 1,
            'signature': _reader(signature) if signature else None,
            'signature_ratio': (signature.width / signature.height) if signature else 3,
            'network': _reader(network) if network else None,
            'network_ratio': (network.width / network.height) if network else 1,
            'group': company.scout_card_group_label or conf.scout_card_group_label or company.name or '',
            'chief': company.scout_card_chief_name or conf._scout_card_chief() or company._scout_card_chief(),
            'phone': company.scout_card_emergency_phone or conf.scout_card_emergency_phone or company.phone or '',
            'network_name': company.scout_card_network or conf.scout_card_network or NETWORK_DEFAULT,
        }
        assets[key] = data
        return data

    def _image(self, c, reader, ratio, x, y, max_w, max_h, anchor='left'):
        """Dibuja sin deformar dentro de la caja (x, y, max_w, max_h) en mm; regresa el ancho usado."""
        if not reader:
            return 0
        w, h = max_w, max_w / ratio
        if h > max_h:
            h, w = max_h, max_h * ratio
        dx = {'left': 0, 'center': (max_w - w) / 2, 'right': max_w - w}[anchor]
        c.drawImage(reader, (x + dx) * MM, (y + (max_h - h) / 2) * MM, w * MM, h * MM, mask='auto')
        return w

    def _text(self, c, text, x, y, size, color=INK, font='Helvetica', max_w=None, lines=1, leading=None):
        """Texto que no se sale: se parte en renglones y se recorta con "…". Regresa la y del siguiente renglón."""
        from reportlab.lib.utils import simpleSplit
        text = (text or '').strip()
        if not text:
            return y
        leading = leading or size * 1.18
        c.setFont(font, size)
        c.setFillColorRGB(*color)
        parts = simpleSplit(text, font, size, max_w * MM) if max_w else [text]
        if len(parts) > lines:
            parts = parts[:lines]
            last = parts[-1]
            while last and c.stringWidth(last + '…', font, size) > max_w * MM:
                last = last[:-1]
            parts[-1] = last.rstrip() + '…'
        for line in parts:
            c.drawString(x * MM, y * MM, line)
            y -= leading / MM
        return y

    def _card_shell(self, c, x, y):
        from reportlab.lib.colors import Color
        path = c.beginPath()
        path.roundRect(x * MM, y * MM, CARD_W * MM, CARD_H * MM, 3 * MM)
        c.saveState()
        c.clipPath(path, stroke=0, fill=0)
        c.setFillColor(Color(1, 1, 1))
        c.rect(x * MM, y * MM, CARD_W * MM, CARD_H * MM, stroke=0, fill=1)
        return path

    def _card_border(self, c, x, y):
        c.restoreState()
        c.setStrokeColorRGB(*NAVY)
        c.setLineWidth(0.6)
        c.roundRect(x * MM, y * MM, CARD_W * MM, CARD_H * MM, 3 * MM, stroke=1, fill=0)

    def _greca(self, c, x, y, w, h=2.0):
        """Greca (meandro) naranja sobre fondo blanco."""
        c.setFillColorRGB(1, 1, 1)
        c.rect(x * MM, y * MM, w * MM, h * MM, stroke=0, fill=1)
        c.setStrokeColorRGB(*ORANGE)
        c.setLineWidth(0.45)
        unit = h * 1.2
        pad = h * 0.18
        top, bottom = y + h - pad, y + pad
        cx = x
        while cx + unit <= x + w + 0.01:
            u = unit
            p = c.beginPath()
            p.moveTo(cx * MM, bottom * MM)
            p.lineTo((cx + u * 0.85) * MM, bottom * MM)
            p.lineTo((cx + u * 0.85) * MM, top * MM)
            p.lineTo((cx + u * 0.2) * MM, top * MM)
            p.lineTo((cx + u * 0.2) * MM, (bottom + (top - bottom) * 0.38) * MM)
            p.lineTo((cx + u * 0.55) * MM, (bottom + (top - bottom) * 0.38) * MM)
            c.drawPath(p, stroke=1, fill=0)
            cx += u
        c.setLineWidth(0.3)
        c.line(x * MM, (y + h) * MM, (x + w) * MM, (y + h) * MM)

    def _check(self, c, x, y, size):
        c.setStrokeColorRGB(1, 1, 1)
        c.setLineWidth(0.9)
        p = c.beginPath()
        p.moveTo(x * MM, (y + size * 0.5) * MM)
        p.lineTo((x + size * 0.35) * MM, (y + size * 0.15) * MM)
        p.lineTo((x + size) * MM, (y + size * 0.9) * MM)
        c.drawPath(p, stroke=1, fill=0)

    # ------------------------------------------------------------------ frente
    def _front(self, c, m, x, y, assets):
        company = (m.company_id or self.env.company).sudo()
        a = self._assets(company, assets)
        reg, cycle = m._scout_card_info()
        reg = reg if m.scout_type == 'youth' else None
        valid = m._scout_card_valid()
        self._card_shell(c, x, y)
        head_h = 10.5
        # marca de agua (logo con sus colores, muy tenue)
        if a['wm']:
            self._image(c, a['wm'], a['logo_ratio'], x + 22, y + 7, 42, 34, 'center')
        # encabezado
        c.setFillColorRGB(*NAVY)
        c.rect(x * MM, (y + CARD_H - head_h) * MM, CARD_W * MM, head_h * MM, stroke=0, fill=1)
        used = self._image(c, a['logo'], a['logo_ratio'], x + 2.6, y + CARD_H - head_h + 1.2, 12, head_h - 2.4)
        tx = x + 2.6 + (used + 2 if used else 0)
        self._text(c, a['group'], tx, y + CARD_H - 5.0, 8.6, (1, 1, 1), 'Helvetica-Bold', max_w=CARD_W - (tx - x) - 3)
        c.setFont('Helvetica-Bold', 5.6)
        c.setFillColorRGB(249 / 255, 180 / 255, 156 / 255)
        c.drawString(tx * MM, (y + CARD_H - 8.6) * MM, _('CREDENCIAL SCOUT'))
        self._greca(c, x, y + CARD_H - head_h - 2.0, CARD_W)
        # foto
        top = y + CARD_H - head_h - 2.0 - 1.6
        ph_w, ph_h = 20.0, 25.0
        px, py = x + 3, top - ph_h
        photo = _pil(m.image_1920) if m.image_1920 else None
        c.saveState()
        clip = c.beginPath()
        clip.roundRect(px * MM, py * MM, ph_w * MM, ph_h * MM, 1.5 * MM)
        c.clipPath(clip, stroke=0, fill=0)
        if photo:
            c.drawImage(_reader(_cover(photo, ph_w, ph_h)), px * MM, py * MM, ph_w * MM, ph_h * MM, mask='auto')
        else:
            c.setFillColorRGB(0.91, 0.93, 0.95)
            c.rect(px * MM, py * MM, ph_w * MM, ph_h * MM, stroke=0, fill=1)
            c.setFont('Helvetica', 6)
            c.setFillColorRGB(*MUTED)
            c.drawCentredString((px + ph_w / 2) * MM, (py + ph_h / 2) * MM, _('Sin foto'))
        c.restoreState()
        c.setStrokeColorRGB(*ORANGE)
        c.setLineWidth(0.9)
        c.roundRect(px * MM, py * MM, ph_w * MM, ph_h * MM, 1.5 * MM, stroke=1, fill=0)
        # datos
        qr_size = 17.0
        tx = x + 25.5
        max_w = CARD_W - 25.5 - qr_size - 4.5
        ty = top - 3.0
        ty = self._text(c, m.name, tx, ty, 8.8, NAVY, 'Helvetica-Bold', max_w=max_w, lines=2, leading=9.6)
        if m.scout_name:
            ty = self._text(c, m.scout_name, tx, ty - 0.3, 7.4, ORANGE, 'Helvetica-Bold', max_w=max_w)
        ty = self._text(c, m.scout_section_id.display_name or '', tx, ty - 0.4, 7, INK, 'Helvetica-Bold',
                        max_w=max_w)
        role = ' · '.join(x for x in (m.scout_patrol_id.name, m.scout_position_id.name) if x)
        ty = self._text(c, role, tx, ty, 6.4, INK, max_w=max_w, lines=2, leading=7.4)
        if reg:
            ty = self._text(c, _('Ficha %s', reg.name), tx, ty, 6.4, INK, max_w=max_w)
        if cycle:
            ty = self._text(c, _('Ciclo %(c)s · vence %(d)s', c=cycle.name,
                                 d=cycle.date_end.strftime('%d/%m/%Y') if cycle.date_end else ''), tx, ty, 6.4, INK,
                            max_w=max_w)
        # QR
        try:
            from .scout_online_payment import scout_qr_png
            qr = scout_qr_png(m._scout_card_url(), box_size=10)
            from reportlab.lib.utils import ImageReader
            c.drawImage(ImageReader(io.BytesIO(qr)), (x + CARD_W - qr_size - 2.6) * MM, (top - qr_size) * MM,
                        qr_size * MM, qr_size * MM)
            c.setFont('Helvetica', 4.6)
            c.setFillColorRGB(*MUTED)
            c.drawCentredString((x + CARD_W - qr_size / 2 - 2.6) * MM, (top - qr_size - 2.2) * MM, _('Verificar'))
        except Exception:  # noqa: BLE001
            _logger.exception('QR de la credencial')
        # pie
        foot_h = 6.0
        c.setFillColorRGB(*(GREEN if valid else RED))
        c.rect(x * MM, y * MM, CARD_W * MM, foot_h * MM, stroke=0, fill=1)
        c.setFont('Helvetica-Bold', 7)
        c.setFillColorRGB(1, 1, 1)
        if valid:
            self._check(c, x + 3, y + 1.6, 2.8)
            c.drawString((x + 7.2) * MM, (y + 2.0) * MM, _('REGISTRO VIGENTE'))
        else:
            c.drawString((x + 3) * MM, (y + 2.0) * MM, _('REGISTRO PENDIENTE'))
        if a['emblem']:
            self._image(c, a['emblem'], a['emblem_ratio'], x + CARD_W - 8, y + 0.6, 5, foot_h - 1.2, 'right')
        self._card_border(c, x, y)

    # ------------------------------------------------------------------ reverso
    def _back(self, c, m, x, y, assets):
        company = (m.company_id or self.env.company).sudo()
        a = self._assets(company, assets)
        reg, cycle = m._scout_card_info()
        self._card_shell(c, x, y)
        if a['wm']:
            self._image(c, a['wm'], a['logo_ratio'], x + 24, y + 9, 38, 30, 'center')
        # encabezado: pertenece al grupo + logo + tipo de sangre
        head_h = 11.0
        c.setFillColorRGB(*NAVY)
        c.rect(x * MM, (y + CARD_H - head_h) * MM, CARD_W * MM, head_h * MM, stroke=0, fill=1)
        used = self._image(c, a['logo'], a['logo_ratio'], x + 2.6, y + CARD_H - head_h + 1.2, 12, head_h - 2.4)
        tx = x + 2.6 + (used + 2 if used else 0)
        c.setFont('Helvetica', 5.4)
        c.setFillColorRGB(249 / 255, 180 / 255, 156 / 255)
        c.drawString(tx * MM, (y + CARD_H - 3.6) * MM, _('PERTENECE AL'))
        self._text(c, a['group'], tx, y + CARD_H - 7.0, 8.2, (1, 1, 1), 'Helvetica-Bold', max_w=CARD_W - (tx - x) - 19)
        blood = dict(m._fields['scout_blood_type'].selection).get(m.scout_blood_type) \
            if m.scout_blood_type and m.scout_blood_type != 'unknown' else ''
        bx, bw, bh = x + CARD_W - 15.5, 12.9, 7.6
        by = y + CARD_H - head_h + (head_h - bh) / 2
        c.setFillColorRGB(1, 1, 1)
        c.roundRect(bx * MM, by * MM, bw * MM, bh * MM, 1.3 * MM, stroke=0, fill=1)
        c.setFillColorRGB(*RED)
        c.setFont('Helvetica-Bold', 4.6)
        c.drawCentredString((bx + bw / 2) * MM, (by + bh - 2.0) * MM, _('SANGRE'))
        c.setFont('Helvetica-Bold', 8.6)
        c.drawCentredString((bx + bw / 2) * MM, (by + 1.3) * MM, blood or '—')
        c.setFillColorRGB(*ORANGE)
        c.rect(x * MM, (y + CARD_H - head_h - 0.9) * MM, CARD_W * MM, 0.9 * MM, stroke=0, fill=1)
        # quién es y su ficha
        ty = y + CARD_H - head_h - 4.2
        left_w = CARD_W - 31
        name_line = '%s · %s' % (m.name, m.scout_section_id.display_name or '') if m.scout_section_id else m.name
        ty = self._text(c, name_line, x + 3, ty, 6.8, NAVY, 'Helvetica-Bold', max_w=CARD_W - 6)
        folio = []
        if reg:
            folio.append(_('Ficha No. %s', reg.name))
        if m.ref:
            folio.append(_('Miembro %s', m.ref))
        if cycle:
            folio.append(_('Ciclo %s', cycle.name))
        ty = self._text(c, ' · '.join(folio), x + 3, ty, 6.2, INK, max_w=CARD_W - 6)
        c.setStrokeColorRGB(0.78, 0.82, 0.86)
        c.setDash(1, 1)
        c.setLineWidth(0.3)
        c.line((x + 3) * MM, (ty + 0.9) * MM, (x + CARD_W - 3) * MM, (ty + 0.9) * MM)
        c.setDash()
        col_top = ty - 1.6
        # columna izquierda: emergencias
        ty = col_top
        c.setFont('Helvetica-Bold', 5.6)
        c.setFillColorRGB(*ORANGE)
        c.drawString((x + 3) * MM, ty * MM, _('EN CASO DE EMERGENCIA'))
        ty -= 2.9
        if a['phone']:
            ty = self._text(c, _('Grupo: %s', a['phone']), x + 3, ty, 7.4, RED, 'Helvetica-Bold', max_w=left_w)
        contacts = []
        for ec in m.scout_emergency_ids[:2]:
            contacts.append('%s%s · %s' % (ec.name or '', ' (%s)' % ec.relationship if ec.relationship else '',
                                           ec.phone or ''))
        if not contacts and m.scout_guardian_id:
            g = m.scout_guardian_id
            contacts.append('%s%s%s' % (g.name, ' (%s)' % m.scout_guardian_relationship
                                        if m.scout_guardian_relationship else '', ' · %s' % g.phone if g.phone else ''))
        for line in contacts:
            ty = self._text(c, line, x + 3, ty, 6.2, NAVY, 'Helvetica-Bold', max_w=left_w)
        care = ' · '.join(v for v in (m.scout_allergies, m.scout_medical_conditions) if v)
        if care:
            ty = self._text(c, _('Alergias / cuidados: %s', care), x + 3, ty - 0.3, 5.6, INK, max_w=left_w, lines=2,
                            leading=6.4)
        service = dict(m._fields['scout_medical_service'].selection).get(m.scout_medical_service) \
            if m.scout_medical_service else ''
        if service:
            policy = m.scout_medical_policy if m.scout_medical_service not in (False, 'none') and m.scout_medical_policy \
                and m.scout_medical_policy.strip().lower() not in ('no', '0', 'n/a', 'na', 'ninguno') else ''
            self._text(c, _('Servicio médico: %s', service + (' · %s' % policy if policy else '')), x + 3, ty, 5.6,
                       INK, max_w=left_w, lines=2, leading=6.4)
        # columna derecha: firma del jefe de grupo
        sx, sw = x + CARD_W - 27, 24.0
        line_y = col_top - 11.5
        if a['signature']:
            self._image(c, a['signature'], a['signature_ratio'], sx, line_y + 0.4, sw, 10, 'center')
        c.setStrokeColorRGB(0.33, 0.33, 0.33)
        c.setLineWidth(0.35)
        c.line(sx * MM, line_y * MM, (sx + sw) * MM, line_y * MM)
        c.setFillColorRGB(*INK)
        chief = a['chief']
        if chief:
            from reportlab.lib.utils import simpleSplit
            c.setFont('Helvetica-Bold', 5.4)
            parts = simpleSplit(chief, 'Helvetica-Bold', 5.4, sw * MM)[:2]
            yy = line_y - 2.2
            for part in parts:
                c.drawCentredString((sx + sw / 2) * MM, yy * MM, part)
                yy -= 2.1
        else:
            yy = line_y - 2.2
        c.setFont('Helvetica', 5.0)
        c.drawCentredString((sx + sw / 2) * MM, yy * MM, _('Jefe de grupo'))
        if cycle and cycle.date_end:
            c.setFont('Helvetica', 5.0)
            c.setFillColorRGB(*MUTED)
            c.drawCentredString((sx + sw / 2) * MM, (yy - 2.6) * MM, _('Vigente al %s', cycle.date_end.strftime('%d/%m/%Y')))
        # red de grupos
        foot_h = 6.0
        net_y = y + foot_h + 0.8
        net_h = 4.4
        text = _('Miembro de la %s', a['network_name']) if a['network_name'] else ''
        c.setFont('Helvetica-Bold', 5.6)
        tw = c.stringWidth(text, 'Helvetica-Bold', 5.6) / MM
        logo_w = (net_h * a['network_ratio']) if a['network'] else 0
        total = tw + (logo_w + 1.2 if logo_w else 0)
        start = x + (CARD_W - min(total, CARD_W - 6)) / 2
        if a['network']:
            self._image(c, a['network'], a['network_ratio'], start, net_y, logo_w, net_h)
            start += logo_w + 1.2
        self._text(c, text, start, net_y + 1.4, 5.6, NAVY, 'Helvetica-Bold', max_w=CARD_W - 6 - (logo_w + 1.2 if logo_w else 0))
        # pie
        c.setFillColorRGB(*NAVY)
        c.rect(x * MM, y * MM, CARD_W * MM, foot_h * MM, stroke=0, fill=1)
        c.setFont('Helvetica-Bold', 6.4)
        c.setFillColorRGB(1, 1, 1)
        found = _('Si la encuentras llama al %s', a['phone']) if a['phone'] else _('SIEMPRE LISTOS PARA SERVIR')
        c.drawCentredString((x + CARD_W / 2) * MM, (y + 2.0) * MM, found)
        self._card_border(c, x, y)


class IrActionsReportScoutCard(models.Model):
    _inherit = 'ir.actions.report'

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        report = self._get_report(report_ref)
        if report.report_name == 'scout_management.report_member_card':
            ids = res_ids if isinstance(res_ids, list) else ([res_ids] if res_ids else [])
            members = self.env['res.partner'].browse(ids).exists()
            layout = self.env.context.get('scout_card_layout') or 'sheet'
            return self.env['scout.card.pdf']._render(members, layout=layout), 'pdf'
        return super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)
