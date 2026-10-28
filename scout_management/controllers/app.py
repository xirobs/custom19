# -*- coding: utf-8 -*-
"""La plataforma como app en el celular (PWA): ícono en la pantalla de inicio, se abre sin barra del navegador
y entra directo a lo de cada quien (portal de jefes, Mis scouts o el sistema)."""
import base64
import io
import json

from PIL import Image, ImageDraw

from odoo import http
from odoo.http import request
from odoo.tools import file_open

NAVY = '#0f3049'
CREAM = '#fbf7f0'

SERVICE_WORKER = """/* Plataforma scout: app instalable. Solo guarda la página "sin conexión"; todo lo demás va a la red. */
const CACHE = "scout-app-v1";
const OFFLINE = "/scouts/sin-conexion";
self.addEventListener("install", (event) => {
    event.waitUntil(caches.open(CACHE).then((cache) => cache.add(OFFLINE)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (event) => {
    event.waitUntil(caches.keys().then((keys) => Promise.all(
        keys.filter((k) => k.startsWith("scout-app-") && k !== CACHE).map((k) => caches.delete(k))
    )).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (event) => {
    const req = event.request;
    if (req.mode !== "navigate" || req.method !== "GET") {
        return;
    }
    const url = new URL(req.url);
    if (url.pathname.startsWith("/odoo") || url.pathname.startsWith("/web/")) {
        return;
    }
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE)));
});
"""


class ScoutApp(http.Controller):

    def _company(self):
        return request.env.company.sudo()

    @http.route('/scouts/app', type='http', auth='public', website=True, sitemap=False)
    def scout_app_start(self, **kw):
        """Punto de entrada del ícono: cada quien a su portal."""
        user = request.env.user
        if request.env.user._is_public():
            return request.redirect('/web/login?redirect=/scouts/app')
        if user.sudo()._scout_leader_sections():
            return request.redirect('/dirigentes')
        partner = user.partner_id.sudo()
        has_scouts = request.env['res.partner'].sudo().search_count([
            ('is_scout', '=', True), ('scout_state', '!=', 'inactive'),
            '|', '|', ('id', '=', partner.id), ('scout_guardian_id', 'child_of', partner.id),
            ('scout_guardian2_id', 'child_of', partner.id)], limit=1)
        if has_scouts:
            return request.redirect('/my/scouts')
        if user._is_internal():
            return request.redirect('/odoo')
        return request.redirect('/my')

    @http.route('/scouts/manifest.webmanifest', type='http', auth='public', methods=['GET'], readonly=True,
                sitemap=False)
    def scout_app_manifest(self, **kw):
        company = self._company()
        name = company.scout_app_name or company.name or 'Plataforma Scout'
        manifest = {
            'id': '/scouts/app',
            'name': name,
            'short_name': (company.scout_app_short_name or name)[:12],
            'description': 'Portal de jefes y familias del grupo scout',
            'lang': 'es-MX',
            'start_url': '/scouts/app',
            'scope': '/',
            'display': 'standalone',
            'orientation': 'portrait',
            'background_color': CREAM,
            'theme_color': NAVY,
            'icons': [
                {'src': f'/scouts/app-icon/{size}.png?c={company.id}', 'sizes': f'{size}x{size}', 'type': 'image/png',
                 'purpose': 'any'} for size in (192, 512)
            ] + [{'src': f'/scouts/app-icon/512.png?c={company.id}&maskable=1', 'sizes': '512x512',
                  'type': 'image/png', 'purpose': 'maskable'}],
            'shortcuts': [
                {'name': 'Portal de jefes', 'url': '/dirigentes',
                 'icons': [{'src': f'/scouts/app-icon/192.png?c={company.id}', 'sizes': '192x192'}]},
                {'name': 'Mis scouts', 'url': '/my/scouts',
                 'icons': [{'src': f'/scouts/app-icon/192.png?c={company.id}', 'sizes': '192x192'}]},
            ],
        }
        return request.make_response(json.dumps(manifest, ensure_ascii=False), [
            ('Content-Type', 'application/manifest+json; charset=utf-8'), ('Cache-Control', 'public, max-age=3600')])

    @http.route('/scouts/app-icon/<int:size>.png', type='http', auth='public', methods=['GET'], readonly=True,
                sitemap=False)
    def scout_app_icon(self, size=192, c=None, maskable=None, **kw):
        size = 512 if size > 192 else (180 if size == 180 else 192)
        company = request.env['res.company'].sudo().browse(int(c or 0)).exists() or self._company()
        raw = company.scout_app_icon or company.logo
        try:
            logo = Image.open(io.BytesIO(base64.b64decode(raw))) if raw else None
        except Exception:  # noqa: BLE001
            logo = None
        if logo is None:
            with file_open('scout_management/static/description/icon.png', 'rb') as f:
                logo = Image.open(io.BytesIO(f.read()))
        logo = logo.convert('RGBA')
        # recorta los márgenes vacíos (transparentes o blancos) para que el logo llene el ícono
        alpha_box = logo.getchannel('A').point(lambda a: 255 if a > 16 else 0).getbbox()
        if alpha_box:
            logo = logo.crop(alpha_box)
        white = Image.new('RGBA', logo.size, (255, 255, 255, 255))
        diff = Image.alpha_composite(white, logo).convert('L').point(lambda v: 255 if v < 245 else 0).getbbox()
        if diff:
            logo = logo.crop(diff)
        canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        bg = Image.new('RGBA', (size, size), CREAM)
        mask = Image.new('L', (size, size), 0)
        if maskable or size == 180:
            # Android recorta el ícono adaptable y iPhone redondea el suyo: se entrega cuadrado y lleno
            mask.paste(255, (0, 0, size, size))
            inner = int(size * (0.62 if maskable else 0.8))  # zona segura de los íconos adaptables
        else:
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * 0.22), fill=255)
            inner = int(size * 0.8)
        canvas.paste(bg, (0, 0), mask)
        # franja naranja y azul abajo, como la pañoleta
        band = ImageDraw.Draw(canvas)
        if not maskable:
            band.rectangle((0, int(size * 0.9), size, size), fill=NAVY)
            band.rectangle((0, int(size * 0.885), size, int(size * 0.9)), fill='#f36f45')
            canvas.putalpha(Image.composite(canvas.getchannel('A'), Image.new('L', (size, size), 0), mask))
        box_w, box_h = inner, int(inner * (0.88 if not maskable else 1))
        ratio = min(box_w / logo.width, box_h / logo.height)  # también agranda logos pequeños
        logo = logo.resize((max(1, int(logo.width * ratio)), max(1, int(logo.height * ratio))), Image.LANCZOS)
        x = (size - logo.width) // 2
        y = (int(size * (0.9 if not maskable else 1)) - logo.height) // 2
        canvas.alpha_composite(logo, (x, max(y, 0)))
        out = io.BytesIO()
        (canvas.convert('RGB') if size == 180 else canvas).save(out, 'PNG', optimize=True)
        return request.make_response(out.getvalue(), [('Content-Type', 'image/png'),
                                                      ('Cache-Control', 'public, max-age=86400')])

    @http.route('/scout-sw.js', type='http', auth='public', methods=['GET'], readonly=True, sitemap=False)
    def scout_app_sw(self, **kw):
        return request.make_response(SERVICE_WORKER, [('Content-Type', 'text/javascript'),
                                                      ('Service-Worker-Allowed', '/'),
                                                      ('Cache-Control', 'no-cache')])

    @http.route('/scouts/sin-conexion', type='http', auth='public', methods=['GET'], readonly=True, sitemap=False)
    def scout_app_offline(self, **kw):
        company = self._company()
        html = f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Sin conexión</title>
<style>body{{margin:0;font-family:system-ui,sans-serif;background:{NAVY};color:#fff;display:flex;min-height:100vh;
align-items:center;justify-content:center;text-align:center;padding:24px}}.c{{max-width:340px}}
img{{width:96px;height:96px;border-radius:22px;background:{CREAM}}}h1{{font-size:1.4rem;margin:18px 0 6px}}
p{{opacity:.8}}button{{margin-top:14px;background:#f36f45;color:#fff;border:0;border-radius:99px;
padding:12px 26px;font-weight:800;font-size:1rem}}</style></head><body><div class="c">
<img src="/scouts/app-icon/192.png?c={company.id}" alt="">
<h1>Sin conexión</h1><p>Revisa tu internet y vuelve a intentar. Siempre listos ⚜</p>
<button onclick="location.reload()">Reintentar</button></div></body></html>"""
        return request.make_response(html, [('Content-Type', 'text/html; charset=utf-8')])

    @http.route('/scouts/pdf', type='http', auth='user', methods=['GET'], sitemap=False)
    def scout_app_pdf(self, u='', back='', **kw):
        """Visor de PDF dentro de la app (con botón para regresar): en el celular instalado como app el PDF
        se abría sin barra y no había cómo volver."""
        from html import escape
        from urllib.parse import quote
        path = u if u.startswith('/') and not u.startswith('//') else '/'
        back_url = back if back.startswith('/') and not back.startswith('//') else '/scouts/app'
        viewer = '/web/static/lib/pdfjs/web/viewer.html?file=' + quote(path, safe='')
        html = f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><title>PDF</title>
<meta name="theme-color" content="{NAVY}">
<style>html,body{{margin:0;height:100%;background:#525659;overscroll-behavior:none}}
.bar{{position:fixed;top:0;left:0;right:0;z-index:2;display:flex;align-items:center;gap:10px;background:{NAVY};
color:#fff;padding:calc(8px + env(safe-area-inset-top)) 12px 8px;border-bottom:3px solid #f36f45;font-family:system-ui,sans-serif}}
.bar a{{color:#fff;text-decoration:none;font-weight:800;border:1px solid rgba(255,255,255,.45);border-radius:99px;
padding:6px 14px;font-size:15px}}.bar a.dl{{margin-left:auto;background:#f36f45;border-color:#f36f45}}
iframe{{position:fixed;left:0;right:0;bottom:0;top:calc(52px + env(safe-area-inset-top));width:100%;
height:calc(100% - 52px - env(safe-area-inset-top));border:0;background:#525659}}</style></head><body>
<div class="bar"><a href="{escape(back_url)}" onclick="if(history.length>1){{history.back();return false;}}">&#8249; Regresar</a>
<a class="dl" href="{escape(path)}" download>Descargar</a></div>
<iframe src="{escape(viewer)}" title="PDF"></iframe></body></html>"""
        return request.make_response(html, [('Content-Type', 'text/html; charset=utf-8')])

