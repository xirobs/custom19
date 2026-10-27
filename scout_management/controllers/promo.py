# -*- coding: utf-8 -*-
"""Página pública de la plataforma scout (publicidad, servicios y contacto)."""
import re
import time

from odoo import _, http
from odoo.http import request

from ..models.scout_promo import MEMBER_RANGES, PROMO_INTERESTS

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class ScoutPromo(http.Controller):

    def _promo_values(self, **extra):
        company = request.env.company.sudo()
        values = {
            'company': company,
            'interests': PROMO_INTERESTS,
            'member_ranges': MEMBER_RANGES,
            'whatsapp_url': company._scout_promo_whatsapp_url(
                'Hola, me interesa la plataforma scout para mi grupo.'),
            'promo_brand': company.scout_promo_brand or '',
            'form_ts': int(time.time()),
            'post': {},
        }
        values.update(extra)
        return values

    @http.route(['/plataforma-scout'], type='http', auth='public', website=True, sitemap=True)
    def scout_promo_page(self, enviado=None, **kw):
        return request.render('scout_management.scout_promo_page', self._promo_values(sent=bool(enviado)))

    @http.route(['/plataforma-scout/contacto'], type='http', auth='public', website=True, methods=['POST'])
    def scout_promo_contact(self, **post):
        # Trampa para robots: el campo "website" está oculto y un formulario enviado en menos de 3 s no es humano.
        try:
            too_fast = time.time() - int(post.get('form_ts') or 0) < 3
        except ValueError:
            too_fast = True
        if post.get('website') or too_fast:
            return request.redirect('/plataforma-scout?enviado=1#contacto')
        errors = []
        if len((post.get('name') or '').strip()) < 3:
            errors.append(_('Escribe tu nombre.'))
        email = (post.get('email') or '').strip()
        phone = re.sub(r'\D', '', post.get('phone') or '')
        if not email and len(phone) < 10:
            errors.append(_('Déjanos un correo o un teléfono para contactarte.'))
        if email and not EMAIL_RE.match(email):
            errors.append(_('Revisa el correo.'))
        if errors:
            return request.render('scout_management.scout_promo_page',
                                  self._promo_values(errors=errors, post=post))
        request.env['scout.promo.lead'].sudo()._scout_create_from_web(post)
        return request.redirect('/plataforma-scout?enviado=1#contacto')
