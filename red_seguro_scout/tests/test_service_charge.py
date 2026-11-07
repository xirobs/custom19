# -*- coding: utf-8 -*-
import re

from odoo.tests import HttpCase, tagged

from .test_group_portal import _RssPortalCommon


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestServiceCharge(_RssPortalCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()
        cls.policy.write({'bank_beneficiary': 'Red Seguro Scout AC', 'bank_name': 'BBVA',
                          'bank_clabe': '012180001234567891'})
        cls.tesorero = cls._member(cls.g7, 'treasurer', 'teso.srv@example.com')

    def _wizard(self, groups, send=True):
        wiz = self.env['rss.service.charge.wizard'].with_context(
            active_model='rss.scout.group', active_ids=groups.ids).create({'send': send})
        self.assertEqual(wiz.amount, 500)
        action = wiz.action_create()
        return self.env['rss.service.charge'].search(action['domain'])

    def test_01_wizard_creates_and_sends(self):
        charges = self._wizard(self.g7 | self.g3)
        self.assertEqual(len(charges), 2)
        c7 = charges.filtered(lambda c: c.group_id == self.g7)
        self.assertEqual(c7.amount, 500)
        self.assertEqual(c7.reference, 'RSV-G7-%s' % c7.id)
        self.assertTrue(c7.name.startswith('SRV/'))
        self.assertEqual(c7.state, 'sent')
        msg = c7.message_ids.filtered(lambda m: self.tesorero.partner_id in m.partner_ids)[:1]
        self.assertIn('012180001234567891', msg.body)
        self.assertIn(c7.access_token, msg.body)
        # Grupo sin representantes con correo: se crea pero queda por enviar
        c3 = charges - c7
        if not c3.representative_ids:
            self.assertEqual(c3.state, 'draft')
        wa = c3.action_send_whatsapp()
        self.assertIn('wa.me', wa['url'])
        self.assertEqual(c3.state, 'sent')

    def test_02_transfer_proof_and_register(self):
        charge = self._wizard(self.g7)
        self.authenticate(None, None)
        url = '/seguro/servicio/%s/%s' % (charge.id, charge.access_token)
        res = self.url_open(url)
        self.assertIn('012180001234567891', res.text)
        self.assertIn(charge.reference, res.text)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', res.text).group(1)
        res = self.url_open(url + '/comprobante', data={'csrf_token': csrf, 'tracking_key': 'MBAN0100'},
                            files={'proof': ('comp.jpg', b'\xff\xd8\xff x', 'image/jpeg')})
        self.assertEqual(res.status_code, 200)
        charge.invalidate_recordset()
        self.assertEqual(charge.state, 'review')
        self.assertEqual(len(charge.attachment_ids), 1)
        charge.action_register_payment()
        self.assertEqual(charge.state, 'paid')
        self.assertTrue(charge.paid_date)
        pdf = self.url_open(url + '/recibo')
        self.assertEqual(pdf.headers.get('Content-Type'), 'application/pdf')
        # Portal del grupo
        self.authenticate('teso.srv@example.com', 'clave-teso.srv')
        self.assertIn(charge.reference, self.url_open('/mi-grupo/pagos').text)

    def test_03_mercadopago_marks_paid(self):
        charge = self._wizard(self.g7)
        provider = self.env['payment.provider'].create({'name': 'Prueba SRV', 'code': 'none', 'state': 'test'})
        method = self.env['payment.method'].with_context(active_test=False).search([], limit=1)
        tx = self.env['payment.transaction'].create({
            'provider_id': provider.id, 'payment_method_id': method.id, 'reference': charge.reference,
            'amount': charge.amount_mp_total, 'currency_id': charge.currency_id.id,
            'partner_id': self.tesorero.partner_id.id})
        tx._set_done()
        tx._rss_close_service_charges()
        self.assertEqual(charge.state, 'paid')
        self.assertEqual(charge.payment_method, 'mercadopago')
        self.assertAlmostEqual(charge.mp_commission, charge.amount_mp_total - 500, 2)
