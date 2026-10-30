# -*- coding: utf-8 -*-
"""App instalable de Red Seguro Scout (solo este módulo).

Es independiente de la app de BitaScout: su propio nombre, ícono, identificador y
punto de entrada (/seguro-app), que lleva a cada quien a su portal.
"""
import json

from odoo import http
from odoo.http import request

WINE = '#6E0B14'
CREAM = '#F8F1E7'
ICON_BASE = '/red_seguro_scout/static/src/img/app/'


class RssApp(http.Controller):

    @http.route(['/seguro-app'], type='http', auth='public', website=True, sitemap=False)
    def rss_app_start(self, **kw):
        """Punto de entrada del ícono: representante → /mi-grupo, asesor → /seguro."""
        user = request.env.user
        if user._is_public():
            return request.redirect('/web/login?redirect=/seguro-app')
        if request.env['rss.group.member']._for_user(user):
            return request.redirect('/mi-grupo')
        if request.env['rss.advisor']._for_user(user):
            return request.redirect('/seguro')
        if user._is_internal() and user.has_group('red_seguro_scout.group_rss_user'):
            return request.redirect('/odoo/action-red_seguro_scout.rss_insured_action')
        return request.redirect('/my')

    @http.route(['/seguro-app/manifest.webmanifest'], type='http', auth='public', methods=['GET'],
                readonly=True, sitemap=False)
    def rss_app_manifest(self, **kw):
        manifest = {
            'id': '/seguro-app',
            'name': 'Red Seguro Scout',
            'short_name': 'Seguro Scout',
            'description': 'Asegurados, pagos, certificados y siniestros de tu grupo scout',
            'lang': 'es-MX',
            'start_url': '/seguro-app',
            'scope': '/',
            'display': 'standalone',
            'orientation': 'portrait',
            'background_color': CREAM,
            'theme_color': WINE,
            'icons': [
                {'src': ICON_BASE + 'icon_192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
                {'src': ICON_BASE + 'icon_512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
                {'src': ICON_BASE + 'maskable_512.png', 'sizes': '512x512', 'type': 'image/png',
                 'purpose': 'maskable'},
            ],
            'shortcuts': [
                {'name': 'Cargar asegurados', 'url': '/mi-grupo/importar',
                 'icons': [{'src': ICON_BASE + 'icon_192.png', 'sizes': '192x192'}]},
                {'name': 'Certificados', 'url': '/mi-grupo/certificados',
                 'icons': [{'src': ICON_BASE + 'icon_192.png', 'sizes': '192x192'}]},
            ],
        }
        return request.make_response(json.dumps(manifest, ensure_ascii=False), [
            ('Content-Type', 'application/manifest+json; charset=utf-8'),
            ('Cache-Control', 'public, max-age=3600')])
