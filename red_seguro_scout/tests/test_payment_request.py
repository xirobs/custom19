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

    def test_05_platform_list_sends_payment_link(self):
        """Lista cargada desde la plataforma: los preinscritos quedan en un pago pendiente y la liga
        les llega a los representantes."""
        from .test_group_portal import _xlsx, TestOfficeListLoad
        wiz = self.env['rss.import.wizard'].create({
            'file': base64.b64encode(_xlsx(TestOfficeListLoad.ROWS[:1], TestOfficeListLoad.HEADERS)),
            'filename': 'g7.xlsx'})
        wiz.action_load()
        batch = self.env['rss.import.batch'].search([('office_load', '=', True), ('group_id', '=', self.g7.id)])
        batch.action_save_draft()
        pay = batch.rss_request_payment_id
        self.assertTrue(pay)
        self.assertEqual(pay.member_ids.mapped('first_name'), ['Lucía'])
        self.assertEqual(pay.amount_expected, 100)
        self.assertTrue(pay.request_sent_date)
        msg = pay.message_ids.filtered(lambda m: self.tesorero.partner_id in m.partner_ids)[:1]
        self.assertTrue(msg)
        self.assertIn('012180001234567891', msg.body)
        self.assertIn(pay.access_token, msg.body)

    def test_06_group_capture_sends_payment_info(self):
        """El grupo captura su lista en el portal: al pasar a pago, les llega el importe y la cuenta."""
        from datetime import date
        batch = self.env['rss.import.batch'].create_manual(self.g7, rows=1)
        batch.line_ids.write({'first_name': 'Nueva', 'last_name_father': 'Persona', 'last_name_mother': 'Grupo',
                              'birthdate': date(2014, 2, 2), 'sex': 'female'})
        batch.action_request_payment()
        self.assertEqual(batch.state, 'payment')
        msg = batch.message_ids.filtered(lambda m: self.tesorero.partner_id in m.partner_ids)[:1]
        self.assertTrue(msg)
        self.assertIn('012180001234567891', msg.body)
        self.assertIn(batch.reference, msg.body)
        self.assertIn('/mi-grupo/importar/%s' % batch.id, msg.body)

    def test_05_sandbox_payment_is_not_registered(self):
        """Un pago hecho con Mercado Pago en modo de prueba no registra el pago ni emite recibo."""
        self.env['ir.config_parameter'].sudo().set_param('red_seguro_scout.accept_test_payments', False)
        pay = self._request()
        pay.action_send_payment_request()
        provider = self.env['payment.provider'].create({'name': 'Sandbox', 'code': 'none', 'state': 'test'})
        method = self.env['payment.method'].with_context(active_test=False).search([], limit=1)
        tx = self.env['payment.transaction'].create({
            'provider_id': provider.id, 'payment_method_id': method.id, 'reference': pay.request_reference,
            'amount': pay.amount_mp_total, 'currency_id': pay.currency_id.id,
            'partner_id': self.tesorero.partner_id.id,
        })
        tx._set_done()
        tx._rss_close_payment_requests()
        self.assertEqual(pay.state, 'draft')
        self.assertIn('PRUEBA', pay.message_ids[:1].body)
        self.assertEqual(self.maria.pay_status, 'unpaid')

    def test_06_payment_alerts(self):
        """Avisos a la administración: recibo emitido y comprobante por validar."""
        boss = self.env['res.partner'].create({'name': 'Coordinación Red', 'email': 'coordinacion@example.com'})
        settings = self.env['res.config.settings'].create({'rss_payment_notify_partner_ids': [(6, 0, boss.ids)]})
        settings.execute()
        self.assertEqual(self.env.company.rss_payment_notify_partner_ids, boss)
        pay = self._request()
        pay.action_send_payment_request()
        pay._group_submit_transfer('0098', 'MBAN01', [('c.jpg', base64.b64encode(b'\xff\xd8\xff x'), 'image/jpeg')])
        alert = pay.message_ids.filtered(lambda m: boss in m.partner_ids)
        self.assertTrue(alert)
        self.assertIn('por validar', alert[0].body)
        pay.action_confirm()
        alert = pay.message_ids.filtered(lambda m: boss in m.partner_ids and 'Recibo' in (m.body or ''))
        self.assertTrue(alert, 'Aviso del recibo emitido')
        self.assertIn('Personas cubiertas', alert[0].body)
        self.assertIn('/odoo/rss.payment/%s' % pay.id, alert[0].body)
