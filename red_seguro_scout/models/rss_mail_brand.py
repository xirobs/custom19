# -*- coding: utf-8 -*-
"""Correos y avisos del seguro firmados como Red Seguro Scout (no con el nombre de la compañía
de Odoo, que puede ser el de un grupo, p. ej. «Scouts Grupo 7 Ethnos")."""
from email.utils import formataddr

from odoo import api, models

RSS_BRAND_NAME = 'Red Seguro Scout'
RSS_MAIL_LAYOUT = 'red_seguro_scout.mail_notification_rss'


class RssMailBrandMixin(models.AbstractModel):
    _name = 'rss.mail.brand.mixin'
    _description = 'Correos con la identidad de Red Seguro Scout'

    @api.model
    def _rss_email_from(self):
        company = self.env.company
        email = company.email or (company.partner_id.email if company.partner_id else False)
        return formataddr((RSS_BRAND_NAME, email)) if email else False


def _rss_brand_model(cls_name, model_name):
    """Clase que hereda el modelo y fija el marco y el remitente de Red Seguro Scout en sus avisos.
    (El método va en la propia clase: en un mixin quedaría detrás del de mail.thread y no se usaría.)"""

    def message_post(self, **kwargs):
        if not kwargs.get('email_layout_xmlid'):
            kwargs['email_layout_xmlid'] = RSS_MAIL_LAYOUT
        if not kwargs.get('email_from'):
            sender = self._rss_email_from()
            if sender:
                kwargs['email_from'] = sender
        return super(cls, self).message_post(**kwargs)

    cls = type(cls_name, (models.Model,), {
        '_name': model_name, '_inherit': [model_name, 'rss.mail.brand.mixin'], '__module__': __name__,
        'message_post': message_post})
    return cls


# Todos los registros del seguro que envían avisos usan la identidad de la Red
for _model in ('rss.advisor', 'rss.certificate.batch', 'rss.claim', 'rss.group.cluster', 'rss.group.member',
               'rss.import.batch', 'rss.insured', 'rss.payment', 'rss.policy', 'rss.report', 'rss.scout.group',
               'rss.service.charge'):
    _cls_name = 'RssMailBrand_' + _model.replace('.', '_')
    globals()[_cls_name] = _rss_brand_model(_cls_name, _model)


RSS_PAY_PREFIXES = ('RSS-', 'RSP-', 'RSV-')


class RssPaymentPageBrand(models.AbstractModel):
    _name = 'rss.pay.brand'
    _description = 'Página de pago de Odoo con la identidad de Red Seguro Scout'

    @api.model
    def _is_rss_payment_page(self):
        """True en /payment/... cuando el cobro es del seguro (referencia RSS-, RSP- o RSV-)."""
        from odoo.http import request
        if not request or not request.httprequest.path.startswith('/payment/'):
            return False
        reference = request.params.get('reference') or ''
        if not reference:
            tx_id = request.session.get('__payment_monitored_tx_id__')
            if tx_id:
                tx = self.env['payment.transaction'].sudo().browse(tx_id).exists()
                reference = tx.reference or ''
        return reference.upper().startswith(RSS_PAY_PREFIXES)
