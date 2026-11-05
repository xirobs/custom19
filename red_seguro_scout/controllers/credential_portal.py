# -*- coding: utf-8 -*-
"""Credencial del seguro en los portales y página pública del QR."""
import base64

from odoo import _, http
from odoo.http import content_disposition, request

from .advisor_portal import RssAdvisorPortal
from .group_portal import RssGroupPortal

MAX_PHOTO = 8 * 1024 * 1024
PHOTO_TYPES = ('image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif')


def _pdf(insured, name):
    insured._credential_ensure_token()
    pdf, _fmt = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
        'red_seguro_scout.action_report_rss_credential', insured.ids)
    return request.make_response(pdf, headers=[('Content-Type', 'application/pdf'),
                                               ('Content-Disposition', content_disposition(name))])


def _save_photo(insured, post):
    """Sube o quita la foto. Devuelve (ok, mensaje)."""
    if post.get('remove'):
        insured.sudo().photo = False
        insured.sudo().message_post(body=_('Foto de la credencial quitada desde el portal.'))
        return True, _('Foto quitada: la credencial usa el acomodo sin foto.')
    upload = request.httprequest.files.get('photo')
    if not upload or not upload.filename:
        return False, _('Elige una foto.')
    data = upload.read()
    if len(data) > MAX_PHOTO:
        return False, _('La foto pesa más de 8 MB.')
    if (upload.mimetype or '').lower() not in PHOTO_TYPES:
        return False, _('Sube una foto JPG o PNG.')
    try:
        insured.sudo().photo = base64.b64encode(data)
    except Exception:  # noqa: BLE001 - imagen dañada o formato no soportado
        return False, _('No se pudo leer la foto; prueba con otra en JPG o PNG.')
    insured.sudo().message_post(body=_('Foto de la credencial actualizada desde el portal.'))
    return True, _('Foto guardada.')


def _filename(insured):
    return 'Credencial_%s.pdf' % (insured.name or 'asegurado').replace(' ', '_')


class RssGroupCredential(RssGroupPortal):

    def _cred_insured(self, member, insured_id):
        insured = request.env['rss.insured'].sudo().browse(int(insured_id)).exists()
        return insured if insured and insured.group_id == member.group_id else None

    @http.route(['/mi-grupo/asegurados/<int:insured_id>/credencial'], type='http', auth='user', website=True,
                methods=['GET'], sitemap=False)
    def group_credential(self, insured_id, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        insured = self._cred_insured(member, insured_id)
        if not insured:
            return request.redirect('/mi-grupo/asegurados')
        return self._render('red_seguro_scout.group_credential', member, 'insured', insured=insured,
                            c=insured._credential_values(), base='/mi-grupo/asegurados/%s/credencial' % insured.id,
                            back='/mi-grupo/asegurados')

    @http.route(['/mi-grupo/asegurados/<int:insured_id>/credencial'], type='http', auth='user', website=True,
                methods=['POST'], sitemap=False)
    def group_credential_photo(self, insured_id, **post):
        member, denied = self._guard()
        if denied:
            return denied
        url = '/mi-grupo/asegurados/%s/credencial' % insured_id
        insured = self._cred_insured(member, insured_id)
        if not insured:
            return request.redirect('/mi-grupo/asegurados')
        ok, msg = _save_photo(insured, post)
        return self._back(url, **({'ok': msg} if ok else {'error': msg}))

    @http.route(['/mi-grupo/asegurados/<int:insured_id>/credencial.pdf'], type='http', auth='user', sitemap=False)
    def group_credential_pdf(self, insured_id, **kw):
        member = request.env['rss.group.member']._for_user(request.env.user)
        insured = self._cred_insured(member, insured_id) if member else None
        return _pdf(insured, _filename(insured)) if insured else request.not_found()

    @http.route(['/mi-grupo/credenciales.pdf'], type='http', auth='user', sitemap=False)
    def group_credentials_pdf(self, estatus='active', **kw):
        member = request.env['rss.group.member']._for_user(request.env.user)
        if not member:
            return request.not_found()
        states = ['active'] if estatus == 'active' else ['active', 'draft']
        insured = request.env['rss.insured'].sudo().search(
            [('group_id', '=', member.group_id.id), ('state', 'in', states)], order='last_name_father, first_name')
        if not insured:
            return self._back('/mi-grupo/asegurados', error=_('No hay asegurados para imprimir credenciales.'))
        return _pdf(insured, 'Credenciales_%s.pdf' % (member.group_id.number or member.group_id.id))


class RssAdvisorCredential(RssAdvisorPortal):

    def _cred_insured(self, advisor, insured_id):
        insured = request.env['rss.insured'].sudo().browse(int(insured_id)).exists()
        return insured if insured and insured.group_id in self._groups(advisor) else None

    @http.route(['/seguro/asegurados/<int:insured_id>/credencial'], type='http', auth='user', website=True,
                methods=['GET'], sitemap=False)
    def advisor_credential(self, insured_id, **kw):
        advisor, denied = self._guard()
        if denied:
            return denied
        insured = self._cred_insured(advisor, insured_id)
        if not insured:
            return request.redirect('/seguro/asegurados')
        return self._render('red_seguro_scout.advisor_credential', advisor, 'insured', insured=insured,
                            c=insured._credential_values(), base='/seguro/asegurados/%s/credencial' % insured.id,
                            back='/seguro/asegurados?grupo=%s' % insured.group_id.id)

    @http.route(['/seguro/asegurados/<int:insured_id>/credencial'], type='http', auth='user', website=True,
                methods=['POST'], sitemap=False)
    def advisor_credential_photo(self, insured_id, **post):
        advisor, denied = self._guard()
        if denied:
            return denied
        url = '/seguro/asegurados/%s/credencial' % insured_id
        insured = self._cred_insured(advisor, insured_id)
        if not insured:
            return request.redirect('/seguro/asegurados')
        ok, msg = _save_photo(insured, post)
        return self._back(url, **({'ok': msg} if ok else {'error': msg}))

    @http.route(['/seguro/asegurados/<int:insured_id>/credencial.pdf'], type='http', auth='user', sitemap=False)
    def advisor_credential_pdf(self, insured_id, **kw):
        advisor = request.env['rss.advisor']._for_user(request.env.user)
        insured = self._cred_insured(advisor, insured_id) if advisor else None
        return _pdf(insured, _filename(insured)) if insured else request.not_found()

    @http.route(['/seguro/credenciales.pdf'], type='http', auth='user', sitemap=False)
    def advisor_credentials_pdf(self, grupo=None, estatus='active', **kw):
        advisor = request.env['rss.advisor']._for_user(request.env.user)
        if not advisor:
            return request.not_found()
        groups = self._groups(advisor)
        if grupo:
            groups = groups.filtered(lambda g: str(g.id) == str(grupo))
        states = ['active'] if estatus == 'active' else ['active', 'draft']
        insured = request.env['rss.insured'].sudo().search(
            [('group_id', 'in', groups.ids), ('state', 'in', states)], order='group_id, last_name_father, first_name')
        if not insured:
            return self._back('/seguro/asegurados', error=_('No hay asegurados para imprimir credenciales.'))
        return _pdf(insured, 'Credenciales.pdf')


class RssCredentialPublic(http.Controller):

    @http.route(['/credencial/<string:token>'], type='http', auth='public', website=True, sitemap=False)
    def credential_public(self, token, **kw):
        insured = request.env['rss.insured'].sudo().search([('credential_token', '=', token)], limit=1) \
            if token and len(token) >= 12 else request.env['rss.insured']
        if not insured:
            return request.render('red_seguro_scout.credential_public', {'insured': False, 'c': {},
                                                                         'page_name': 'rss_credential'})
        return request.render('red_seguro_scout.credential_public', {'insured': insured, 'c': insured._credential_values(),
                                                                     'page_name': 'rss_credential'})
