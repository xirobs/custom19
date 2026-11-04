# -*- coding: utf-8 -*-
"""Portal del grupo (/mi-grupo) para jefe de grupo, secretario y tesorero.

Cada cuenta pertenece a un solo grupo y solo ve los registros de ese grupo. Los usuarios
son de portal: todo se lee con sudo y SIEMPRE filtrado por su grupo. Solo el capturista
del grupo puede cargar asegurados (Excel o PDF).
"""
import base64
import logging
from datetime import date
from urllib.parse import urlencode

from odoo import _, fields, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import content_disposition, request
from odoo.addons.portal.controllers.portal import CustomerPortal

from ..models.rss_parsers import build_template_xlsx, parse_date

_logger = logging.getLogger(__name__)

PAGE_SIZE = 50
MAX_UPLOAD = 15 * 1024 * 1024
EDITABLE = ('first_name', 'second_name', 'last_name_father', 'last_name_mother', 'birthdate', 'sex',
            'tutor_name', 'tutor_phone', 'tutor_email')


class RssGroupPortal(http.Controller):

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _member():
        return request.env['rss.group.member']._for_user(request.env.user)

    def _base_values(self):
        values = CustomerPortal()._prepare_portal_layout_values()
        values.update({'page_name': 'rss_group', 'no_breadcrumbs': True})
        return values

    def _guard(self):
        member = self._member()
        if not member:
            values = self._base_values()
            values.update({'member': False, 'company': request.env.company.sudo()})
            return None, request.render('red_seguro_scout.group_no_access', values)
        return member, None

    @staticmethod
    def _back(url, error=None, ok=None):
        params = {k: v for k, v in (('error', error), ('ok', ok)) if v}
        return request.redirect(url + ('?' + urlencode(params) if params else ''))

    def _render(self, template, member, section, **values):
        sudo_env = request.env(su=True)
        base = self._base_values()
        base.update({
            'member': member,
            'group': member.group_id.sudo(),
            'company': member.company_id.sudo(),
            'group_page': section,
            'error': request.params.get('error'),
            'ok': request.params.get('ok'),
            'fmt_date': lambda d: d.strftime('%d/%m/%Y') if d else '',
            'money': lambda v: '$ {:,.2f}'.format(v or 0.0),
            'labels': lambda model, field: dict(sudo_env[model]._fields[field]._description_selection(sudo_env)),
            'capture_open': self._capture_window(member)[0],
            'capture_msg': self._capture_window(member)[1],
        })
        base.update(values)
        return request.render(template, base)

    @staticmethod
    def _capture_window(member):
        """(abierta, mensaje) según el calendario de captura de la póliza del grupo."""
        policy = member.group_id.sudo().policy_id
        if not policy or not policy.capture_window_enforced:
            return True, ''
        today = fields.Date.context_today(member)
        start, end = policy.capture_day_from or 1, policy.capture_day_to or 10
        if start <= today.day <= end:
            return True, _('La captura de este mes cierra el día %s.', end)
        return False, _('La captura está abierta del día %(a)s al %(b)s de cada mes.', a=start, b=end)

    @staticmethod
    def _insured_domain(member):
        return [('group_id', '=', member.group_id.id)]

    # ------------------------------------------------------------------ aviso de privacidad (público)
    @http.route(['/aviso-de-privacidad-seguro'], type='http', auth='public', website=True, sitemap=True)
    def privacy_notice(self, **kw):
        values = self._base_values()
        values.update({'company': request.env.company.sudo(), 'member': False})
        return request.render('red_seguro_scout.privacy_notice_page', values)

    # ------------------------------------------------------------------ inicio
    @http.route(['/mi-grupo'], type='http', auth='user', website=True, sitemap=False)
    def group_home(self, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        env = request.env(su=True)
        dom = self._insured_domain(member)
        Insured = env['rss.insured']
        stats = {
            'active': Insured.search_count(dom + [('state', '=', 'active')]),
            'draft': Insured.search_count(dom + [('state', '=', 'draft')]),
            'certificates': Insured.search_count(dom + [('has_certificate', '=', True)]),
            'claims': env['rss.claim'].search_count([('group_id', '=', member.group_id.id),
                                                     ('state', 'in', ('reported', 'in_process', 'approved'))]),
            'male': Insured.search_count(dom + [('state', '=', 'active'), ('sex', '=', 'male')]),
            'female': Insured.search_count(dom + [('state', '=', 'active'), ('sex', '=', 'female')]),
        }
        team = env['rss.group.member'].search([('group_id', '=', member.group_id.id), ('active', '=', True)])
        batches = env['rss.import.batch'].search([('group_id', '=', member.group_id.id)], limit=5)
        month_start = fields.Date.context_today(member).replace(day=1)
        report = env['rss.report'].search([('company_id', '=', member.company_id.id), ('state', '!=', 'draft'),
                                           ('period_date', '>=', month_start)], limit=1)
        report_stats = report.with_context(rss_only_group_ids=member.group_id.ids)._export_stats() if report else {}
        return self._render('red_seguro_scout.group_home', member, 'home', stats=stats, team=team,
                            batches=batches, report=report, report_stats=report_stats)

    # ------------------------------------------------------------------ asegurados
    @http.route(['/mi-grupo/asegurados'], type='http', auth='user', website=True, sitemap=False)
    def group_insured(self, q=None, estatus='all', origen='all', page=1, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        env = request.env(su=True)
        domain = self._insured_domain(member)
        if origen == 'platform':
            domain.append(('capture_source', 'in', ('backend', False)))
        elif origen == 'portal':
            domain.append(('capture_source', 'in', ('portal', 'excel', 'pdf')))
        if estatus in ('active', 'draft', 'inactive'):
            domain.append(('state', '=', estatus))
        elif estatus == 'certificate':
            domain.append(('has_certificate', '=', True))
        elif estatus == 'no_certificate':
            domain += [('has_certificate', '=', False), ('state', '=', 'active')]
        if q:
            domain += ['|', ('name', 'ilike', q), ('tutor_name', 'ilike', q)]
        try:
            page = max(int(page or 1), 1)
        except ValueError:
            page = 1
        total = env['rss.insured'].search_count(domain)
        insured = env['rss.insured'].search(domain, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
        # Personas que se cargaron en el portal pero aún no quedan aseguradas (falta pagar o validar)
        pending = env['rss.import.line'].search([
            ('batch_id.group_id', '=', member.group_id.id),
            ('batch_id.state', 'in', ('review', 'payment', 'verifying')),
            ('include', '=', True), ('status', '!=', 'error'), ('insured_id', '=', False)],
            order='batch_id desc, sequence', limit=200)
        if q:
            qn = q.lower()
            pending = pending.filtered(lambda l: qn in ' '.join(
                x for x in (l.first_name, l.second_name, l.last_name_father, l.last_name_mother,
                            l.tutor_name) if x).lower())
        is_open, window_msg = self._capture_window(member)
        return self._render('red_seguro_scout.group_insured', member, 'insured', insured=insured,
                            q=q or '', estatus=estatus, origen=origen, page=page, total=total,
                            page_size=PAGE_SIZE, pending=pending, capture_open=is_open,
                            window_msg=window_msg)

    @http.route(['/mi-grupo/asegurados/<int:insured_id>/certificado'], type='http', auth='user', sitemap=False)
    def group_certificate(self, insured_id, **kw):
        member = self._member()
        if not member:
            return request.not_found()
        insured = request.env['rss.insured'].sudo().browse(insured_id).exists()
        if not insured or insured.group_id != member.group_id or not insured.certificate_attachment_id:
            return request.not_found()
        att = insured.certificate_attachment_id
        return request.make_response(att.raw or b'', headers=[
            ('Content-Type', 'application/pdf'), ('Content-Disposition', content_disposition(att.name))])

    # ------------------------------------------------------------------ carga de asegurados
    @http.route(['/mi-grupo/importar/plantilla.xlsx'], type='http', auth='user', sitemap=False)
    def group_template(self, **kw):
        member = self._member()
        if not member:
            return request.not_found()
        data = build_template_xlsx(member.group_id.name)
        return request.make_response(data, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition('Plantilla_asegurados_%s.xlsx' % (
                member.group_id.name or '').replace(' ', '_')))])

    @http.route(['/mi-grupo/importar'], type='http', auth='user', website=True, methods=['GET'], sitemap=False)
    def group_import_form(self, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        batches = request.env['rss.import.batch'].sudo().search([('group_id', '=', member.group_id.id)], limit=10)
        return self._render('red_seguro_scout.group_import', member, 'import', batches=batches)

    @http.route(['/mi-grupo/importar'], type='http', auth='user', website=True, methods=['POST'], sitemap=False)
    def group_import_upload(self, privacy=None, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        if not member.can_capture:
            return self._back('/mi-grupo/importar', error=_('Solo el capturista de tu grupo puede cargar asegurados.'))
        is_open, msg = self._capture_window(member)
        if not is_open:
            return self._back('/mi-grupo/importar', error=msg)
        if not privacy:
            return self._back('/mi-grupo/importar', error=_('Debes aceptar el aviso de privacidad para continuar.'))
        upload = request.httprequest.files.get('file')
        if not upload or not upload.filename:
            return self._back('/mi-grupo/importar', error=_('Elige el archivo Excel o PDF con la lista.'))
        data = upload.read()
        if len(data) > MAX_UPLOAD:
            return self._back('/mi-grupo/importar', error=_('El archivo pesa más de 15 MB.'))
        try:
            batch = request.env['rss.import.batch'].sudo().create_from_file(
                member.group_id.sudo(), upload.filename, data, member=member, privacy_accepted=True)
        except (UserError, ValidationError) as exc:
            return self._back('/mi-grupo/importar', error=str(exc))
        return request.redirect('/mi-grupo/importar/%s' % batch.id)

    def _batch_or_none(self, member, batch_id):
        batch = request.env['rss.import.batch'].sudo().browse(int(batch_id)).exists()
        if not batch or batch.group_id != member.group_id:
            return None
        return batch

    @http.route(['/mi-grupo/importar/<int:batch_id>'], type='http', auth='user', website=True,
                methods=['GET'], sitemap=False)
    def group_import_review(self, batch_id, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        batch = self._payable_batch(member, batch_id)
        if not batch:
            return request.redirect('/mi-grupo/importar')
        policy = member.group_id.sudo().policy_id
        _tariff, unit_today = policy._tariff_for(fields.Date.context_today(member)) if policy else (None, 0)
        return self._render('red_seguro_scout.group_import_review', member, 'import', batch=batch,
                            today=date.today(), policy=policy, unit_today=unit_today,
                            mp_available=self._mercadopago_available(batch),
                            money=lambda v: '$ {:,.2f}'.format(v or 0.0),
                            fmt_dt=lambda d: fields.Datetime.context_timestamp(member, d).strftime('%d/%m/%Y %H:%M') if d else '')

    @http.route(['/mi-grupo/importar/<int:batch_id>'], type='http', auth='user', website=True,
                methods=['POST'], sitemap=False)
    def group_import_save(self, batch_id, action='validate', **post):
        member, denied = self._guard()
        if denied:
            return denied
        url = '/mi-grupo/importar/%s' % batch_id
        batch = self._batch_or_none(member, batch_id)
        if not batch:
            return request.redirect('/mi-grupo/importar')
        if not member.can_capture:
            return self._back(url, error=_('Solo el capturista de tu grupo puede guardar asegurados.'))
        if action == 'cancel' and batch.state == 'payment':
            batch.action_cancel()
            return self._back('/mi-grupo/importar', ok=_('Carga cancelada; no se guardó a nadie.'))
        if batch.state != 'review':
            return self._back(url, error=_('Esta carga ya no se puede modificar.'))
        if action == 'cancel':
            batch.action_cancel()
            return self._back('/mi-grupo/importar', ok=_('Carga cancelada; no se guardó a nadie.'))
        # Correcciones hechas en la tabla
        for line in batch.line_ids:
            vals = {}
            for field in EDITABLE:
                key = 'l%s_%s' % (line.id, field)
                if key not in post:
                    continue
                value = (post.get(key) or '').strip()
                if field == 'birthdate':
                    vals[field] = parse_date(value) if value else False
                    vals['raw_birthdate'] = value
                elif field == 'sex':
                    vals[field] = value if value in ('male', 'female') else False
                else:
                    vals[field] = value or False
            vals['include'] = bool(post.get('l%s_include' % line.id))
            line.write(vals)
        # Filas totalmente vacías (captura manual): se quitan, dejando al menos una
        blank = batch.line_ids.filtered(lambda l: l._is_blank())
        if blank and len(blank) < len(batch.line_ids):
            blank.unlink()
        if action == 'add_row':
            batch.action_add_row()
            batch.action_validate()
            return request.redirect(url + '#fin')
        batch.action_validate()
        if action == 'confirm':
            is_open, msg = self._capture_window(member)
            if not is_open:
                return self._back(url, error=msg)
            try:
                batch.action_request_payment()
            except (UserError, ValidationError) as exc:
                return self._back(url, error=str(exc))
            return request.redirect(url)
        return self._back(url, ok=_('Revisión actualizada.'))

    # ------------------------------------------------------------------ captura manual
    @http.route(['/mi-grupo/capturar'], type='http', auth='user', website=True, methods=['POST'], sitemap=False)
    def group_manual(self, privacy=None, rows='3', **kw):
        member, denied = self._guard()
        if denied:
            return denied
        if not member.can_capture:
            return self._back('/mi-grupo/importar', error=_('Solo el capturista de tu grupo puede capturar asegurados.'))
        is_open, msg = self._capture_window(member)
        if not is_open:
            return self._back('/mi-grupo/importar', error=msg)
        if not privacy:
            return self._back('/mi-grupo/importar', error=_('Debes aceptar el aviso de privacidad para continuar.'))
        try:
            rows = min(max(int(rows), 1), 30)
        except ValueError:
            rows = 3
        batch = request.env['rss.import.batch'].sudo().create_manual(
            member.group_id.sudo(), member=member, privacy_accepted=True, rows=rows)
        return request.redirect('/mi-grupo/importar/%s' % batch.id)

    # ------------------------------------------------------------------ pago
    def _payable_batch(self, member, batch_id):
        batch = self._batch_or_none(member, batch_id)
        if batch and batch.state == 'payment' and fields.Datetime.now() > batch.payment_deadline:
            batch._expire()
        return batch

    @http.route(['/mi-grupo/importar/<int:batch_id>/regresar'], type='http', auth='user', website=True,
                methods=['POST'], sitemap=False)
    def group_back_to_review(self, batch_id, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        batch = self._payable_batch(member, batch_id)
        if batch and member.can_capture:
            batch.action_back_to_review()
        return request.redirect('/mi-grupo/importar/%s' % batch_id)

    @http.route(['/mi-grupo/importar/<int:batch_id>/transferencia'], type='http', auth='user', website=True,
                methods=['POST'], sitemap=False)
    def group_pay_transfer(self, batch_id, reference='', transfer_date='', **kw):
        member, denied = self._guard()
        if denied:
            return denied
        url = '/mi-grupo/importar/%s' % batch_id
        batch = self._payable_batch(member, batch_id)
        if not batch or not member.can_capture:
            return request.redirect('/mi-grupo/importar')
        files = []
        for upload in request.httprequest.files.getlist('proof'):
            if upload and upload.filename:
                data = upload.read()
                if len(data) > MAX_UPLOAD:
                    return self._back(url, error=_('El comprobante pesa más de 15 MB.'))
                files.append((upload.filename, data, upload.mimetype))
        try:
            batch.action_submit_transfer(reference, parse_date(transfer_date) if transfer_date else None, files)
        except (UserError, ValidationError) as exc:
            return self._back(url, error=str(exc))
        return self._back(url, ok=_('Recibimos tu comprobante. En cuanto se confirme el depósito, '
                                    'tus asegurados quedarán activos y te llegará el recibo.'))

    @http.route(['/mi-grupo/importar/<int:batch_id>/mercadopago'], type='http', auth='user', website=True,
                methods=['GET'], sitemap=False)
    def group_pay_mercadopago(self, batch_id, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        url = '/mi-grupo/importar/%s' % batch_id
        batch = self._payable_batch(member, batch_id)
        if not batch or not member.can_capture:
            return request.redirect('/mi-grupo/importar')
        if batch.state != 'payment':
            return self._back(url, error=_('Esta carga ya no está esperando pago.'))
        if not self._mercadopago_available(batch):
            return self._back(url, error=_('Mercado Pago no está disponible por ahora; paga por transferencia.'))
        from odoo.addons.payment import utils as payment_utils
        partner = request.env.user.partner_id
        amount = batch.amount_mp_total
        params = {
            'reference': batch.reference,
            'amount': amount,
            'currency_id': batch.currency_id.id,
            'partner_id': partner.id,
            'company_id': batch.company_id.id,
            'access_token': payment_utils.generate_access_token(partner.id, amount, batch.currency_id.id),
            'rss_mercado_pago_only': 1,
        }
        return request.redirect('/payment/pay?' + urlencode(params))

    @staticmethod
    def _mercadopago_available(batch):
        """Mercado Pago activo y compatible (empresa, moneda, monto) según las reglas de Odoo."""
        providers = request.env['payment.provider'].sudo()._get_compatible_providers(
            batch.company_id.id, request.env.user.partner_id.id, batch.amount_mp_total or 1.0,
            currency_id=batch.currency_id.id, rss_mercado_pago_only=True)
        return bool(providers)

    # ------------------------------------------------------------------ siniestros
    @http.route(['/mi-grupo/siniestros'], type='http', auth='user', website=True, sitemap=False)
    def group_claims(self, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        claims = request.env['rss.claim'].sudo().search([('group_id', '=', member.group_id.id)], limit=200)
        return self._render('red_seguro_scout.group_claims', member, 'claims', claims=claims)

    def _group_claim(self, member, claim_id):
        claim = request.env['rss.claim'].sudo().browse(int(claim_id)).exists()
        if not claim or claim.group_id != member.group_id:
            return None
        return claim

    @http.route(['/mi-grupo/siniestros/<int:claim_id>'], type='http', auth='user', website=True,
                methods=['GET'], sitemap=False)
    def group_claim(self, claim_id, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        claim = self._group_claim(member, claim_id)
        if not claim:
            return request.redirect('/mi-grupo/siniestros')
        messages = claim.message_ids.filtered(lambda m: m.message_type == 'comment')[:20]
        return self._render('red_seguro_scout.group_claim', member, 'claims', claim=claim, messages=messages,
                            states=('reported', 'in_process', 'approved', 'paid', 'rejected'))

    @http.route(['/mi-grupo/siniestros/<int:claim_id>'], type='http', auth='user', website=True,
                methods=['POST'], sitemap=False)
    def group_claim_update(self, claim_id, **post):
        """Seguimiento del siniestro por el representante (lo mismo que puede hacer el asesor)."""
        member, denied = self._guard()
        if denied:
            return denied
        url = '/mi-grupo/siniestros/%s' % claim_id
        claim = self._group_claim(member, claim_id)
        if not claim:
            return request.redirect('/mi-grupo/siniestros')
        vals = {}
        state = post.get('state')
        if state in ('reported', 'in_process', 'approved', 'paid', 'rejected') and state != claim.state:
            vals['state'] = state
        number = (post.get('insurer_claim_number') or '').strip()
        if number != (claim.insurer_claim_number or ''):
            vals['insurer_claim_number'] = number or False
        for key in ('amount_claimed', 'amount_paid'):
            raw = (post.get(key) or '').replace(',', '').replace('$', '').strip()
            if raw:
                try:
                    amount = float(raw)
                except ValueError:
                    return self._back(url, error=_('El monto "%s" no es un número.', raw))
                if amount < 0:
                    return self._back(url, error=_('Los montos no pueden ser negativos.'))
                if amount != claim[key]:
                    vals[key] = amount
        if vals.get('state') == 'paid' and not (vals.get('amount_paid') or claim.amount_paid):
            return self._back(url, error=_('Captura el monto pagado antes de marcarlo como pagado.'))
        note = (post.get('note') or '').strip()
        try:
            if vals:
                claim.write(vals)
            attachments = request.env['ir.attachment'].sudo()
            for upload in request.httprequest.files.getlist('documents'):
                if upload and upload.filename:
                    attachments |= attachments.create({'name': upload.filename, 'raw': upload.read(),
                                                       'mimetype': upload.mimetype, 'res_model': 'rss.claim',
                                                       'res_id': claim.id})
            if attachments:
                claim.attachment_ids = [fields.Command.link(a.id) for a in attachments]
            if note or vals or attachments:
                claim.message_post(body=note or _('Actualización del grupo.'), author_id=member.partner_id.id,
                                   message_type='comment', subtype_xmlid='mail.mt_note',
                                   attachment_ids=attachments.ids)
        except (UserError, ValidationError) as exc:
            return self._back(url, error=str(exc))
        return self._back(url, ok=_('Siniestro actualizado.'))

    @http.route(['/mi-grupo/siniestros/<int:claim_id>/documento/<int:attachment_id>'], type='http',
                auth='user', sitemap=False)
    def group_claim_document(self, claim_id, attachment_id, **kw):
        member = request.env['rss.group.member']._for_user(request.env.user)
        claim = self._group_claim(member, claim_id) if member else None
        att = claim and claim.attachment_ids.filtered(lambda a: a.id == attachment_id)
        if not att:
            return request.not_found()
        return request.make_response(att.raw, headers=[
            ('Content-Type', att.mimetype or 'application/octet-stream'),
            ('Content-Disposition', content_disposition(att.name))])

    # ------------------------------------------------------------------ relaciones (solo su grupo)
    def _group_reports(self, member):
        reports = request.env['rss.report'].sudo().search([
            ('company_id', '=', member.company_id.id), ('state', '!=', 'draft')], limit=36)
        return reports.filtered(lambda r: member.group_id in r.line_ids.group_id)

    @http.route(['/mi-grupo/relaciones'], type='http', auth='user', website=True, sitemap=False)
    def group_reports(self, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        reports = self._group_reports(member)
        stats = {r.id: r.with_context(rss_only_group_ids=member.group_id.ids)._export_stats() for r in reports}
        return self._render('red_seguro_scout.group_reports', member, 'reports', reports=reports, rstats=stats)

    def _group_report(self, member, report_id):
        report = request.env['rss.report'].sudo().browse(int(report_id)).exists()
        if not report or report not in self._group_reports(member):
            return None
        return report.with_context(rss_only_group_ids=member.group_id.ids)

    @http.route(['/mi-grupo/relaciones/<int:report_id>/excel'], type='http', auth='user', sitemap=False)
    def group_report_xlsx(self, report_id, **kw):
        member = request.env['rss.group.member']._for_user(request.env.user)
        report = self._group_report(member, report_id) if member else None
        if not report:
            return request.not_found()
        return request.make_response(report._build_xlsx(), headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition(report._get_xlsx_filename()))])

    @http.route(['/mi-grupo/relaciones/<int:report_id>/pdf'], type='http', auth='user', sitemap=False)
    def group_report_pdf(self, report_id, **kw):
        member = request.env['rss.group.member']._for_user(request.env.user)
        report = self._group_report(member, report_id) if member else None
        if not report:
            return request.not_found()
        pdf, _fmt = request.env['ir.actions.report'].sudo().with_context(
            rss_only_group_ids=member.group_id.ids)._render_qweb_pdf(
            'red_seguro_scout.action_report_relacion_mensual', report.ids)
        name = 'Relacion_%s_%s_%02d.pdf' % ((member.group_id.number or member.group_id.id),
                                             report.date_from.year, report.date_from.month)
        return request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'), ('Content-Disposition', content_disposition(name))])

    # ------------------------------------------------------------------ pagos (solo su grupo)
    @http.route(['/mi-grupo/pagos'], type='http', auth='user', website=True, sitemap=False)
    def group_payments(self, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        payments = request.env['rss.payment'].sudo().search([
            ('group_id', '=', member.group_id.id), ('state', 'in', ('confirmed', 'remitted'))], limit=100)
        pending = request.env['rss.import.batch'].sudo().search([
            ('group_id', '=', member.group_id.id), ('state', 'in', ('payment', 'verifying'))])
        return self._render('red_seguro_scout.group_payments', member, 'payments', payments=payments,
                            pending_batches=pending)

    @http.route(['/mi-grupo/pagos/<int:payment_id>/recibo'], type='http', auth='user', sitemap=False)
    def group_payment_receipt(self, payment_id, **kw):
        member = request.env['rss.group.member']._for_user(request.env.user)
        payment = request.env['rss.payment'].sudo().browse(payment_id).exists()
        if (not member or not payment or payment.group_id != member.group_id
                or payment.state not in ('confirmed', 'remitted')):
            return request.not_found()
        pdf, _fmt = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
            'red_seguro_scout.action_report_recibo_pago', payment.ids)
        name = 'Recibo_%s.pdf' % (payment.name or '').replace('/', '_')
        return request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'), ('Content-Disposition', content_disposition(name))])
