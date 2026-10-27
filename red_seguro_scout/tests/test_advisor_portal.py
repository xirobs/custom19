# -*- coding: utf-8 -*-
from datetime import date

from odoo.fields import Command
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestAdvisorPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.policy = env['rss.policy'].create({'name': 'Póliza portal', 'premium_per_member': 300})
        cls.cluster_a = env['rss.group.cluster'].create({'name': 'Distrito A'})
        cls.group_a = env['rss.scout.group'].create({'name': 'Grupo Portal A', 'policy_id': cls.policy.id,
                                                     'cluster_id': cls.cluster_a.id})
        cls.group_b = env['rss.scout.group'].create({'name': 'Grupo Portal B', 'policy_id': cls.policy.id})

        def insured(group, first):
            rec = env['rss.insured'].create({
                'first_name': first, 'last_name_father': 'Prueba', 'birthdate': date(2014, 1, 1),
                'sex': 'female', 'group_id': group.id, 'tutor_name': 'Tutor', 'tutor_phone': '222',
                'date_start': date(2026, 1, 1)})
            rec.action_activate()
            return rec
        cls.ins_a = insured(cls.group_a, 'Alicia')
        cls.ins_b = insured(cls.group_b, 'Beatriz')
        cls.claim_a = env['rss.claim'].create({'insured_id': cls.ins_a.id, 'injury_type': 'fracture',
                                               'date_accident': date(2026, 3, 1)})
        cls.claim_b = env['rss.claim'].create({'insured_id': cls.ins_b.id, 'injury_type': 'cut',
                                               'date_accident': date(2026, 3, 1)})
        cls.report = env['rss.report'].create({'policy_id': cls.policy.id, 'period_date': date(2026, 3, 1),
                                               'group_ids': [Command.set((cls.group_a | cls.group_b).ids)]})
        cls.report.action_generate()
        cls.report.action_mark_sent()

        partner = env['res.partner'].create({'name': 'Asesora Seguros', 'email': 'asesora@example.com'})
        cls.advisor = env['rss.advisor'].create({'partner_id': partner.id})
        cls.advisor.action_grant_portal()
        cls.advisor.user_id.password = 'asesora-pass-1'

    def test_01_portal_user_not_internal(self):
        self.assertTrue(self.advisor.has_portal)
        self.assertTrue(self.advisor.user_id.share, 'El asesor es usuario de portal, no interno')
        self.assertFalse(self.advisor.user_id.has_group('red_seguro_scout.group_rss_user'))

    def test_02_pages_load(self):
        self.authenticate('asesora@example.com', 'asesora-pass-1')
        for url, text in (('/seguro', 'Portal del asesor'), ('/seguro/asegurados', 'Alicia'),
                          ('/seguro/relaciones', 'Registrar acuse'), ('/seguro/pagos', 'Pagos de los grupos'),
                          ('/seguro/siniestros', 'Alicia'), ('/seguro/siniestros/%s' % self.claim_a.id, 'Seguimiento'),
                          ('/my', 'Red Seguro Scout · Asesor')):
            res = self.url_open(url)
            self.assertEqual(res.status_code, 200, url)
            self.assertIn(text, res.text, url)

    def test_03_excel_download(self):
        self.authenticate('asesora@example.com', 'asesora-pass-1')
        res = self.url_open('/seguro/relaciones/%s/excel' % self.report.id)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.content.startswith(b'PK'), 'Es un archivo xlsx')

    def test_04_restricted_to_cluster(self):
        self.advisor.cluster_ids = [Command.set(self.cluster_a.ids)]
        self.authenticate('asesora@example.com', 'asesora-pass-1')
        page = self.url_open('/seguro/asegurados?estatus=all').text
        self.assertIn('Alicia', page)
        self.assertNotIn('Beatriz', page)
        self.assertNotIn('Beatriz', self.url_open('/seguro/siniestros?estatus=all').text)
        # Siniestro de otro grupo: no lo abre
        res = self.url_open('/seguro/siniestros/%s' % self.claim_b.id)
        self.assertNotIn('Seguimiento', res.text)
        # La relación incluye otro grupo: no la descarga
        self.assertEqual(self.url_open('/seguro/relaciones/%s/excel' % self.report.id).status_code, 404)

    def test_05_update_claim_and_ack(self):
        self.authenticate('asesora@example.com', 'asesora-pass-1')
        page = self.url_open('/seguro/siniestros/%s' % self.claim_a.id).text
        import re
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        res = self.url_open('/seguro/siniestros/%s' % self.claim_a.id, data={
            'csrf_token': csrf, 'state': 'in_process', 'insurer_claim_number': 'SIN-77',
            'amount_claimed': '1,800.50', 'note': 'Falta factura del hospital',
        }, files={'documents': ('informe.pdf', b'%PDF-1.4 prueba', 'application/pdf')})
        self.assertEqual(res.status_code, 200)
        self.claim_a.invalidate_recordset()
        self.assertEqual(self.claim_a.state, 'in_process')
        self.assertEqual(self.claim_a.insurer_claim_number, 'SIN-77')
        self.assertAlmostEqual(self.claim_a.amount_claimed, 1800.50)
        self.assertEqual(self.claim_a.attachment_ids.name, 'informe.pdf')
        note = self.claim_a.message_ids.filtered(lambda m: 'Falta factura' in (m.body or ''))
        self.assertTrue(note)
        self.assertEqual(note.author_id, self.advisor.partner_id)
        # No puede marcar pagado sin monto
        self.url_open('/seguro/siniestros/%s' % self.claim_a.id, data={'csrf_token': csrf, 'state': 'paid'})
        self.claim_a.invalidate_recordset()
        self.assertEqual(self.claim_a.state, 'in_process')
        # Acuse de relación
        self.url_open('/seguro/relaciones/%s/acuse' % self.report.id,
                      data={'csrf_token': csrf, 'insurer_ack': 'ACUSE-2026-03'})
        self.report.invalidate_recordset()
        self.assertEqual(self.report.state, 'acknowledged')
        self.assertEqual(self.report.insurer_ack, 'ACUSE-2026-03')

    def test_06_read_only_advisor(self):
        self.advisor.can_update_claims = False
        self.authenticate('asesora@example.com', 'asesora-pass-1')
        page = self.url_open('/seguro/siniestros/%s' % self.claim_a.id).text
        self.assertNotIn('Guardar seguimiento', page)

    def test_07_other_portal_user_denied(self):
        other = self.env['res.users'].create({
            'name': 'Papá', 'login': 'papa_portal@example.com', 'password': 'papa-pass-123',
            'group_ids': [Command.set([self.env.ref('base.group_portal').id])]})
        self.assertTrue(other)
        self.authenticate('papa_portal@example.com', 'papa-pass-123')
        res = self.url_open('/seguro/asegurados')
        self.assertIn('No tienes acceso al portal del asesor', res.text)
        self.assertNotIn('Alicia', res.text)
        self.assertEqual(self.url_open('/seguro/relaciones/%s/excel' % self.report.id).status_code, 404)
