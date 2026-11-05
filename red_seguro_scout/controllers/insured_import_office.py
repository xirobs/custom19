# -*- coding: utf-8 -*-
"""Carga de listas (Excel / PDF) desde el portal del asesor y plantilla de la plataforma."""
from odoo import _, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import content_disposition, request

from ..models.rss_parsers import build_template_xlsx
from .advisor_portal import RssAdvisorPortal

MAX_UPLOAD = 25 * 1024 * 1024


def _xlsx(data, name):
    return request.make_response(data, headers=[
        ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
        ('Content-Disposition', content_disposition(name))])


class RssAdvisorImport(RssAdvisorPortal):

    def _advisor_batches(self, advisor):
        return request.env['rss.import.batch'].sudo().search(
            [('group_id', 'in', self._groups(advisor).ids)], order='id desc', limit=30)

    def _advisor_batch(self, advisor, batch_id):
        batch = request.env['rss.import.batch'].sudo().browse(int(batch_id)).exists()
        if not batch or batch.group_id not in self._groups(advisor):
            return None
        return batch

    @http.route(['/seguro/cargar'], type='http', auth='user', website=True, methods=['GET'], sitemap=False)
    def advisor_import(self, grupo=None, **kw):
        advisor, denied = self._guard()
        if denied:
            return denied
        return self._render('red_seguro_scout.advisor_import', advisor, 'insured',
                            import_groups=self._groups(advisor), grupo=grupo,
                            batches=self._advisor_batches(advisor))

    @http.route(['/seguro/cargar'], type='http', auth='user', website=True, methods=['POST'], sitemap=False)
    def advisor_import_upload(self, **post):
        advisor, denied = self._guard()
        if denied:
            return denied
        if not advisor.can_register_insured:
            return self._back('/seguro/cargar', error=_('No tienes permiso para cargar asegurados.'))
        upload = request.httprequest.files.get('file')
        if not upload or not upload.filename:
            return self._back('/seguro/cargar', error=_('Elige un archivo Excel (.xlsx) o PDF.'))
        data = upload.read()
        if len(data) > MAX_UPLOAD:
            return self._back('/seguro/cargar', error=_('El archivo pesa más de 25 MB.'))
        groups = self._groups(advisor)
        group = groups.filtered(lambda g: str(g.id) == (post.get('group_id') or ''))
        try:
            batches, unmatched = request.env['rss.import.batch'].sudo().create_office_batches(
                groups, upload.filename, data, default_group=group or None, advisor=advisor)
        except (UserError, ValidationError) as exc:
            return self._back('/seguro/cargar', error=str(exc))
        note = ''
        if unmatched:
            note = _(' %s personas no se cargaron porque su grupo no es uno de los tuyos o no se reconoce.',
                     len(unmatched))
        if len(batches) == 1:
            return self._back('/seguro/cargas/%s' % batches.id,
                              ok=_('Lista leída. Revisa y guarda en preinscripción.') + note)
        return self._back('/seguro/cargar', ok=_('Lista leída en %s cargas, una por grupo. Revisa cada una.',
                                                 len(batches)) + note)

    @http.route(['/seguro/cargas/<int:batch_id>'], type='http', auth='user', website=True, methods=['GET'],
                sitemap=False)
    def advisor_batch(self, batch_id, **kw):
        advisor, denied = self._guard()
        if denied:
            return denied
        batch = self._advisor_batch(advisor, batch_id)
        if not batch:
            return request.redirect('/seguro/cargar')
        return self._render('red_seguro_scout.advisor_import_batch', advisor, 'insured', batch=batch)

    @http.route(['/seguro/cargas/<int:batch_id>'], type='http', auth='user', website=True, methods=['POST'],
                sitemap=False)
    def advisor_batch_action(self, batch_id, **post):
        advisor, denied = self._guard()
        if denied:
            return denied
        url = '/seguro/cargas/%s' % batch_id
        batch = self._advisor_batch(advisor, batch_id)
        if not batch or not batch.office_load or batch.state != 'review':
            return self._back('/seguro/cargar', error=_('Esta carga ya no se puede modificar.'))
        for line in batch.line_ids:
            line.include = bool(post.get('l%s_include' % line.id))
        try:
            if post.get('action') == 'save':
                count = batch.action_save_draft()
                return self._back('/seguro/asegurados?grupo=%s&estatus=draft' % batch.group_id.id,
                                  ok=_('%(n)s personas guardadas en preinscripción en %(g)s.',
                                       n=count, g=batch.group_id.name))
            if post.get('action') == 'cancel':
                batch.action_cancel()
                return self._back('/seguro/cargar', ok=_('Carga cancelada.'))
            batch.action_validate()
        except (UserError, ValidationError) as exc:
            return self._back(url, error=str(exc))
        return self._back(url, ok=_('Lista revisada de nuevo.'))

    @http.route(['/seguro/plantilla.xlsx'], type='http', auth='user', sitemap=False)
    def advisor_template(self, **kw):
        advisor = request.env['rss.advisor']._for_user(request.env.user)
        if not advisor:
            return request.not_found()
        names = self._groups(advisor).sorted(lambda g: (g.number or 999, g.name)).mapped('name')
        return _xlsx(build_template_xlsx(with_group=True, group_names=names),
                     'Plantilla_asegurados_varios_grupos.xlsx')


class RssBackendTemplate(http.Controller):

    @http.route(['/red-seguro-scout/plantilla.xlsx'], type='http', auth='user', sitemap=False)
    def backend_template(self, **kw):
        if not request.env.user.has_group('red_seguro_scout.group_rss_user'):
            return request.not_found()
        names = request.env['rss.scout.group'].search([]).sorted(lambda g: (g.number or 999, g.name)).mapped('name')
        return _xlsx(build_template_xlsx(with_group=True, group_names=names),
                     'Plantilla_asegurados_varios_grupos.xlsx')
