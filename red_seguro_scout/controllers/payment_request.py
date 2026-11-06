# -*- coding: utf-8 -*-
"""Liga de pago que reciben los representantes del grupo (correo o WhatsApp):
pagar con Mercado Pago o subir el comprobante de transferencia, sin iniciar sesión."""
import base64
import hmac
from urllib.parse import urlencode

from odoo import _, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import content_disposition, request

MAX_UPLOAD = 15 * 1024 * 1024


class RssPaymentRequestPortal(http.Controller):

    @staticmethod
    def _payment(payment_id, token):
        pay = request.env['rss.payment'].sudo().browse(int(payment_id)).exists()
        if not pay or not pay.access_token or not token or not hmac.compare_digest(pay.access_token, str(token)):
            return None
        return pay

    def _page(self, pay, error=None, ok=None):
        return request.render('red_seguro_scout.payment_request_page', {
            'pay': pay, 'policy': pay.policy_id if pay else False, 'error': error, 'ok': ok,
            'mp_available': bool(pay) and pay.state == 'draft' and pay._mercadopago_available(),
            'money': lambda v: '$ {:,.2f}'.format(v or 0),
            'page_name': 'rss_payment_request',
        })

    @http.route(['/seguro/pago/<int:payment_id>/<string:token>'], type='http', auth='public', website=True,
                methods=['GET'], sitemap=False)
    def payment_request(self, payment_id, token, **kw):
        pay = self._payment(payment_id, token)
        status = kw.get('status')
        return self._page(pay, ok=_('Recibimos tu comprobante. En cuanto se verifique el depósito '
                                    'se emitirá tu recibo.') if status == 'enviado' else None)

    @http.route(['/seguro/pago/<int:payment_id>/<string:token>/comprobante'], type='http', auth='public',
                website=True, methods=['POST'], sitemap=False)
    def payment_request_proof(self, payment_id, token, reference='', tracking_key='', **kw):
        pay = self._payment(payment_id, token)
        if not pay:
            return self._page(pay)
        files = []
        for upload in request.httprequest.files.getlist('proof'):
            if upload and upload.filename:
                data = upload.read()
                if len(data) > MAX_UPLOAD:
                    return self._page(pay, error=_('El comprobante pesa más de 15 MB.'))
                files.append((upload.filename, base64.b64encode(data), upload.mimetype))
        try:
            pay._group_submit_transfer(reference, tracking_key, files)
        except (UserError, ValidationError) as exc:
            return self._page(pay, error=str(exc))
        return request.redirect('/seguro/pago/%s/%s?status=enviado' % (pay.id, token))

    @http.route(['/seguro/pago/<int:payment_id>/<string:token>/mercadopago'], type='http', auth='public',
                website=True, methods=['GET'], sitemap=False)
    def payment_request_mercadopago(self, payment_id, token, **kw):
        pay = self._payment(payment_id, token)
        if not pay or pay.state != 'draft':
            return self._page(pay, error=_('Este pago ya no está pendiente.') if pay else None)
        if not pay._mercadopago_available():
            return self._page(pay, error=_('Mercado Pago no está disponible por ahora; paga por transferencia.'))
        from odoo.addons.payment import utils as payment_utils
        partner = (pay.representative_ids[:1] or pay.group_id.sudo().portal_member_ids[:1].partner_id
                   or request.env.user.partner_id)
        if not request.env.user._is_public():
            partner = request.env.user.partner_id
        amount = pay.amount_mp_total
        params = {
            'reference': pay.request_reference,
            'amount': amount,
            'currency_id': pay.currency_id.id,
            'partner_id': partner.id,
            'company_id': pay.company_id.id,
            'access_token': payment_utils.generate_access_token(partner.id, amount, pay.currency_id.id),
            'rss_mercado_pago_only': 1,
        }
        return request.redirect('/payment/pay?' + urlencode(params))

    @http.route(['/seguro/pago/<int:payment_id>/<string:token>/recibo'], type='http', auth='public',
                sitemap=False)
    def payment_request_receipt(self, payment_id, token, **kw):
        pay = self._payment(payment_id, token)
        if not pay or pay.state not in ('confirmed', 'remitted'):
            return request.not_found()
        pdf, _fmt = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
            'red_seguro_scout.action_report_recibo_pago', pay.ids)
        name = 'Recibo_%s.pdf' % (pay.name or '').replace('/', '_')
        return request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'), ('Content-Disposition', content_disposition(name))])
