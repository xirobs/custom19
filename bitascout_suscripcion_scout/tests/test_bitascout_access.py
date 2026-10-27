# -*- coding: utf-8 -*-
from datetime import date

from freezegun import freeze_time

from odoo.exceptions import ValidationError
from odoo.fields import Command
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'bitascout_suscripcion_scout')
class TestBitaScoutAccess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.platform = cls.env.ref('bitascout_suscripcion_scout.bita_platform_bitascout')
        cls.leader_group = cls.env.ref('scout_management.group_scout_leader')
        cls.section = cls.env['scout.section'].search([], limit=1) or cls.env['scout.section'].create(
            {'name': 'Tropa prueba'})
        cls.portal_leader = cls.env['res.users'].create({
            'name': 'Dirigente Portal', 'login': 'dirigente_portal@example.com',
            'group_ids': [Command.set([cls.env.ref('base.group_portal').id])],
        })
        cls.section.leader_ids = [Command.link(cls.portal_leader.id)]
        cls.Sub = cls.env['bita.subscription']

    def test_01_platform_registered_with_trial(self):
        self.assertEqual(self.platform.trial_months, 3)
        self.assertIn(self.leader_group, self.platform.group_ids)
        self.assertFalse(self.platform.enforce, 'Arranca apagado para no cortar a nadie')

    def test_02_portal_leader_gets_trial_then_blocked(self):
        self.platform.action_enforce()
        with freeze_time('2026-11-01'):
            self.assertIn(self.section, self.portal_leader._scout_leader_sections())
            trial = self.Sub.search([('user_id', '=', self.portal_leader.id)])
            self.assertTrue(trial.is_trial)
            self.assertEqual(trial.date_end, date(2027, 1, 31))
        with freeze_time('2027-02-04'):
            self.Sub._cron_check_subscriptions()
            self.assertFalse(self.portal_leader._scout_leader_sections())
            self.assertTrue(self.portal_leader._bita_scout_blocked())
        with freeze_time('2027-02-05'):
            self.Sub.create({'user_id': self.portal_leader.id, 'platform_id': self.platform.id,
                             'period': 'year', 'date_start': date(2027, 2, 5),
                             'amount': 1200}).action_activate()
            self.assertIn(self.section, self.portal_leader._scout_leader_sections())

    def test_03_internal_role_trial_and_block(self):
        self.platform.action_enforce()
        user = self.env['res.users'].create({
            'name': 'Secretaria', 'login': 'secretaria_prueba@example.com',
            'group_ids': [Command.link(self.env.ref('base.group_user').id)],
        })
        user.write({'group_ids': [Command.link(self.env.ref('scout_management.group_scout_officer').id)]})
        self.assertTrue(self.Sub.search([('user_id', '=', user.id)]).is_trial)
        self.Sub.search([('user_id', '=', user.id)]).action_cancel()
        self.assertFalse(user.has_group('scout_management.group_scout_leader'))
        with self.assertRaises(ValidationError):
            user.write({'group_ids': [Command.link(self.leader_group.id)]})

    def test_04_courtesy_includes_portal_leaders(self):
        self.platform.action_grant_courtesy()
        sub = self.Sub.search([('user_id', '=', self.portal_leader.id)])
        self.assertTrue(sub.is_trial, 'Los dirigentes actuales arrancan con su prueba de 3 meses')
