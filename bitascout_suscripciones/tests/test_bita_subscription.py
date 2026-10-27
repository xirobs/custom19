# -*- coding: utf-8 -*-
from datetime import date

from freezegun import freeze_time

from odoo.exceptions import ValidationError
from odoo.fields import Command
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'bitascout_suscripciones')
class TestBitaSubscription(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.role_a = cls.env['res.groups'].create({'name': 'Rol prueba A'})
        cls.role_b = cls.env['res.groups'].create({'name': 'Rol prueba B'})
        cls.platform = cls.env['bita.platform'].create({
            'name': 'Plataforma prueba', 'code': 'prueba',
            'group_ids': [Command.set((cls.role_a | cls.role_b).ids)],
            'default_group_id': cls.role_a.id, 'grace_days': 0, 'trial_months': 0,
        })
        cls.trial_platform = cls.env['bita.platform'].create({
            'name': 'Con prueba', 'code': 'con_prueba', 'trial_months': 3, 'grace_days': 0,
            'group_ids': [Command.set(cls.role_b.ids)], 'default_group_id': cls.role_b.id,
        })
        cls.user = cls.env['res.users'].create({
            'name': 'Dirigente Prueba', 'login': 'dirigente_prueba@example.com',
            'email': 'dirigente_prueba@example.com',
            'group_ids': [Command.link(cls.env.ref('base.group_user').id), Command.link(cls.role_a.id)],
        })
        cls.Sub = cls.env['bita.subscription']

    def _sub(self, **kw):
        vals = {'user_id': self.user.id, 'platform_id': self.platform.id, 'period': 'month',
                'date_start': date.today(), 'amount': 100}
        vals.update(kw)
        return self.Sub.create(vals)

    def test_01_no_enforce_keeps_groups(self):
        self.user._bita_sync_access()
        self.assertIn(self.role_a, self.user.group_ids)

    def test_02_enforce_revokes_without_subscription(self):
        self.platform.action_enforce()
        self.assertNotIn(self.role_a, self.user.group_ids)
        self.assertIn(self.env.ref('base.group_user'), self.user.all_group_ids,
                      'Sigue siendo usuario interno')

    def test_03_payment_grants_role(self):
        self.platform.action_enforce()
        sub = self._sub(group_id=self.role_b.id)
        self.assertNotIn(self.role_b, self.user.group_ids, 'Pendiente de pago no da acceso')
        sub.action_activate()
        self.assertIn(self.role_b, self.user.group_ids)
        sub.action_cancel()
        self.assertNotIn(self.role_b, self.user.group_ids)

    def test_04_manual_assignment_blocked(self):
        self.platform.action_enforce()
        with self.assertRaises(ValidationError):
            self.user.write({'group_ids': [Command.link(self.role_a.id)]})
        with self.assertRaises(ValidationError):
            self.role_a.write({'user_ids': [Command.link(self.user.id)]})
        self.user.bita_exempt = True
        self.user.write({'group_ids': [Command.link(self.role_a.id)]})
        self.assertIn(self.role_a, self.user.group_ids)

    def test_05_expiry_by_cron(self):
        self.platform.action_enforce()
        with freeze_time('2026-01-10'):
            sub = self._sub(date_start=date(2026, 1, 10))
            sub.action_activate()
            self.assertEqual(sub.date_end, date(2026, 2, 9))
            self.assertIn(self.role_a, self.user.group_ids)
        with freeze_time('2026-02-09'):
            self.Sub._cron_check_subscriptions()
            self.assertEqual(sub.state, 'active')
        with freeze_time('2026-02-10'):
            self.Sub._cron_check_subscriptions()
            self.assertEqual(sub.state, 'expired')
            self.assertNotIn(self.role_a, self.user.group_ids)

    def test_06_login_revokes_expired(self):
        self.platform.action_enforce()
        with freeze_time('2026-03-01'):
            self._sub(date_start=date(2026, 3, 1)).action_activate()
        with freeze_time('2026-05-01'):
            self.user._update_last_login()
            self.assertNotIn(self.role_a, self.user.group_ids)

    def test_07_two_platforms_independent(self):
        self.platform.action_enforce()
        self.trial_platform.action_enforce()
        self._sub().action_activate()
        self.assertTrue(self.user._bita_has_access(self.platform))
        # La otra plataforma: sin pago pero con prueba disponible
        self.user.write({'group_ids': [Command.link(self.role_b.id)]})
        trial = self.Sub.search([('user_id', '=', self.user.id),
                                 ('platform_id', '=', self.trial_platform.id)])
        self.assertTrue(trial.is_trial)
        self.assertTrue(self.user._bita_has_access(self.trial_platform))

    def test_08_trial_three_months_once(self):
        self.trial_platform.action_enforce()
        with freeze_time('2026-11-01'):
            self.user.write({'group_ids': [Command.link(self.role_b.id)]})
            trial = self.Sub.search([('user_id', '=', self.user.id),
                                     ('platform_id', '=', self.trial_platform.id)])
            self.assertEqual(trial.date_end, date(2027, 1, 31))
            self.assertEqual(trial.payment_method, 'trial')
        with freeze_time('2027-02-01'):
            self.Sub._cron_check_subscriptions()
            self.assertEqual(trial.state, 'expired')
            self.assertNotIn(self.role_b, self.user.group_ids)
            with self.assertRaises(ValidationError):
                self.user.write({'group_ids': [Command.link(self.role_b.id)]})
            # Paga: vuelve a tener acceso
            self.Sub.create({'user_id': self.user.id, 'platform_id': self.trial_platform.id,
                             'period': 'year', 'date_start': date(2027, 2, 1),
                             'amount': 1200}).action_activate()
            self.assertIn(self.role_b, self.user.group_ids)

    def test_09_courtesy_for_current_users(self):
        with freeze_time('2026-10-27'):
            self.platform.courtesy_until = date(2026, 11, 30)
            self.platform.action_grant_courtesy()
            sub = self.Sub.search([('user_id', '=', self.user.id),
                                   ('platform_id', '=', self.platform.id)])
            self.assertEqual(sub.date_end, date(2026, 11, 30))
            self.platform.action_enforce()
            self.assertIn(self.role_a, self.user.group_ids, 'Nadie se queda fuera al encender')

    def test_10_reminder_email(self):
        self.platform.action_enforce()
        with freeze_time('2026-06-01'):
            sub = self._sub(date_start=date(2026, 6, 1))
            sub.action_activate()
        with freeze_time('2026-06-25'):
            self.Sub._cron_check_subscriptions()
            self.assertTrue(sub.reminder_sent)
            mail = self.env['mail.mail'].search([('model', '=', 'bita.subscription'),
                                                 ('res_id', '=', sub.id)], limit=1)
            self.assertTrue(mail)
            self.assertIn('vence', mail.subject)
