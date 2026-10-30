# -*- coding: utf-8 -*-
"""Certificados de la póliza desde los portales del grupo (/mi-grupo) y del asesor (/seguro)."""
import io
import logging

from odoo import _, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import content_disposition, request

from .advisor_portal import RssAdvisorPortal
from .group_portal import RssGroupPortal

_logger = logging.getLogger(__name__)
MAX_CERT_UPLOAD = 60 * 1024 * 1024


def _read_files():
    files, total = [], 0
    for upload in request.httprequest.files.getlist('files'):
        if upload and upload.filename:
            data = upload.read()
            total += len(data)
            files.append((upload.filename, data))
    if total > MAX_CERT_UPLOAD:
        raise UserError(_('Los archivos pesan más de 60 MB; súbelos en partes.'))
    return files


def _stats(groups):
    Insured = request.env['rss.insured'].sudo()
    dom = [('group_id', 'in', groups.ids), ('state', '=', 'active')]
    return {'with': Insured.search_count(dom + [('has_certificate', '=', True)]),
            'without': Insured.search_count(dom + [('has_certificate', '=', False)])}


def _apply_review(batch, post):
    assignments, skips = {}, set()
    for line in batch.line_ids.filtered(lambda l: not l.attached):
        key = 'l%s_insured' % line.id
        if key in post:
            try:
                assignments[line.id] = int(post.get(key) or 0)
            except ValueError:
                assignments[line.id] = 0
        if post.get('l%s_skip' % line.id):
            skips.add(line.id)
    batch.portal_apply(assignments, skips)


def _line_pdf(batch, line_id):
    from odoo.tools.pdf import PdfFileReader, PdfFileWriter
    line = batch.line_ids.filtered(lambda l: l.id == int(line_id))
    if not line:
        return request.not_found()
    reader = PdfFileReader(io.BytesIO(line.attachment_id.raw), strict=False)
    writer = PdfFileWriter()
    for index in range(line.page_from - 1, line.page_to):
        writer.add_page(reader.pages[index])
    out = io.BytesIO()
    writer.write(out)
    return request.make_response(out.getvalue(), headers=[
        ('Content-Type', 'application/pdf'),
        ('Content-Disposition', content_disposition('certificado_pag_%s.pdf' % line.pages, disposition_type='inline'))])


class RssGroupCertificates(RssGroupPortal):

    def _cert_batch(self, member, batch_id):
        batch = request.env['rss.certificate.batch'].sudo().browse(int(batch_id)).exists()
        if not batch or batch.source != 'group' or batch.group_ids != member.group_id:
            return None
        return batch

    def _cert_values(self, member):
        group = member.group_id.sudo()
        return {'base_url': '/mi-grupo', 'can_upload': member.is_capturer, 'groups': group}

    @http.route(['/mi-grupo/certificados'], type='http', auth='user', website=True, methods=['GET'], sitemap=False)
    def group_certificates(self, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        values = self._cert_values(member)
        batches = request.env['rss.certificate.batch'].sudo().search(
            [('source', '=', 'group'), ('group_ids', 'in', member.group_id.ids)], limit=20)
        return self._render('red_seguro_scout.group_certificates', member, 'certificates',
                            batches=batches.filtered(lambda b: b.group_ids == member.group_id),
                            stats=_stats(member.group_id), **values)

    @http.route(['/mi-grupo/certificados'], type='http', auth='user', website=True, methods=['POST'], sitemap=False)
    def group_certificates_upload(self, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        if not member.is_capturer:
            return self._back('/mi-grupo/certificados', error=_('Solo el capturista de tu grupo puede cargar certificados.'))
        try:
            batch = request.env['rss.certificate.batch'].sudo().create_from_portal(
                member.group_id.sudo(), _read_files(), member=member)
        except (UserError, ValidationError) as exc:
            return self._back('/mi-grupo/certificados', error=str(exc))
        return request.redirect('/mi-grupo/certificados/%s' % batch.id)

    @http.route(['/mi-grupo/certificados/<int:batch_id>'], type='http', auth='user', website=True,
                methods=['GET'], sitemap=False)
    def group_certificate_review(self, batch_id, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        batch = self._cert_batch(member, batch_id)
        if not batch:
            return request.redirect('/mi-grupo/certificados')
        return self._render('red_seguro_scout.group_certificate_review', member, 'certificates', batch=batch,
                            allowed=batch._portal_allowed_insured().sorted('name'), **self._cert_values(member))

    @http.route(['/mi-grupo/certificados/<int:batch_id>'], type='http', auth='user', website=True,
                methods=['POST'], sitemap=False)
    def group_certificate_save(self, batch_id, action='save', **post):
        member, denied = self._guard()
        if denied:
            return denied
        url = '/mi-grupo/certificados/%s' % batch_id
        batch = self._cert_batch(member, batch_id)
        if not batch or not member.is_capturer or batch.state != 'review':
            return request.redirect('/mi-grupo/certificados')
        try:
            _apply_review(batch, post)
            if action == 'attach':
                batch.action_attach()
                return self._back(url, ok=_('%s certificados anexados a sus asegurados.', batch.attached_count))
        except (UserError, ValidationError) as exc:
            return self._back(url, error=str(exc))
        return self._back(url, ok=_('Cambios guardados.'))

    @http.route(['/mi-grupo/certificados/<int:batch_id>/linea/<int:line_id>.pdf'], type='http', auth='user', sitemap=False)
    def group_certificate_line(self, batch_id, line_id, **kw):
        member = self._member()
        batch = self._cert_batch(member, batch_id) if member else None
        return _line_pdf(batch, line_id) if batch else request.not_found()


class RssAdvisorCertificates(RssAdvisorPortal):

    def _cert_batch(self, advisor, batch_id):
        batch = request.env['rss.certificate.batch'].sudo().browse(int(batch_id)).exists()
        if not batch or batch.advisor_id != advisor:
            return None
        return batch

    def _cert_values(self, advisor):
        return {'base_url': '/seguro', 'can_upload': advisor.can_upload_certificates,
                'groups': self._groups(advisor)}

    @http.route(['/seguro/certificados'], type='http', auth='user', website=True, methods=['GET'], sitemap=False)
    def advisor_certificates(self, **kw):
        advisor, denied = self._guard()
        if denied:
            return denied
        values = self._cert_values(advisor)
        batches = request.env['rss.certificate.batch'].sudo().search([('advisor_id', '=', advisor.id)], limit=20)
        return self._render('red_seguro_scout.advisor_certificates', advisor, 'certificates', batches=batches,
                            stats=_stats(values['groups']), **values)

    @http.route(['/seguro/certificados'], type='http', auth='user', website=True, methods=['POST'], sitemap=False)
    def advisor_certificates_upload(self, **kw):
        advisor, denied = self._guard()
        if denied:
            return denied
        if not advisor.can_upload_certificates:
            return self._back('/seguro/certificados', error=_('Tu acceso no incluye la carga de certificados.'))
        try:
            batch = request.env['rss.certificate.batch'].sudo().create_from_portal(
                self._groups(advisor), _read_files(), advisor=advisor)
        except (UserError, ValidationError) as exc:
            return self._back('/seguro/certificados', error=str(exc))
        return request.redirect('/seguro/certificados/%s' % batch.id)

    @http.route(['/seguro/certificados/<int:batch_id>'], type='http', auth='user', website=True,
                methods=['GET'], sitemap=False)
    def advisor_certificate_review(self, batch_id, **kw):
        advisor, denied = self._guard()
        if denied:
            return denied
        batch = self._cert_batch(advisor, batch_id)
        if not batch:
            return request.redirect('/seguro/certificados')
        return self._render('red_seguro_scout.advisor_certificate_review', advisor, 'certificates', batch=batch,
                            allowed=batch._portal_allowed_insured().sorted('name'), **self._cert_values(advisor))

    @http.route(['/seguro/certificados/<int:batch_id>'], type='http', auth='user', website=True,
                methods=['POST'], sitemap=False)
    def advisor_certificate_save(self, batch_id, action='save', **post):
        advisor, denied = self._guard()
        if denied:
            return denied
        url = '/seguro/certificados/%s' % batch_id
        batch = self._cert_batch(advisor, batch_id)
        if not batch or not advisor.can_upload_certificates or batch.state != 'review':
            return request.redirect('/seguro/certificados')
        try:
            _apply_review(batch, post)
            if action == 'attach':
                batch.action_attach()
                return self._back(url, ok=_('%s certificados anexados a sus asegurados.', batch.attached_count))
        except (UserError, ValidationError) as exc:
            return self._back(url, error=str(exc))
        return self._back(url, ok=_('Cambios guardados.'))

    @http.route(['/seguro/certificados/<int:batch_id>/linea/<int:line_id>.pdf'], type='http', auth='user', sitemap=False)
    def advisor_certificate_line(self, batch_id, line_id, **kw):
        advisor = self._advisor()
        batch = self._cert_batch(advisor, batch_id) if advisor else None
        return _line_pdf(batch, line_id) if batch else request.not_found()

    @http.route(['/seguro/asegurados/<int:insured_id>/certificado'], type='http', auth='user', sitemap=False)
    def advisor_insured_certificate(self, insured_id, **kw):
        advisor = self._advisor()
        if not advisor:
            return request.not_found()
        insured = request.env['rss.insured'].sudo().browse(insured_id).exists()
        if not insured or insured.group_id not in self._groups(advisor) or not insured.certificate_attachment_id:
            return request.not_found()
        att = insured.certificate_attachment_id
        return request.make_response(att.raw or b'', headers=[
            ('Content-Type', 'application/pdf'), ('Content-Disposition', content_disposition(att.name))])
