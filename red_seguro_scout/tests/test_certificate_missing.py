# -*- coding: utf-8 -*-
from datetime import date

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..models.rss_certificate import RssCertificateBatch
from ..models.rss_certificate_missing import split_certificate_name
from .test_group_portal import _RssPortalCommon, _pdf


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestCertificateMissingAndFuzzy(_RssPortalCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()
        # Captura con error y apellidos repetidos, como en los registros reales
        cls.typo = cls.env['rss.insured'].create({
            'group_id': cls.g7.id, 'first_name': 'Ana Laura Herrea Gonzalez', 'last_name_father': 'Herrera',
            'last_name_mother': 'Gonzalez', 'birthdate': date(1991, 12, 31), 'sex': 'female'})

    def test_01_split_name(self):
        self.assertEqual(split_certificate_name('LOPEZ MALAGON DANIELA PAULETTE'),
                         ('LOPEZ', 'MALAGON', 'DANIELA', 'PAULETTE'))
        self.assertEqual(split_certificate_name('CRUZ FUENTES DARIEL'), ('CRUZ', 'FUENTES', 'DARIEL', False))
        self.assertEqual(split_certificate_name('SOTO ANA'), ('SOTO', False, 'ANA', False))

    def test_02_fuzzy(self):
        fuzzy = [(1, {'ana', 'laura', 'herrea', 'gonzalez'}), (2, {'maria', 'fernanda', 'vasquez', 'galindooo'}),
                 (3, {'pedro', 'lopez', 'soto'})]
        self.assertEqual(RssCertificateBatch._fuzzy_match('herrera gonzalez ana laura', fuzzy), [1])
        self.assertEqual(RssCertificateBatch._fuzzy_match('vazquez galindo maria fernanda', fuzzy), [2])
        self.assertEqual(RssCertificateBatch._fuzzy_match('lopez malagon daniela paulette', fuzzy), [],
                         'Quien no está registrado no se pega a otro')

    def test_03_typo_matched_and_missing_created(self):
        pdf = _pdf([
            ['CERTIFICADO INDIVIDUAL No. AP-0101', 'Asegurado: HERRERA GONZALEZ ANA LAURA',
             'Fecha de nacimiento: 31/12/1991'],
            ['CERTIFICADO INDIVIDUAL No. AP-0102', 'Asegurado: LOPEZ MALAGON DANIELA PAULETTE',
             'Fecha de nacimiento: 25/09/2019'],
        ])
        att = self.env['ir.attachment'].create({'name': 'c.pdf', 'raw': pdf, 'mimetype': 'application/pdf'})
        batch = self.env['rss.certificate.batch'].create({'file_ids': [Command.set(att.ids)]})
        batch.action_read()
        lines = batch.line_ids.sorted('page_from')
        self.assertEqual(lines[0].insured_id, self.typo, 'Tolera el error de captura')
        self.assertEqual(lines[1].status, 'unmatched')
        self.assertEqual(lines[1].new_sex, 'female')
        # (En el formato AP Colectivo la fecha se lee del recuadro; aquí se captura como en la revisión)
        lines[1].new_group_id = self.g7
        if not lines[1].birthdate_read:
            with self.assertRaises(UserError):
                lines[1].action_create_missing_lines()
            lines[1].birthdate_read = date(2019, 9, 25)
        lines[1].new_group_id = False
        batch.action_attach()
        self.assertTrue(self.typo.certificate_attachment_id)
        action = batch.action_open_unmatched()
        pending = self.env['rss.certificate.line'].search(action['domain'])
        self.assertEqual(pending, lines[1])
        pending.new_group_id = self.g7
        pending.action_create_missing_lines()
        new = lines[1].insured_id
        self.assertTrue(new)
        self.assertEqual((new.first_name, new.second_name, new.last_name_father, new.last_name_mother),
                         ('Daniela', 'Paulette', 'Lopez', 'Malagon'))
        self.assertEqual(new.group_id, self.g7)
        self.assertEqual(new.state, 'active')
        self.assertEqual(new.section, 'manada')
        self.assertTrue(new.certificate_attachment_id)
        self.assertEqual(batch.state, 'done')

    def test_04_duplicate_does_not_block_others(self):
        pdf = _pdf([
            ['CERTIFICADO INDIVIDUAL No. AP-0201', 'Asegurado: GARCIA LOPEZ MARIA FERNANDA'],
            ['CERTIFICADO INDIVIDUAL No. AP-0202', 'Asegurado: GARCIA LOPEZ MARIA FERNANDA'],
            ['CERTIFICADO INDIVIDUAL No. AP-0203', 'Asegurado: JOSE LUIS DE LA CRUZ RAMOS'],
        ])
        att = self.env['ir.attachment'].create({'name': 'd.pdf', 'raw': pdf, 'mimetype': 'application/pdf'})
        batch = self.env['rss.certificate.batch'].create({'file_ids': [Command.set(att.ids)]})
        batch.action_read()
        batch.action_attach()
        self.assertTrue(self.jose.certificate_attachment_id, 'Los demás sí se anexan')
        self.assertFalse(self.maria.certificate_attachment_id, 'La repetida queda para revisión')
