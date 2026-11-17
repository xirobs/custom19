# -*- coding: utf-8 -*-
"""Pagos con Mercado Pago (módulo oficial de Odoo) para las cargas del portal del grupo."""
import logging
import re

from odoo import models

_logger = logging.getLogger(__name__)

REFERENCE_RE = re.compile(r'^RSS-G\d+-(\d+)')


TEST_PARAM = 'red_seguro_scout.accept_test_payments'


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _rss_is_test_payment(self, record=None):
        """Pago hecho con el proveedor en modo de prueba (sandbox): no entra dinero real, así que no se
        registra (salvo que el parámetro red_seguro_scout.accept_test_payments lo permita)."""
        self.ensure_one()
        if self.provider_id.state != 'test':
            return False
        if self.env['ir.config_parameter'].sudo().get_param(TEST_PARAM):
            return False
        if record is not None:
            record.message_post(body='Pago de PRUEBA %s (Mercado Pago en modo de prueba): no entró dinero real '
                                     'y NO se registró. Pon Mercado Pago en modo «Activado» para cobrar.'
                                     % self.reference)
        _logger.warning('Red Seguro Scout: transacción de prueba %s ignorada', self.reference)
        return True

    def _post_process(self):
        """Al confirmarse el pago, la carga se marca pagada y se crean los asegurados."""
        res = super()._post_process()
        self._rss_close_import_batches()
        return res

    def _rss_close_import_batches(self):
        for tx in self.filtered(lambda t: t.state == 'done' and t.reference):
            match = REFERENCE_RE.match(tx.reference)
            if not match:
                continue
            batch = self.env['rss.import.batch'].sudo().browse(int(match.group(1))).exists()
            if not batch:
                continue
            if tx._rss_is_test_payment(batch):
                continue
            if tx.currency_id.compare_amounts(tx.amount, batch.amount_mp_total) < 0:
                batch.message_post(body='Pago de Mercado Pago %s por un importe menor al esperado (%s); '
                                        'revísalo manualmente.' % (tx.reference, tx.amount))
                continue
            try:
                batch._mercadopago_paid(tx)
            except Exception:  # noqa: BLE001 - el pago ya se cobró; se revisa a mano
                _logger.exception('Red Seguro Scout: no se pudo cerrar la carga %s', batch.id)
                batch.message_post(body='El pago %s se recibió pero no se pudo cerrar la carga '
                                        'automáticamente; revísala.' % tx.reference)


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    def _get_compatible_providers(self, *args, rss_mercado_pago_only=False, **kwargs):
        """En el cobro de cargas del portal solo se ofrece Mercado Pago (la comisión ya va calculada
        para ese medio; la transferencia tiene su propio flujo con comprobante)."""
        providers = super()._get_compatible_providers(*args, **kwargs)
        if rss_mercado_pago_only:
            providers = providers.filtered(lambda p: p.code == 'mercado_pago')
        return providers
