# -*- coding: utf-8 -*-
"""Portal del asesor de seguros (/seguro).

El asesor es un usuario de portal: no tiene permisos sobre los modelos, así que todo se
lee con sudo y SIEMPRE filtrado por los grupos que su registro de asesor le permite ver.
"""
import base64
import logging
from urllib.parse import urlencode

from odoo import _, fields, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import content_disposition, request
from odoo.addons.portal.controllers.portal import CustomerPortal

_logger = logging.getLogger(__name__)

PAGE_SIZE = 50
CLAIM_STATES = ('reported', 'in_process', 'approved', 'paid', 'rejected')


class RssAdvisorPortal(http.Controller):

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _advisor():
        return request.env['rss.advisor']._for_user(request.env.user)

    def _guard(self):
        """Asesor activo o página de 'sin acceso'."""
        advisor = self._advisor()
        if not advisor:
            values = CustomerPortal()._prepare_portal_layout_values()
            values.update({'page_name': 'rss_advisor', 'no_breadcrumbs': True, 'advisor': False})
            return None, request.render('red_seguro_scout.advisor_no_access', values)
        return advisor, None

    @staticmethod
    def _back(url, error=None, ok=None):
        params = {k: v for k, v in (('error', error), ('ok', ok)) if v}
        return request.redirect(url + ('?' + urlencode(params) if params else ''))

    def _render(self, template, advisor, section, **values):
        sudo_env = request.env(su=True)
        base = CustomerPortal()._prepare_portal_layout_values()
        base.update({
            'page_name': 'rss_advisor',
            'no_breadcrumbs': True,
            'advisor': advisor,
            'advisor_page': section,
            'error': request.params.get('error'),
            'ok': request.params.get('ok'),
            'money': lambda v: '$ {:,.2f}'.format(v or 0.0),
            'fmt_date': lambda d: d.strftime('%d/%m/%Y') if d else '',
            'labels': lambda model, field: dict(sudo_env[model]._fields[field]._description_selection(sudo_env)),
        })
        base.update(values)
        return request.render(template, base)

    @staticmethod
    def _groups(advisor):
        return advisor._allowed_groups()

    def _claims_domain(self, advisor):
        return [('group_id', 'in', self._groups(advisor).ids)]

    def _report_allowed(self, advisor, report):
        """Un asesor limitado a ciertos grupos no descarga relaciones con otros grupos."""
        if report.company_id != advisor.company_id:
            return False
        if not advisor._is_restricted():
            return True
        return not (report.line_ids.group_id - self._groups(advisor))

    # ------------------------------------------------------------------ inicio
    @http.route(['/seguro'], type='http', auth='user', website=True, sitemap=False)
    def advisor_home(self, **kw):
        advisor, denied = self._guard()
        if denied:
            return denied
        env = request.env(su=True)
        groups = self._groups(advisor)
        today = fields.Date.context_today(advisor)
        month_start = today.replace(day=1)
        report = env['rss.report'].search([('company_id', '=', advisor.company_id.id),
                                           ('period_date', '>=', month_start)], limit=1)
        stats = {
            'insured': env['rss.insured'].search_count([('group_id', 'in', groups.ids),
                                                         ('state', '=', 'active')]),
            'groups': len(groups),
            'claims_open': env['rss.claim'].search_count(self._claims_domain(advisor) + [
                ('state', 'in', ('reported', 'in_process', 'approved'))]),
            'unpaid': len(groups.filtered(lambda g: not g.paid_current_year)),
        }
        claims = env['rss.claim'].search(self._claims_domain(advisor) + [
            ('state', 'in', ('reported', 'in_process', 'approved'))], limit=5)
        return self._render('red_seguro_scout.advisor_home', advisor, 'home', stats=stats,
                            report=report, claims=claims, groups=groups)

    # ------------------------------------------------------------------ asegurados
    @http.route(['/seguro/asegurados'], type='http', auth='user', website=True, sitemap=False)
    def advisor_insured(self, grupo=None, q=None, estatus='active', page=1, **kw):
        advisor, denied = self._guard()
        if denied:
            return denied
        env = request.env(su=True)
        groups = self._groups(advisor)
        domain = [('group_id', 'in', groups.ids)]
        group = groups.filtered(lambda g: str(g.id) == str(grupo or ''))
        if group:
            domain.append(('group_id', '=', group.id))
        if estatus in ('active', 'draft', 'inactive'):
            domain.append(('state', '=', estatus))
        if q:
            domain += ['|', '|', ('name', 'ilike', q), ('curp', 'ilike', q), ('tutor_name', 'ilike', q)]
        page = max(int(page or 1), 1)
        total = env['rss.insured'].search_count(domain)
        insured = env['rss.insured'].search(domain, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
        return self._render('red_seguro_scout.advisor_insured', advisor, 'insured',
                            insured=insured, groups=groups, group=group, q=q or '',
                            estatus=estatus, page=page, total=total, page_size=PAGE_SIZE)

    # ------------------------------------------------------------------ relaciones
    @http.route(['/seguro/relaciones'], type='http', auth='user', website=True, sitemap=False)
    def advisor_reports(self, **kw):
        advisor, denied = self._guard()
        if denied:
            return denied
        reports = request.env['rss.report'].sudo().search([
            ('company_id', '=', advisor.company_id.id), ('state', '!=', 'draft')], limit=36)
        reports = reports.filtered(lambda r: self._report_allowed(advisor, r))
        return self._render('red_seguro_scout.advisor_reports', advisor, 'reports', reports=reports)

    def _report_or_none(self, advisor, report_id):
        report = request.env['rss.report'].sudo().browse(int(report_id)).exists()
        if not report or report.state == 'draft' or not self._report_allowed(advisor, report):
            return None
        return report

    @http.route(['/seguro/relaciones/<int:report_id>/excel'], type='http', auth='user', sitemap=False)
    def advisor_report_xlsx(self, report_id, **kw):
        advisor = self._advisor()
        if not advisor:
            return request.not_found()
        report = self._report_or_none(advisor, report_id)
        if not report:
            return request.not_found()
        data = report._build_xlsx()
        return request.make_response(data, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition(report._get_xlsx_filename())),
        ])

    @http.route(['/seguro/relaciones/<int:report_id>/pdf'], type='http', auth='user', sitemap=False)
    def advisor_report_pdf(self, report_id, **kw):
        advisor = self._advisor()
        if not advisor:
            return request.not_found()
        report = self._report_or_none(advisor, report_id)
        if not report:
            return request.not_found()
        pdf, _fmt = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
            'red_seguro_scout.action_report_relacion_mensual', report.ids)
        name = 'Relacion_asegurados_%s_%02d.pdf' % (report.date_from.year, report.date_from.month)
        return request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'), ('Content-Disposition', content_disposition(name))])

    @http.route(['/seguro/relaciones/<int:report_id>/acuse'], type='http', auth='user', website=True,
                methods=['POST'], sitemap=False)
    def advisor_report_ack(self, report_id, insurer_ack='', **kw):
        advisor, denied = self._guard()
        if denied:
            return denied
        report = self._report_or_none(advisor, report_id)
        if not report or not advisor.can_ack_reports:
            return self._back('/seguro/relaciones', error=_('No puedes registrar el acuse de esa relación.'))
        insurer_ack = (insurer_ack or '').strip()
        if not insurer_ack:
            return self._back('/seguro/relaciones', error=_('Escribe el folio o acuse de la aseguradora.'))
        if report.state not in ('sent', 'acknowledged'):
            return self._back('/seguro/relaciones', error=_('La relación aún no se ha enviado a la aseguradora.'))
        report.write({'insurer_ack': insurer_ack, 'state': 'acknowledged'})
        report.message_post(body=_('Acuse registrado por el asesor %(name)s: %(ack)s',
                                   name=advisor.name, ack=insurer_ack),
                            author_id=advisor.partner_id.id)
        return self._back('/seguro/relaciones', ok=_('Acuse registrado en %s.', report.name))

    # ------------------------------------------------------------------ pagos
    @http.route(['/seguro/pagos'], type='http', auth='user', website=True, sitemap=False)
    def advisor_payments(self, **kw):
        advisor, denied = self._guard()
        if denied:
            return denied
        groups = self._groups(advisor)
        payments = request.env['rss.payment'].sudo().search([
            ('group_id', 'in', groups.ids), ('state', 'in', ('confirmed', 'remitted'))], limit=100)
        return self._render('red_seguro_scout.advisor_payments', advisor, 'payments',
                            payments=payments, groups=groups)

    @http.route(['/seguro/pagos/<int:payment_id>/recibo'], type='http', auth='user', sitemap=False)
    def advisor_payment_receipt(self, payment_id, **kw):
        advisor = self._advisor()
        if not advisor:
            return request.not_found()
        payment = request.env['rss.payment'].sudo().browse(payment_id).exists()
        if (not payment or payment.group_id not in self._groups(advisor)
                or payment.state not in ('confirmed', 'remitted')):
            return request.not_found()
        pdf, _fmt = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
            'red_seguro_scout.action_report_recibo_pago', payment.ids)
        name = 'Recibo_%s.pdf' % (payment.name or '').replace('/', '_')
        return request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'), ('Content-Disposition', content_disposition(name))])

    # ------------------------------------------------------------------ siniestros
    @http.route(['/seguro/siniestros'], type='http', auth='user', website=True, sitemap=False)
    def advisor_claims(self, estatus='open', **kw):
        advisor, denied = self._guard()
        if denied:
            return denied
        domain = self._claims_domain(advisor)
        if estatus == 'open':
            domain.append(('state', 'in', ('reported', 'in_process', 'approved')))
        elif estatus in CLAIM_STATES:
            domain.append(('state', '=', estatus))
        claims = request.env['rss.claim'].sudo().search(domain, limit=100)
        return self._render('red_seguro_scout.advisor_claims', advisor, 'claims',
                            claims=claims, estatus=estatus)

    def _claim_or_none(self, advisor, claim_id):
        claim = request.env['rss.claim'].sudo().browse(int(claim_id)).exists()
        if not claim or claim.group_id not in self._groups(advisor):
            return None
        return claim

    @http.route(['/seguro/siniestros/<int:claim_id>'], type='http', auth='user', website=True,
                methods=['GET'], sitemap=False)
    def advisor_claim(self, claim_id, **kw):
        advisor, denied = self._guard()
        if denied:
            return denied
        claim = self._claim_or_none(advisor, claim_id)
        if not claim:
            return request.redirect('/seguro/siniestros')
        messages = claim.message_ids.filtered(lambda m: m.message_type == 'comment')[:20]
        return self._render('red_seguro_scout.advisor_claim', advisor, 'claims', claim=claim,
                            messages=messages, states=CLAIM_STATES)

    @http.route(['/seguro/siniestros/<int:claim_id>'], type='http', auth='user', website=True,
                methods=['POST'], sitemap=False)
    def advisor_claim_update(self, claim_id, **post):
        advisor, denied = self._guard()
        if denied:
            return denied
        url = '/seguro/siniestros/%s' % claim_id
        claim = self._claim_or_none(advisor, claim_id)
        if not claim:
            return request.redirect('/seguro/siniestros')
        if not advisor.can_update_claims:
            return self._back(url, error=_('Tu acceso es solo de consulta.'))
        vals = {}
        state = post.get('state')
        if state in CLAIM_STATES and state != claim.state:
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
        files = request.httprequest.files.getlist('documents')
        note = (post.get('note') or '').strip()
        try:
            if vals:
                claim.write(vals)
            attachments = request.env['ir.attachment'].sudo()
            for upload in files:
                if not upload or not upload.filename:
                    continue
                attachments |= attachments.create({
                    'name': upload.filename,
                    'raw': upload.read(),
                    'mimetype': upload.mimetype,
                    'res_model': 'rss.claim',
                    'res_id': claim.id,
                })
            if attachments:
                claim.attachment_ids = [fields.Command.link(a.id) for a in attachments]
            if note or vals or attachments:
                body = note or _('Actualización del asesor.')
                claim.message_post(body=body, author_id=advisor.partner_id.id,
                                   message_type='comment', subtype_xmlid='mail.mt_note',
                                   attachment_ids=attachments.ids)
        except (UserError, ValidationError) as exc:
            return self._back(url, error=str(exc))
        return self._back(url, ok=_('Siniestro actualizado.'))

    @http.route(['/seguro/siniestros/<int:claim_id>/documento/<int:attachment_id>'], type='http',
                auth='user', sitemap=False)
    def advisor_claim_document(self, claim_id, attachment_id, **kw):
        advisor = self._advisor()
        if not advisor:
            return request.not_found()
        claim = self._claim_or_none(advisor, claim_id)
        attachment = request.env['ir.attachment'].sudo().browse(attachment_id).exists()
        if not claim or attachment not in claim.attachment_ids:
            return request.not_found()
        return request.make_response(attachment.raw or b'', headers=[
            ('Content-Type', attachment.mimetype or 'application/octet-stream'),
            ('Content-Disposition', content_disposition(attachment.name))])
