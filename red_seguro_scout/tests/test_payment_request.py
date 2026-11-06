# -*- coding: utf-8 -*-
import base64

from odoo.tests import HttpCase, tagged

from .test_group_portal import _RssPortalCommon


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestPaymentRequest(_RssPortalCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()
        cls.policy.write({'bank_beneficiary': 'Red Seguro Scout AC', 'bank_name': 'BBVA',
                          'bank_clabe': '012180001234567891', 'bank_notes': 'Envía tu comprobante.'})
        cls.tesorero = cls._member(cls.g7, 'treasurer', 'tesorero.g7@example.com')

    def _request(self):
        pay = self.env['rss.payment'].create({'group_id': self.g7.id, 'amount': 0, 'year': 2026})
        self.assertEqual(pay.member_ids, self.maria | self.jose)
        return pay

    def test_01_send_by_email_and_whatsapp(self):
        pay = self._request()
        action = pay.action_send_payment_request()
        self.assertTrue(pay.access_token)
        self.assertEqual(pay.request_reference, 'RSP-G7-%s' % pay.id)
        self.assertIn(self.tesorero.partner_id.id, action['context']['default_partner_ids'])
        body = str(action['context']['default_body'])
        self.assertIn('012180001234567891', body)
        self.assertIn(pay.payment_url, body)
        self.assertIn('200.00', body)
        # Enviar el correo marca la fecha de envío
        composer = self.env['mail.compose.message'].with_context(action['context']).create({})
        composer._action_send_mail()
        self.assertTrue(pay.request_sent_date)
        self.assertIn(self.tesorero.partner_id, pay.message_ids[:1].partner_ids)
        wa = pay.action_send_payment_whatsapp()
        self.assertTrue(wa['url'].startswith('https://wa.me/'))
        self.assertIn('012180001234567891', pay._request_text())

    def test_02_public_page_and_transfer_proof(self):
        pay = self._request()
        pay.action_send_payment_request()
        self.authenticate(None, None)
        res = self.url_open('/seguro/pago/%s/%s' % (pay.id, pay.access_token))
        self.assertEqual(res.status_code, 200)
        self.assertIn('012180001234567891', res.text)
        self.assertIn(pay.request_reference, res.text)
        bad = self.url_open('/seguro/pago/%s/%s' % (pay.id, 'x' * 24))
        self.assertIn('no existe', bad.text)
        res = self.url_open('/seguro/pago/%s/%s/comprobante' % (pay.id, pay.access_token),
                            data={'csrf_token': self._csrf(res.text), 'reference': '0098765',
                                  'tracking_key': 'MBAN01002611060012345'},
                            files={'proof': ('comprobante.jpg', b'\xff\xd8\xff fake', 'image/jpeg')})
        self.assertEqual(res.status_code, 200)
        pay.invalidate_recordset()
        self.assertTrue(pay.proof_submitted)
        self.assertEqual(pay.tracking_key, 'MBAN01002611060012345')
        self.assertEqual(len(pay.attachment_ids), 1)
        self.assertEqual(pay.amount, 200)
        self.assertEqual(pay.state, 'draft', 'La transferencia la confirma la Red')
        # Portal del grupo: aparece en «Pagos por realizar»
        self.authenticate('tesorero.g7@example.com', 'clave-tesorero.g7')
        res = self.url_open('/mi-grupo/pagos')
        self.assertIn(pay.request_reference, res.text)
        self.assertIn('Comprobante en revisión', res.text)

    @staticmethod
    def _csrf(html):
        import re
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def test_03_mercadopago_confirms_and_issues_receipt(self):
        pay = self._request()
        pay.action_send_payment_request()
        provider = self.env['payment.provider'].create({'name': 'Prueba RSP', 'code': 'none', 'state': 'test'})
        method = self.env['payment.method'].with_context(active_test=False).search([], limit=1)
        tx = self.env['payment.transaction'].create({
            'provider_id': provider.id, 'payment_method_id': method.id, 'reference': pay.request_reference,
            'amount': pay.amount_mp_total, 'currency_id': pay.currency_id.id,
            'partner_id': self.tesorero.partner_id.id,
        })
        tx._set_done()
        tx._rss_close_payment_requests()
        self.assertEqual(pay.state, 'confirmed')
        self.assertEqual(pay.payment_method, 'mercadopago')
        self.assertEqual(pay.amount, 200)
        self.assertAlmostEqual(pay.mp_commission, pay.amount_mp_total - 200, 2)
        self.authenticate(None, None)
        res = self.url_open('/seguro/pago/%s/%s/recibo' % (pay.id, pay.access_token))
        self.assertEqual(res.headers.get('Content-Type'), 'application/pdf')

    def test_04_mercadopago_underpaid_stays_draft(self):
        pay = self._request()
        pay.action_send_payment_request()
        provider = self.env['payment.provider'].create({'name': 'Prueba RSP 2', 'code': 'none', 'state': 'test'})
        method = self.env['payment.method'].with_context(active_test=False).search([], limit=1)
        tx = self.env['payment.transaction'].create({
            'provider_id': provider.id, 'payment_method_id': method.id, 'reference': pay.request_reference,
            'amount': 50, 'currency_id': pay.currency_id.id, 'partner_id': self.tesorero.partner_id.id,
        })
        tx._set_done()
        tx._rss_close_payment_requests()
        self.assertEqual(pay.state, 'draft')
