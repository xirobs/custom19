# -*- coding: utf-8 -*-
import base64
import io
import re
from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command
from odoo.tests import HttpCase, TransactionCase, tagged


def _xlsx(rows, headers):
    import xlsxwriter
    out = io.BytesIO()
    book = xlsxwriter.Workbook(out, {'in_memory': True})
    ws = book.add_worksheet()
    ws.write(0, 0, 'LISTA DEL GRUPO')            # título libre arriba, como lo mandan
    ws.write_row(2, 0, headers)
    for i, row in enumerate(rows, start=3):
        ws.write_row(i, 0, row)
    book.close()
    return out.getvalue()


def _pdf(pages):
    """PDF de varias páginas; cada página es una lista de renglones de texto."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=letter)
    for lines in pages:
        y = 740
        for line in lines:
            c.drawString(60, y, line)
            y -= 22
        c.showPage()
    c.save()
    return out.getvalue()


class _RssPortalCommon:

    @classmethod
    def _setup_data(cls):
        env = cls.env
        cls.policy = env['rss.policy'].create({'name': 'Póliza grupos', 'premium_per_member': 100})
        cls.g7 = env['rss.scout.group'].create({'name': 'Grupo 7 Ethnos', 'number': 7, 'policy_id': cls.policy.id,
                                                'city': 'Puebla'})
        cls.g3 = env['rss.scout.group'].create({'name': 'Grupo 3 Lan Nakuh', 'number': 3, 'policy_id': cls.policy.id})

        def ins(group, first, second, father, mother, birth, sex='female'):
            rec = env['rss.insured'].create({
                'first_name': first, 'second_name': second, 'last_name_father': father,
                'last_name_mother': mother, 'birthdate': birth, 'sex': sex, 'group_id': group.id,
                'tutor_name': 'Tutor Prueba', 'tutor_phone': '2221234567', 'date_start': date(2026, 9, 1)})
            rec.action_activate()
            return rec
        cls.maria = ins(cls.g7, 'María', 'Fernanda', 'García', 'López', date(2014, 3, 12))
        cls.jose = ins(cls.g7, 'José', 'Luis', 'de la Cruz', 'Ramos', date(2012, 7, 30), 'male')
        cls.ana_a = ins(cls.g3, 'Ana', False, 'Pérez', 'Soto', date(2013, 1, 5))
        cls.ana_b = ins(cls.g3, 'Ana', False, 'Pérez', 'Soto', date(2015, 6, 20))   # homónima

    @classmethod
    def _member(cls, group, role, login, capturer=False):
        partner = cls.env['res.partner'].create({'name': login.split('@')[0].title(), 'email': login})
        member = cls.env['rss.group.member'].create({'group_id': group.id, 'partner_id': partner.id,
                                                     'role': role, 'is_capturer': capturer})
        member.action_grant_portal()
        member.user_id.password = 'clave-' + login.split('@')[0]
        return member


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestGroupMembersAndImport(_RssPortalCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()

    def test_01_roles_one_per_group_and_single_capturer(self):
        jefe = self._member(self.g7, 'leader', 'jefe7@example.com', capturer=True)
        self.assertTrue(jefe.user_id.share, 'Es usuario de portal')
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self._member(self.g7, 'leader', 'otrojefe7@example.com')
        teso = self._member(self.g7, 'treasurer', 'teso7@example.com')
        with self.assertRaises(ValidationError):
            teso.is_capturer = True
        teso.action_make_capturer()
        self.assertTrue(teso.is_capturer)
        self.assertFalse(jefe.is_capturer)
        # Una cuenta solo puede representar a un grupo
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.env['rss.group.member'].create({'group_id': self.g3.id, 'partner_id': jefe.partner_id.id,
                                                     'role': 'secretary'})

    def test_02_excel_import_with_duplicates(self):
        headers = ['No.', 'APELLIDO PATERNO', 'APELLIDO MATERNO', 'NOMBRE(S)', 'FECHA DE NACIMIENTO', 'EDAD',
                   'SEXO', 'NOMBRE DEL PADRE O TUTOR', 'TELÉFONO DE EMERGENCIA', 'CORREO ELECTRÓNICO']
        rows = [
            [1, 'GARCÍA', 'LÓPEZ', 'MARÍA FERNANDA', '12/03/2014', 12, 'M', 'Laura López', '2221112233', 'l@x.com'],  # ya existe
            [2, 'Flores', 'Vega', 'Regina', '05/11/2015', 10, 'M', 'Mónica Vega', '2223334455', 'm@x.com'],
            [3, 'Flores', 'Vega', 'Regina', '05/11/2015', 10, 'M', 'Mónica Vega', '2223334455', 'm@x.com'],     # repetida
            [4, 'Ruiz', 'Paz', 'Tomás', 'no sé', 9, 'H', 'Raúl Ruiz', '2225556677', 'r@x.com'],                # error fecha
            [5, 'Solís', 'Mar', 'Iker', '09/01/2016', 10, 'H', '', '', ''],                                     # sin tutor: válido
        ]
        batch = self.env['rss.import.batch'].create_from_file(self.g7, 'lista.xlsx', _xlsx(rows, headers))
        lines = batch.line_ids.sorted('sequence')
        self.assertEqual(lines.mapped('status'), ['duplicate', 'ok', 'duplicate', 'error', 'ok'])
        self.assertEqual(lines[0].duplicate_insured_id, self.maria)
        self.assertEqual(lines[1].sex, 'female', 'Con H/M en la columna, la M es Mujer')
        # Corrige en la tabla y guarda
        lines[3].birthdate = date(2017, 2, 2)
        batch.action_request_payment()
        self.assertEqual(batch.state, 'payment')
        self.assertFalse(self.env['rss.insured'].search([('import_batch_id', '=', batch.id)]),
                         'Nadie se guarda antes de pagar')
        batch.action_submit_transfer('SPEI123', files=[('comp.pdf', b'%PDF-1.4 x', 'application/pdf')])
        batch.action_validate_transfer()
        self.assertEqual(batch.created_count, 3)
        new = self.env['rss.insured'].search([('import_batch_id', '=', batch.id)])
        self.assertEqual(set(new.mapped('state')), {'active'})
        self.assertEqual(set(new.mapped('capture_source')), {'excel'})
        self.assertEqual(set(new.group_id.ids), {self.g7.id})
        iker = new.filtered(lambda i: i.first_name == 'Iker')
        self.assertTrue(iker and not iker.tutor_name, 'Menor sin tutor queda asegurado con el campo en blanco')

    def test_03_pdf_import(self):
        pdf = _pdf([[
            'Grupo 3 Lan Nakuh - Asegurados',
            'Apellido paterno  Apellido materno  Nombre  Fecha de nacimiento  Sexo  Tutor  Telefono  Correo',
            '1  Mendoza  Lara  Sofia Isabel  14/02/2014  Mujer  Carla Lara  2224445566  carla@correo.com',
            '2  de la Rosa  Gil  Emilio  22/08/2013  Hombre  Hugo de la Rosa  2227778899  hugo@correo.com',
        ]])
        batch = self.env['rss.import.batch'].create_from_file(self.g3, 'lista.pdf', pdf)
        self.assertEqual(batch.source, 'pdf')
        lines = batch.line_ids.sorted('sequence')
        self.assertEqual(len(lines), 2)
        self.assertEqual((lines[0].first_name, lines[0].second_name, lines[0].last_name_father,
                          lines[0].last_name_mother), ('Sofia', 'Isabel', 'Mendoza', 'Lara'))
        self.assertEqual(lines[1].last_name_father, 'De la Rosa')
        self.assertEqual(lines[1].tutor_email, 'hugo@correo.com')
        self.assertEqual(lines.mapped('status'), ['ok', 'ok'])

    def test_04_certificates_split_and_match(self):
        pdf = _pdf([
            ['CERTIFICADO INDIVIDUAL No. AP-0001', 'Nombre del asegurado: GARCIA LOPEZ MARIA FERNANDA',
             'Fecha de nacimiento: 12/03/2014', 'Accidentes personales escolares'],
            ['Condiciones generales del seguro', 'Coberturas: fracturas, golpes, cortes'],           # 2a hoja de María
            ['CERTIFICADO INDIVIDUAL No. AP-0002', 'Asegurado: JOSE LUIS DE LA CRUZ RAMOS'],
            ['CERTIFICADO INDIVIDUAL No. AP-0003', 'Asegurado: PEDRO PICAPIEDRA ROCA'],             # no registrado
            ['CERTIFICADO INDIVIDUAL No. AP-0004', 'Asegurado: PEREZ SOTO ANA', 'Nacimiento: 20/06/2015'],
            ['CERTIFICADO INDIVIDUAL No. AP-0005', 'Asegurado: PEREZ SOTO ANA'],                      # homónima sin fecha
        ])
        att = self.env['ir.attachment'].create({'name': 'certificados.pdf', 'raw': pdf, 'mimetype': 'application/pdf'})
        batch = self.env['rss.certificate.batch'].create({'file_ids': [Command.set(att.ids)]})
        batch.action_read()
        lines = batch.line_ids.sorted('page_from')
        self.assertEqual([(l.page_from, l.page_to) for l in lines], [(1, 2), (3, 3), (4, 4), (5, 5), (6, 6)])
        self.assertEqual(lines[0].insured_id, self.maria)
        self.assertEqual(lines[0].certificate_number, 'AP-0001')
        self.assertEqual(lines[1].insured_id, self.jose)
        self.assertEqual(lines[2].status, 'unmatched', 'Quien no está registrado NO se pega a otro')
        self.assertEqual(lines[3].insured_id, self.ana_b, 'La fecha de nacimiento desempata homónimos')
        self.assertEqual(lines[4].status, 'ambiguous')
        self.assertEqual(set(lines[4].candidate_ids.ids), {self.ana_a.id, self.ana_b.id})
        lines[4].skip = True
        lines[2].skip = True
        batch.action_attach()
        self.assertTrue(self.maria.certificate_attachment_id)
        self.assertEqual(self.maria.certificate_number, 'AP-0001')
        from odoo.tools.pdf import PdfFileReader
        reader = PdfFileReader(io.BytesIO(self.maria.certificate_attachment_id.raw))
        self.assertEqual(len(reader.pages), 2, 'María recibe solo sus 2 páginas')
        self.assertIn('MARIA', reader.pages[0].extract_text().upper())
        self.assertEqual(len(PdfFileReader(io.BytesIO(self.jose.certificate_attachment_id.raw)).pages), 1)
        self.assertFalse(self.ana_a.certificate_attachment_id)


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestGroupPortalHttp(_RssPortalCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()
        cls.capt = cls._member(cls.g7, 'secretary', 'secre7@example.com', capturer=True)
        cls.jefe = cls._member(cls.g7, 'leader', 'jefe7b@example.com')
        cls.other = cls._member(cls.g3, 'leader', 'jefe3@example.com', capturer=True)
        cls.maria.certificate_attachment_id = cls.env['ir.attachment'].create(
            {'name': 'cert.pdf', 'raw': b'%PDF-1.4 cert', 'mimetype': 'application/pdf',
             'res_model': 'rss.insured', 'res_id': cls.maria.id})
        cls.claim = cls.env['rss.claim'].create({'insured_id': cls.maria.id, 'injury_type': 'fracture',
                                                 'date_accident': date(2026, 10, 1)})

    def _csrf(self, url):
        return re.search(r'name="csrf_token" value="([^"]+)"', self.url_open(url).text).group(1)

    def test_01_only_own_group(self):
        self.authenticate('jefe3@example.com', 'clave-jefe3')
        page = self.url_open('/mi-grupo/asegurados').text
        self.assertIn('Pérez', page)
        self.assertNotIn('García', page)
        self.assertEqual(self.url_open('/mi-grupo/asegurados/%s/certificado' % self.maria.id).status_code, 404)
        self.assertNotIn(self.maria.name, self.url_open('/mi-grupo/siniestros').text)
        self.assertIn('aviso de privacidad', self.url_open('/mi-grupo').text)

    def test_02_pages_and_certificate(self):
        self.authenticate('jefe7b@example.com', 'clave-jefe7b')
        for url, text in (('/mi-grupo', 'Grupo 7 Ethnos'), ('/mi-grupo/asegurados', 'García'),
                          ('/mi-grupo/siniestros', 'García'), ('/mi-grupo/siniestros/%s' % self.claim.id, 'Fractura'),
                          ('/my', 'Red Seguro Scout · Grupo 7 Ethnos'), ('/aviso-de-privacidad-seguro', 'Derechos ARCO')):
            res = self.url_open(url)
            self.assertEqual(res.status_code, 200, url)
            self.assertIn(text, res.text, url)
        res = self.url_open('/mi-grupo/asegurados/%s/certificado' % self.maria.id)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.content.startswith(b'%PDF'))
        # Por omisión los tres representantes cargan; el jefe ve el formulario aunque no sea capturista
        self.assertNotIn('la hace solo el <b>capturista</b>', self.url_open('/mi-grupo/importar').text)
        # Si el grupo limita la carga al capturista, el jefe ya no la ve
        self.g7.portal_capture_mode = 'capturer'
        self.assertIn('la hace solo el <b>capturista</b>', self.url_open('/mi-grupo/importar').text)

    def test_03_capturer_uploads_reviews_and_saves(self):
        self.authenticate('secre7@example.com', 'clave-secre7')
        csrf = self._csrf('/mi-grupo/importar')
        tpl = self.url_open('/mi-grupo/importar/plantilla.xlsx')
        self.assertTrue(tpl.content.startswith(b'PK'))
        headers = ['Nombre', 'Apellido paterno', 'Apellido materno', 'Fecha de nacimiento', 'Sexo',
                   'Padre o tutor', 'Teléfono de emergencia', 'Correo']
        data = _xlsx([['Valeria', 'Ortiz', 'Luna', '03/04/2014', 'Mujer', 'Rosa Luna', '2221010101', 'r@x.com'],
                      ['Bruno', 'Ortiz', 'Luna', '31/02/2013', 'Hombre', 'Rosa Luna', '2221010101', 'r@x.com']], headers)
        # Sin aceptar el aviso de privacidad no avanza
        res = self.url_open('/mi-grupo/importar', data={'csrf_token': csrf},
                            files={'file': ('lista.xlsx', data, 'application/octet-stream')})
        self.assertIn('aviso de privacidad', res.text)
        res = self.url_open('/mi-grupo/importar', data={'csrf_token': csrf, 'privacy': '1'},
                            files={'file': ('lista.xlsx', data, 'application/octet-stream')})
        batch = self.env['rss.import.batch'].search([('group_id', '=', self.g7.id)], limit=1)
        self.assertTrue(batch)
        self.assertIn('Con error', res.text)
        bad = batch.line_ids.filtered(lambda l: l.status == 'error')
        form = {'csrf_token': csrf, 'action': 'confirm'}
        for line in batch.line_ids:
            form['l%s_include' % line.id] = '1'
            form['l%s_birthdate' % line.id] = '28/02/2013' if line == bad else '03/04/2014'
        page = self.url_open('/mi-grupo/importar/%s' % batch.id, data=form).text
        batch.invalidate_recordset()
        self.assertEqual(batch.state, 'payment')
        self.assertIn('Paga para asegurar a tu grupo', page, 'Se abre la ventana de pago')
        self.assertIn(batch.reference, page)
        self.assertFalse(self.env['rss.insured'].search([('import_batch_id', '=', batch.id)]))
        # Transferencia con comprobante
        self.url_open('/mi-grupo/importar/%s/transferencia' % batch.id,
                      data={'csrf_token': csrf, 'reference': 'RASTREO-998', 'transfer_date': '29/10/2026'},
                      files={'proof': ('comprobante.pdf', b'%PDF-1.4 comprobante', 'application/pdf')})
        batch.invalidate_recordset()
        self.assertEqual(batch.state, 'verifying')
        self.assertIn('Estamos revisando tu pago', self.url_open('/mi-grupo/importar/%s' % batch.id).text)
        batch.action_validate_transfer()
        self.assertEqual(batch.state, 'done')
        self.assertEqual(batch.created_count, 2)
        self.assertEqual(batch.member_id, self.capt)
        self.assertEqual(batch.rss_payment_id.state, 'confirmed')
        self.assertEqual(batch.rss_payment_id.amount, batch.amount_total)

    def test_04_non_capturer_cannot_upload(self):
        self.authenticate('jefe7b@example.com', 'clave-jefe7b')
        page = self.url_open('/mi-grupo').text
        csrf = re.search(r'csrf_token: "([^"]+)"|name="csrf_token" value="([^"]+)"', page)
        token = (csrf.group(1) or csrf.group(2)) if csrf else ''
        self.url_open('/mi-grupo/importar', data={'csrf_token': token, 'privacy': '1'},
                      files={'file': ('l.xlsx', b'PK', 'application/octet-stream')})
        self.assertFalse(self.env['rss.import.batch'].search([('group_id', '=', self.g7.id)]))

    def test_05_capture_window(self):
        self.policy.write({'capture_window_enforced': True, 'capture_day_from': 1, 'capture_day_to': 1})
        if date.today().day == 1:
            return
        self.authenticate('secre7@example.com', 'clave-secre7')
        self.assertIn('La captura está abierta del día 1 al 1', self.url_open('/mi-grupo/importar').text)


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestCapturePayment(_RssPortalCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()
        cls.policy.write({'tariff_ids': [
            Command.create({'name': 'T1', 'date_from': date(2026, 8, 19), 'date_to': date(2026, 11, 18), 'amount': 100}),
            Command.create({'name': 'T2', 'date_from': date(2026, 11, 19), 'date_to': date(2027, 2, 18), 'amount': 75}),
            Command.create({'name': 'T3', 'date_from': date(2027, 2, 19), 'date_to': date(2027, 5, 18), 'amount': 50}),
            Command.create({'name': 'T4', 'date_from': date(2027, 5, 19), 'date_to': date(2027, 8, 18), 'amount': 35}),
        ], 'mp_fee_percent': 3.49, 'mp_fee_fixed': 4.0, 'mp_fee_vat': 16.0, 'mp_pass_fee': True})

    def _batch(self, n=2):
        batch = self.env['rss.import.batch'].create_manual(self.g7, rows=n)
        for i, line in enumerate(batch.line_ids):
            line.write({'first_name': 'Persona%s' % i, 'last_name_father': 'Prueba', 'last_name_mother': 'Pago',
                        'birthdate': date(2014, 1, 1 + i), 'sex': 'male', 'tutor_name': 'Tutor',
                        'tutor_phone': '2220000000'})
        batch.action_validate()
        return batch

    def test_01_tariff_by_quarter(self):
        from freezegun import freeze_time
        for day, unit in (('2026-10-29', 100), ('2026-11-19', 75), ('2027-03-01', 50), ('2027-08-18', 35)):
            with freeze_time(day):
                batch = self._batch(2)
                batch.action_request_payment()
                self.assertEqual(batch.unit_amount, unit, day)
                self.assertEqual(batch.amount_total, unit * 2, day)

    def test_02_mercado_pago_commission_covers_net(self):
        total, commission = self.policy._mercado_pago_total(1000)
        self.assertAlmostEqual(total, 1047.03, places=2)
        net = total - (total * 0.0349 + 4) * 1.16
        self.assertAlmostEqual(net, 1000, places=1)
        self.policy.mp_pass_fee = False
        self.assertEqual(self.policy._mercado_pago_total(1000), (1000, 0.0))

    def test_03_reject_gives_same_day_and_expiry(self):
        from freezegun import freeze_time
        with freeze_time('2026-10-29 15:00:00'):
            batch = self._batch(1)
            batch.action_request_payment()
            batch.action_submit_transfer('X1', files=[('c.pdf', b'%PDF x', 'application/pdf')])
            batch.rejection_reason = 'No aparece en el estado de cuenta'
            batch.action_reject_transfer()
            self.assertEqual(batch.state, 'payment')
            self.assertTrue(batch.message_ids.filtered(lambda m: 'solo hoy' in (m.body or '')))
        with freeze_time('2026-10-30 08:00:00'):
            self.env['rss.import.batch']._cron_expire_unpaid()
            self.assertEqual(batch.state, 'expired')
            self.assertFalse(self.env['rss.insured'].search([('import_batch_id', '=', batch.id)]))
            with self.assertRaises(Exception):
                batch.action_submit_transfer('X2', files=[('c.pdf', b'%PDF x', 'application/pdf')])

    def test_04_mercadopago_transaction_confirms_batch(self):
        batch = self._batch(2)
        batch.action_request_payment()
        provider = self.env['payment.provider'].create({'name': 'Prueba RSS', 'code': 'none', 'state': 'test'})
        method = self.env['payment.method'].with_context(active_test=False).search([], limit=1)
        tx = self.env['payment.transaction'].create({
            'provider_id': provider.id, 'payment_method_id': method.id, 'reference': batch.reference,
            'amount': batch.amount_mp_total, 'currency_id': batch.currency_id.id,
            'partner_id': self.env.user.partner_id.id,
        })
        tx._set_done()
        tx._rss_close_import_batches()   # lo que hace _post_process (sin la contabilización de Odoo)
        self.assertEqual(batch.state, 'done')
        self.assertEqual(batch.payment_method, 'mercadopago')
        self.assertEqual(batch.created_count, 2)
        self.assertEqual(batch.rss_payment_id.mp_commission, batch.mp_commission)
        self.assertEqual(set(self.env['rss.insured'].search([('import_batch_id', '=', batch.id)]).mapped('state')),
                         {'active'})

    def test_05_mp_underpaid_is_not_accepted(self):
        batch = self._batch(1)
        batch.action_request_payment()
        provider = self.env['payment.provider'].create({'name': 'Prueba RSS 2', 'code': 'none', 'state': 'test'})
        tx = self.env['payment.transaction'].create({
            'provider_id': provider.id, 'payment_method_id': self.env['payment.method'].with_context(active_test=False).search([], limit=1).id,
            'reference': batch.reference, 'amount': batch.amount_total,  # sin la comisión
            'currency_id': batch.currency_id.id, 'partner_id': self.env.user.partner_id.id,
        })
        tx._set_done()
        tx._rss_close_import_batches()
        self.assertEqual(batch.state, 'payment')

    def test_06_blank_manual_rows_and_back_to_review(self):
        batch = self.env['rss.import.batch'].create_manual(self.g7, rows=3)
        self.assertEqual(len(batch.line_ids), 3)
        self.assertTrue(all(l._is_blank() for l in batch.line_ids))
        batch.line_ids[0].write({'first_name': 'Uno', 'last_name_father': 'A', 'birthdate': date(2014, 2, 2),
                                 'sex': 'female', 'tutor_name': 'T', 'tutor_phone': '1'})
        batch.line_ids.filtered(lambda l: l._is_blank()).unlink()
        batch.action_validate()
        batch.action_request_payment()
        batch.action_back_to_review()
        self.assertEqual(batch.state, 'review')


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestPortalAppAndCertificates(_RssPortalCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()
        cls.capt = cls._member(cls.g7, 'secretary', 'capt7@example.com', capturer=True)
        cls.jefe = cls._member(cls.g7, 'leader', 'jefeapp7@example.com')
        cls.capt3 = cls._member(cls.g3, 'treasurer', 'capt3@example.com', capturer=True)
        partner = cls.env['res.partner'].create({'name': 'Asesor Cert', 'email': 'asesorcert@example.com'})
        cls.advisor = cls.env['rss.advisor'].create({'partner_id': partner.id})
        cls.advisor.action_grant_portal()
        cls.advisor.user_id.password = 'clave-asesorcert'

    def _certs_pdf(self):
        return _pdf([
            ['CERTIFICADO INDIVIDUAL No. AP-0101', 'Asegurado: GARCIA LOPEZ MARIA FERNANDA'],   # grupo 7
            ['CERTIFICADO INDIVIDUAL No. AP-0102', 'Asegurado: JOSE LUIS DE LA CRUZ RAMOS'],    # grupo 7
            ['CERTIFICADO INDIVIDUAL No. AP-0103', 'Asegurado: PEREZ SOTO ANA', 'Nac: 05/01/2013'],  # grupo 3
        ])

    def _csrf(self, url):
        return re.search(r'name="csrf_token" value="([^"]+)"', self.url_open(url).text).group(1)

    def test_01_app_manifest_icons_and_entry(self):
        res = self.url_open('/seguro-app/manifest.webmanifest')
        self.assertEqual(res.status_code, 200)
        manifest = res.json()
        self.assertEqual(manifest['id'], '/seguro-app')
        self.assertEqual(manifest['name'], 'Red Seguro Scout')
        for icon in manifest['icons']:
            self.assertEqual(self.url_open(icon['src']).status_code, 200, icon['src'])
        self.assertIn('/web/login', self.url_open('/seguro-app').url)
        self.authenticate('capt7@example.com', 'clave-capt7')
        self.assertTrue(self.url_open('/seguro-app').url.endswith('/mi-grupo'))
        page = self.url_open('/mi-grupo').text
        self.assertIn('/seguro-app/manifest.webmanifest', page)
        self.assertIn('Instala la app Red Seguro Scout', page)
        self.assertNotIn('/scouts/manifest.webmanifest', page)
        self.authenticate('asesorcert@example.com', 'clave-asesorcert')
        self.assertTrue(self.url_open('/seguro-app').url.endswith('/seguro'))

    def test_02_three_capture_options(self):
        self.authenticate('capt7@example.com', 'clave-capt7')
        page = self.url_open('/mi-grupo/importar').text
        for text in ('A mano', 'Leer Excel', 'Leer PDF'):
            self.assertIn(text, page)
        self.authenticate('jefeapp7@example.com', 'clave-jefeapp7')
        self.assertIn('Leer Excel', self.url_open('/mi-grupo/importar').text, 'Por omisión cargan los tres')
        self.g7.portal_capture_mode = 'capturer'
        page = self.url_open('/mi-grupo/importar').text
        self.assertNotIn('Leer Excel', page)
        self.assertIn('capt7', page.lower())

    def test_03_group_uploads_certificates_only_own_group(self):
        self.authenticate('capt7@example.com', 'clave-capt7')
        csrf = self._csrf('/mi-grupo/certificados')
        self.url_open('/mi-grupo/certificados', data={'csrf_token': csrf},
                      files={'files': ('certificados.pdf', self._certs_pdf(), 'application/pdf')})
        batch = self.env['rss.certificate.batch'].search([('source', '=', 'group')], limit=1)
        self.assertEqual(batch.member_id, self.capt)
        lines = batch.line_ids.sorted('page_from')
        self.assertEqual(lines[0].insured_id, self.maria)
        self.assertEqual(lines[1].insured_id, self.jose)
        self.assertFalse(lines[2].insured_id, 'Una persona de otro grupo no se identifica en este grupo')
        page = self.url_open('/mi-grupo/certificados/%s' % batch.id).text
        self.assertIn('AP-0101', page)
        self.assertNotIn(self.ana_a.name + ' ·', page, 'No se ofrecen asegurados de otros grupos')
        # Intentar asignar a alguien de otro grupo: se rechaza
        self.url_open('/mi-grupo/certificados/%s' % batch.id, data={
            'csrf_token': csrf, 'action': 'save', 'l%s_insured' % lines[2].id: str(self.ana_a.id)})
        lines[2].invalidate_recordset()
        self.assertFalse(lines[2].insured_id)
        self.assertEqual(self.url_open('/mi-grupo/certificados/%s/linea/%s.pdf' % (batch.id, lines[0].id)).status_code, 200)
        self.url_open('/mi-grupo/certificados/%s' % batch.id, data={
            'csrf_token': csrf, 'action': 'attach', 'l%s_skip' % lines[2].id: '1'})
        self.maria.invalidate_recordset()
        self.assertEqual(self.maria.certificate_number, 'AP-0101')
        self.assertFalse(self.ana_a.certificate_attachment_id)
        # Otro grupo no ve esta carga
        self.authenticate('capt3@example.com', 'clave-capt3')
        self.assertNotIn('AP-0101', self.url_open('/mi-grupo/certificados/%s' % batch.id).text)
        self.assertEqual(self.url_open('/mi-grupo/certificados/%s/linea/%s.pdf' % (batch.id, lines[0].id)).status_code, 404)

    def test_04_non_capturer_cannot_upload_certificates(self):
        self.g7.portal_capture_mode = 'capturer'
        self.authenticate('jefeapp7@example.com', 'clave-jefeapp7')
        page = self.url_open('/mi-grupo/certificados').text
        self.assertNotIn('Leer certificados', page)
        csrf = re.search(r'csrf_token: "([^"]+)"|name="csrf_token" value="([^"]+)"', page)
        token = (csrf.group(1) or csrf.group(2)) if csrf else ''
        self.url_open('/mi-grupo/certificados', data={'csrf_token': token},
                      files={'files': ('c.pdf', self._certs_pdf(), 'application/pdf')})
        self.assertFalse(self.env['rss.certificate.batch'].search([('source', '=', 'group')]))

    def test_05_advisor_uploads_certificates_all_groups(self):
        self.authenticate('asesorcert@example.com', 'clave-asesorcert')
        csrf = self._csrf('/seguro/certificados')
        self.url_open('/seguro/certificados', data={'csrf_token': csrf},
                      files={'files': ('certificados.pdf', self._certs_pdf(), 'application/pdf')})
        batch = self.env['rss.certificate.batch'].search([('advisor_id', '=', self.advisor.id)], limit=1)
        self.assertEqual(batch.source, 'advisor')
        self.assertEqual(batch.line_ids.sorted('page_from')[2].insured_id, self.ana_a, 'Fecha de nacimiento desempata')
        self.url_open('/seguro/certificados/%s' % batch.id, data={'csrf_token': csrf, 'action': 'attach'})
        self.ana_a.invalidate_recordset()
        self.assertEqual(self.ana_a.certificate_number, 'AP-0103')
        res = self.url_open('/seguro/asegurados/%s/certificado' % self.ana_a.id)
        self.assertTrue(res.content.startswith(b'%PDF'))


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestPortalLoginAndPassword(_RssPortalCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()

    def test_login_follows_email_and_admin_sets_password(self):
        # Caso real: se dio acceso con un correo provisional y luego se corrigió el correo.
        partner = self.env['res.partner'].create({'name': 'Prueba Grupo', 'email': 'example@example.com'})
        member = self.env['rss.group.member'].create({'group_id': self.g7.id, 'partner_id': partner.id,
                                                     'role': 'leader'})
        action = member.action_grant_portal()
        self.assertEqual(action['res_model'], 'rss.portal.password', 'Tras dar acceso pide la contraseña')
        self.assertEqual(member.portal_login, 'example@example.com')
        member.email = 'Creart.3dp@gmail.com'
        self.assertEqual(member.user_id.login, 'creart.3dp@gmail.com', 'El usuario entra con el correo nuevo')
        # Cambio hecho desde el contacto también se refleja
        partner.email = 'creart.3dp+g1@gmail.com'
        self.assertEqual(member.user_id.login, 'creart.3dp+g1@gmail.com')

        wiz = self.env['rss.portal.password'].with_context(
            **member.action_set_password()['context']).create(
            {'password': 'corta', 'confirm': 'corta'})
        with self.assertRaises(UserError):
            wiz.action_apply()
        wiz.write({'password': 'RumboSeguro2026', 'confirm': 'RumboSeguro2026'})
        wiz.action_apply()
        user = member.user_id
        user.with_user(user)._check_credentials({'type': 'password', 'password': 'RumboSeguro2026'},
                                                {'interactive': False})
        with self.assertRaises(Exception):
            user.with_user(user)._check_credentials({'type': 'password', 'password': 'otra-cosa'},
                                                    {'interactive': False})

    def test_email_already_used_is_rejected(self):
        self._member(self.g7, 'treasurer', 'ocupado@example.com')
        other = self._member(self.g3, 'leader', 'libre@example.com')
        with self.assertRaises(UserError):
            other.email = 'ocupado@example.com'


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestInsuranceLogin(_RssPortalCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()
        cls.capturista = cls._member(cls.g7, 'secretary', 'seguro7@example.com', capturer=True)

    def test_login_page_shows_insurance_brand(self):
        res = self.url_open('/seguro-app/entrar')
        self.assertIn('redirect=/seguro-app', res.url)
        self.assertIn('o_rss_login_brand', res.text)
        self.assertIn('/seguro-app/manifest.webmanifest', res.text)
        # Este celular ya usó la app del seguro: la entrada general y «restablecer contraseña» se ven del seguro
        remembered = self.url_open('/web/login')
        self.assertIn('o_rss_login_brand', remembered.text)
        self.assertIn('/seguro-app/manifest.webmanifest', remembered.text)
        reset = self.url_open('/web/reset_password')
        self.assertIn('o_rss_login_brand', reset.text)
        self.assertNotIn('o_rss_login_brand', self.url_open('/web/login?app=bitascout').text)
        # Un celular nuevo ve la entrada general sin cambios
        self.opener.cookies.clear()
        plain = self.url_open('/web/login')
        self.assertNotIn('o_rss_login_brand', plain.text, 'La entrada general no cambia')

    def test_portal_user_lands_in_insurance_portal(self):
        self.authenticate('seguro7@example.com', 'clave-seguro7')
        res = self.url_open('/mi-grupo')
        self.assertIn('emblema_red_seguro_scout.png', res.text)
        self.assertIn('Rumbo Seguro', res.text)
        from odoo.addons.red_seguro_scout.controllers.app import RssLoginHome
        self.assertTrue(self.env['rss.group.member']._rss_is_login_redirect('/mi-grupo/importar'))
        self.assertFalse(self.env['rss.group.member']._rss_is_login_redirect('/my'))
        self.assertTrue(RssLoginHome)


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestCertificateData(_RssPortalCommon, TransactionCase):
    """Certificado «AP Colectivo» (Pan-American): 3 asegurados, cada uno con su reverso."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()
        env = cls.env
        cls.angel = env['rss.insured'].create({
            'first_name': 'Ángel', 'last_name_father': 'Rodríguez', 'last_name_mother': 'Bandera',
            'birthdate': date(2013, 5, 2), 'sex': 'male', 'group_id': cls.g7.id,
            'tutor_name': 'Laura Karina Pérez Juárez', 'tutor_phone': '6141234567', 'date_start': date(2026, 10, 7)})
        # Una asegurada con el mismo nombre que la contratante: no debe recibir el certificado de Ángel
        cls.laura = env['rss.insured'].create({
            'first_name': 'Laura', 'second_name': 'Karina', 'last_name_father': 'Pérez', 'last_name_mother': 'Juárez',
            'birthdate': date(2012, 1, 1), 'sex': 'female', 'group_id': cls.g7.id,
            'tutor_name': 'Tutor', 'tutor_phone': '2220000000', 'date_start': date(2026, 9, 1)})

    def test_reads_policy_contractor_and_coverages(self):
        import os
        path = os.path.join(os.path.dirname(__file__), 'data', 'certificados_ap_colectivo.pdf')
        with open(path, 'rb') as f:
            pdf = self.env['ir.attachment'].create({'name': 'certificados.pdf', 'raw': f.read(),
                                                     'mimetype': 'application/pdf'})
        batch = self.env['rss.certificate.batch'].create({'group_ids': [Command.set(self.g7.ids)],
                                                          'file_ids': [Command.set(pdf.ids)]})
        batch.action_read()
        self.assertEqual(len(batch.line_ids), 3, 'Un certificado por asegurado (el reverso se une)')
        by_cert = {l.certificate_number: l for l in batch.line_ids}
        self.assertEqual(by_cert['392'].insured_id, self.angel, 'Apellidos primero: RODRÍGUEZ BANDERA ÁNGEL')
        self.assertEqual(by_cert['392'].pages, '1–2')
        self.assertEqual(by_cert['393'].insured_id, self.maria)
        self.assertEqual(by_cert['394'].insured_id, self.jose, 'Partícula «de la Cruz»')
        line = by_cert['392']
        self.assertEqual(line.policy_number, 'AP30101716')
        self.assertEqual(line.contractor_name, 'LAURA KARINA PEREZ JUAREZ')
        self.assertEqual(line.contractor_rfc, 'PEJL801115AN0')
        self.assertIn('C.P. 31060', line.contractor_address)
        self.assertEqual(line.group_id, self.g7)
        batch.action_attach()
        a = self.angel
        self.assertTrue(a.certificate_attachment_id)
        self.assertEqual((a.cert_policy_number, a.certificate_number), ('AP30101716', '392'))
        self.assertEqual((a.cert_date_start, a.cert_date_end), (date(2026, 10, 7), date(2027, 8, 19)))
        self.assertEqual((a.cert_policy_date_start, a.cert_policy_date_end), (date(2026, 8, 19), date(2027, 8, 19)))
        self.assertEqual(a.cert_insurer, 'Pan-American México')
        self.assertEqual(a.contractor_rfc, 'PEJL801115AN0')
        cov = {c.name: c for c in a.coverage_ids}
        self.assertEqual(cov['Indemnización por muerte accidental'].sum_insured, 100000)
        self.assertEqual(cov['Pérdidas Orgánicas Escala B'].sum_insured, 100000)
        gm = cov['Reembolso de Gastos Médicos por Accidente']
        self.assertEqual((gm.sum_insured, gm.deductible), (50000, 300))
        self.assertEqual(a.coverage_total, 250000)
        self.assertFalse(self.laura.certificate_attachment_id, 'La contratante homónima no recibe certificado')
        from odoo.tools.pdf import PdfFileReader
        import io
        self.assertEqual(len(PdfFileReader(io.BytesIO(a.certificate_attachment_id.raw)).pages), 2,
                         'Hoja 1 (datos) + hoja 2 (beneficiarios y firmas)')
        self.assertEqual(by_cert['394'].pages, '5–6')
        # Volver a separar reemplaza el certificado por uno con sus dos hojas
        old = a.certificate_attachment_id
        batch.action_rebuild()
        self.assertNotEqual(a.certificate_attachment_id, old)
        self.assertEqual(len(PdfFileReader(io.BytesIO(a.certificate_attachment_id.raw)).pages), 2)


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestInsuredListAllSources(_RssPortalCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()
        cls.capturista = cls._member(cls.g7, 'secretary', 'lista7@example.com', capturer=True)
        cls.maria.capture_source = 'excel'          # cargada por el grupo en el portal
        # una persona cargada en el portal que aún no paga
        batch = cls.env['rss.import.batch'].sudo().create_manual(
            cls.g7, member=cls.capturista, privacy_accepted=True, rows=1)
        batch.line_ids[:1].write({'first_name': 'Pendiente', 'last_name_father': 'Sinpago', 'last_name_mother': 'Hoy',
                                  'birthdate': date(2015, 2, 2), 'sex': 'male', 'tutor_name': 'Tutor',
                                  'tutor_phone': '2221112233'})

    def test_list_shows_platform_and_portal(self):
        self.authenticate('lista7@example.com', 'clave-lista7')
        html = self.url_open('/mi-grupo/asegurados').text
        self.assertIn('José Luis de la Cruz Ramos', html, 'Alta hecha en la plataforma')
        self.assertIn('María Fernanda García López', html)
        self.assertIn('Portal · Excel', html)
        self.assertIn('Plataforma', html)
        self.assertIn('Dar de alta un asegurado', html)
        self.assertIn('Pendiente Sinpago Hoy', html, 'Aparece en trámite')
        only_portal = self.url_open('/mi-grupo/asegurados?origen=portal').text
        self.assertIn('María Fernanda García López', only_portal)
        self.assertNotIn('José Luis de la Cruz Ramos', only_portal)
        self.assertNotIn('Ana', only_portal, 'Nunca muestra otros grupos')


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestPortalAlta(_RssPortalCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()
        cls.cap = cls._member(cls.g7, 'secretary', 'alta7@example.com', capturer=True)
        partner = cls.env['res.partner'].create({'name': 'Asesora Alta', 'email': 'asesora.alta@example.com'})
        cls.advisor = cls.env['rss.advisor'].create({'partner_id': partner.id})
        cls.advisor.action_grant_portal()
        cls.advisor.user_id.password = 'asesora-alta-1'

    def _csrf(self, url):
        return re.search(r'name="csrf_token" value="([^"]+)"', self.url_open(url).text).group(1)

    def _person(self, **extra):
        data = {'first_name': 'Valeria', 'last_name_father': 'Soto', 'last_name_mother': 'Ruiz',
                'birthdate': '2015-04-10', 'sex': 'female', 'tutor_name': 'Marta Ruiz',
                'tutor_relationship': 'mother', 'tutor_phone': '2225550101', 'privacy': '1', 'next': 'another'}
        data.update(extra)
        return data

    def test_group_alta_goes_to_todays_capture(self):
        self.authenticate('alta7@example.com', 'clave-alta7')
        html = self.url_open('/mi-grupo/asegurados').text
        self.assertIn('/mi-grupo/alta', html)
        res = self.url_open('/mi-grupo/alta', data=dict(self._person(), csrf_token=self._csrf('/mi-grupo/alta')))
        self.assertIn('/mi-grupo/alta', res.url)
        res = self.url_open('/mi-grupo/alta', data=dict(self._person(first_name='Pablo', sex='male', next='done'),
                                                        csrf_token=self._csrf('/mi-grupo/alta')))
        batch = self.env['rss.import.batch'].search([('group_id', '=', self.g7.id), ('source', '=', 'manual')])
        self.assertEqual(len(batch), 1, 'Las altas del día van en una sola captura')
        self.assertEqual(sorted(batch.line_ids.mapped('first_name')), ['Pablo', 'Valeria'])
        self.assertIn('/mi-grupo/importar/%s' % batch.id, res.url)
        self.assertEqual(batch.line_ids.filtered(lambda l: l.first_name == 'Valeria').tutor_relationship, 'mother')
        # sin datos obligatorios no se agrega
        res = self.url_open('/mi-grupo/alta', data=dict(self._person(first_name=''), csrf_token=self._csrf('/mi-grupo/alta')))
        self.assertIn('Falta', res.text)
        self.assertEqual(len(batch.line_ids), 2)
        # menor sin tutor: sí se agrega, con el tutor en blanco
        self.url_open('/mi-grupo/alta', data=dict(self._person(first_name='Leo', sex='male', tutor_name='', tutor_phone='',
                                                               tutor_relationship=''),
                                                  csrf_token=self._csrf('/mi-grupo/alta')))
        leo = batch.line_ids.filtered(lambda l: l.first_name == 'Leo')
        self.assertTrue(leo)
        self.assertNotEqual(leo.status, 'error')
        self.assertFalse(leo.tutor_name)

    @staticmethod
    def _jpeg_data_url(w=90, h=120):
        import base64, io
        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGB', (w, h), (120, 160, 200)).save(buf, 'JPEG')
        return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()

    def test_photo_camera_or_file_in_alta(self):
        """Foto al dar de alta: botón de cámara y de archivo; la foto viaja a la captura y al asegurado."""
        self.authenticate('alta7@example.com', 'clave-alta7')
        form = self.url_open('/mi-grupo/alta').text
        self.assertIn('capture="environment"', form, 'Botón «Tomar foto»')
        self.assertIn('Elegir de galería o archivo', form)
        self.assertIn('multipart/form-data', form)
        self.url_open('/mi-grupo/alta', data=dict(self._person(first_name='Foto', next='done'),
                                                  photo_data=self._jpeg_data_url(),
                                                  csrf_token=self._csrf('/mi-grupo/alta')))
        line = self.env['rss.import.line'].search([('first_name', '=', 'Foto')])
        self.assertTrue(line.photo, 'La foto queda en la captura')
        vals = line.batch_id._line_insured_vals(line, 'portal', line.create_date.date())
        self.assertTrue(vals['photo'], 'y pasa al asegurado al darlo de alta')
        # Foto que no es imagen: no se guarda y avisa
        res = self.url_open('/mi-grupo/alta', data=dict(self._person(first_name='Mala', next='done'),
                                                        csrf_token=self._csrf('/mi-grupo/alta')),
                            files={'photo': ('x.png', b'no es imagen', 'image/png')})
        self.assertIn('No se pudo leer la foto', res.text)
        # Asesor: el asegurado nace con su foto
        self.authenticate('asesora.alta@example.com', 'asesora-alta-1')
        self.url_open('/seguro/alta', data=dict(self._person(first_name='Fotito', group_id=str(self.g3.id), next='done'),
                                                photo_data=self._jpeg_data_url(), csrf_token=self._csrf('/seguro/alta')))
        new = self.env['rss.insured'].search([('first_name', '=', 'Fotito')])
        self.assertTrue(new.photo)

    def test_advisor_sees_preinscritos_and_registers(self):
        self.authenticate('asesora.alta@example.com', 'asesora-alta-1')
        home = self.url_open('/seguro').text
        self.assertIn('En preinscripción', home)
        lst = self.url_open('/seguro/asegurados').text
        self.assertIn('María Fernanda García López', lst, 'Por omisión muestra todos, también preinscritos')
        self.assertIn('/seguro/alta', lst)
        res = self.url_open('/seguro/alta', data=dict(self._person(group_id=str(self.g3.id), next='done'),
                                                      csrf_token=self._csrf('/seguro/alta')))
        new = self.env['rss.insured'].search([('first_name', '=', 'Valeria'), ('group_id', '=', self.g3.id)])
        self.assertTrue(new)
        self.assertEqual((new.state, new.capture_source), ('draft', 'portal'))
        self.assertIn('grupo=%s' % self.g3.id, res.url)
        # repetido: no se duplica
        self.url_open('/seguro/alta', data=dict(self._person(group_id=str(self.g7.id)), csrf_token=self._csrf('/seguro/alta')))
        self.assertEqual(self.env['rss.insured'].search_count([('first_name', '=', 'Valeria')]), 1)

    def test_reread_fixes_glued_policy(self):
        import os
        path = os.path.join(os.path.dirname(__file__), 'data', 'certificados_ap_colectivo.pdf')
        from odoo.tools.pdf import PdfFileReader, PdfFileWriter
        import io
        with open(path, 'rb') as f:
            reader = PdfFileReader(io.BytesIO(f.read()))
        writer = PdfFileWriter()
        writer.add_page(reader.pages[2])
        out = io.BytesIO()
        writer.write(out)
        att = self.env['ir.attachment'].create({'name': 'c.pdf', 'raw': out.getvalue(), 'mimetype': 'application/pdf',
                                                'res_model': 'rss.insured', 'res_id': self.maria.id})
        self.maria.write({'certificate_attachment_id': att.id, 'cert_policy_number': 'AP30101716CERTIFICADO'})
        self.maria.action_reread_certificate()
        self.assertEqual(self.maria.cert_policy_number, 'AP30101716')
        self.assertEqual(self.maria.cert_date_start, date(2026, 10, 7))
        self.assertEqual(len(self.maria.coverage_ids), 3)


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestRepresentativesLikeAdvisor(_RssPortalCommon, HttpCase):
    """Jefe, secretario y tesorero hacen lo mismo que el asesor, solo con su grupo."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()
        cls.teso = cls._member(cls.g7, 'treasurer', 'teso.adv@example.com')       # no es capturista
        cls._member(cls.g7, 'leader', 'jefe.adv@example.com', capturer=True)
        cls.claim = cls.env['rss.claim'].create({'insured_id': cls.maria.id, 'injury_type': 'fracture',
                                                 'date_accident': date(2026, 10, 1)})
        cls.report = cls.env['rss.report'].create({'policy_id': cls.policy.id, 'period_date': date(2026, 10, 1)})
        cls.report.action_generate()

    def _csrf(self, url):
        return re.search(r'name="csrf_token" value="([^"]+)"', self.url_open(url).text).group(1)

    def test_treasurer_can_do_everything_for_own_group(self):
        self.authenticate('teso.adv@example.com', 'clave-teso.adv')
        self.assertIn('/mi-grupo/alta', self.url_open('/mi-grupo/asegurados').text, 'Puede dar de alta')
        self.assertEqual(self.url_open('/mi-grupo/alta').status_code, 200)
        for url in ('/mi-grupo/relaciones', '/mi-grupo/pagos', '/mi-grupo/certificados'):
            res = self.url_open(url)
            self.assertEqual(res.status_code, 200, url)
        self.assertTrue(self.report.line_ids.filtered(lambda l: l.group_id == self.g3), 'La relación trae otros grupos')
        if self.report:
            html = self.url_open('/mi-grupo/relaciones').text
            self.assertIn('/mi-grupo/relaciones/%s/excel' % self.report.id, html)
            xlsx = self.url_open('/mi-grupo/relaciones/%s/excel' % self.report.id)
            self.assertTrue(xlsx.content.startswith(b'PK'))
            import io
            import openpyxl
            ws = openpyxl.load_workbook(io.BytesIO(xlsx.content)).active
            text = ' '.join(str(c.value) for row in ws.iter_rows() for c in row if c.value).upper()
            self.assertIn('GARCÍA', text)
            self.assertNotIn('PÉREZ', text, 'Nunca exporta asegurados de otro grupo')
            self.assertNotIn('LAN NAKUH', text)
        if self.claim:
            url = '/mi-grupo/siniestros/%s' % self.claim.id
            self.assertIn('Guardar seguimiento', self.url_open(url).text)
            self.url_open(url, data={'csrf_token': self._csrf(url), 'state': 'in_process',
                                     'insurer_claim_number': 'SIN-77', 'note': 'Se entregó informe médico'},
                          files={'documents': ('informe.pdf', b'%PDF-1.4 x', 'application/pdf')})
            self.assertEqual((self.claim.state, self.claim.insurer_claim_number), ('in_process', 'SIN-77'))
            att = self.claim.attachment_ids[:1]
            self.assertEqual(self.url_open('%s/documento/%s' % (url, att.id)).status_code, 200)


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestDashboard(_RssPortalCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()
        cls._member(cls.g7, 'treasurer', 'dash7@example.com')
        cls.env['rss.claim'].create({'insured_id': cls.maria.id, 'injury_type': 'fracture', 'date_accident': date(2026, 10, 1)})

    def test_data_scoped_and_consistent(self):
        D = self.env['rss.dashboard']
        all_data = D.get_data()
        g7 = D.get_data(self.g7.ids)
        self.assertEqual(len(g7['groups']), 1)
        self.assertEqual(g7['kpis']['insured'], self.env['rss.insured'].search_count(
            [('group_id', '=', self.g7.id), ('state', '=', 'active')]))
        self.assertGreater(all_data['kpis']['total'], g7['kpis']['total'])
        self.assertEqual(g7['kpis']['claims'], 1)
        piv = g7['pivots']['age_sex']
        self.assertEqual(sum(map(sum, piv['values'])), g7['kpis']['male'] + g7['kpis']['female'])
        xlsx = D.export_xlsx(all_data)
        self.assertTrue(xlsx.startswith(b'PK'))

    def test_group_portal_dashboard(self):
        self.authenticate('dash7@example.com', 'clave-dash7')
        res = self.url_open('/mi-grupo/tablero?tablero=kpi')
        self.assertEqual(res.status_code, 200)
        self.assertIn('data-rss-chart="sex"', res.text)
        self.assertIn('Tablas dinámicas', res.text)
        self.assertNotIn('Lan Nakuh', res.text, 'Solo su grupo')
        x = self.url_open('/mi-grupo/tablero.xlsx')
        self.assertTrue(x.content.startswith(b'PK'))
        import io
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(x.content))
        names = ' '.join(str(c.value) for c in wb['Por grupo']['A'] if c.value)
        self.assertIn('Ethnos', names)
        self.assertNotIn('Lan Nakuh', names)

    def test_backend_dashboard_tour_free(self):
        self.authenticate('admin', 'admin')
        x = self.url_open('/red-seguro-scout/tablero.xlsx')
        self.assertTrue(x.content.startswith(b'PK'))


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestOfficeListLoad(_RssPortalCommon, HttpCase):
    """La plataforma y el asesor cargan listas en Excel/PDF: quedan en preinscripción, sin cobro."""

    HEADERS = ['Grupo', 'Nombre', 'Apellido paterno', 'Apellido materno', 'Fecha de nacimiento', 'Sexo',
               'Padre o tutor', 'Teléfono de emergencia', 'Correo', 'CURP']
    ROWS = [
        ['7', 'Lucía', 'Mora', 'Paz', '04/04/2015', 'Mujer', '', '', '', ''],
        ['Lan Nakuh', 'Diego', 'Cano', 'Ríos', '07/07/2012', 'Hombre', 'Ana Ríos', '2221112233', '', ''],
        ['3', 'Sara', 'Cano', 'Ríos', '01/01/2014', 'Mujer', '', '', '', ''],
        ['99', 'Nadie', 'Sin', 'Grupo', '01/01/2014', 'Mujer', '', '', '', ''],
        ['7', 'María Fernanda', 'García', 'López', '12/03/2014', 'Mujer', '', '', '', ''],   # ya existe
    ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()
        partner = cls.env['res.partner'].create({'name': 'Asesor Carga', 'email': 'asesor.carga@example.com'})
        cls.advisor = cls.env['rss.advisor'].create({'partner_id': partner.id})
        cls.advisor.action_grant_portal()
        cls.advisor.user_id.password = 'asesor-carga-1'

    def test_platform_multi_group_file(self):
        import base64
        wiz = self.env['rss.import.wizard'].create({'file': base64.b64encode(_xlsx(self.ROWS, self.HEADERS)),
                                                    'filename': 'base.xlsx'})
        action = wiz.action_load()
        batches = self.env['rss.import.batch'].search([('office_load', '=', True)])
        self.assertEqual(set(batches.group_id.ids), {self.g7.id, self.g3.id}, 'Una carga por grupo')
        self.assertEqual(action['res_model'], 'rss.import.batch')
        b7 = batches.filtered(lambda b: b.group_id == self.g7)
        self.assertEqual(b7.line_count, 2)
        self.assertEqual(b7.duplicate_count, 1, 'María ya existía')
        self.assertFalse(b7.amount_total if 'amount_total' in b7._fields and b7.state != 'review' else 0)
        batches.action_save_draft()
        new = self.env['rss.insured'].search([('import_batch_id', 'in', batches.ids)])
        self.assertEqual(sorted(new.mapped('first_name')), ['Diego', 'Lucía', 'Sara'])
        self.assertEqual(set(new.mapped('state')), {'draft'})
        self.assertEqual(set(new.mapped('capture_source')), {'backend'})
        self.assertFalse(self.env['rss.payment'].search([('import_batch_id', 'in', batches.ids)]), 'Sin cobro')
        self.assertFalse(new.filtered(lambda i: i.first_name == 'Lucía').tutor_name, 'Tutor opcional')

    def test_platform_single_group_and_template(self):
        import base64
        from odoo.addons.red_seguro_scout.models.rss_parsers import build_template_xlsx, parse_excel
        rows, err = parse_excel(build_template_xlsx(with_group=True))
        self.assertFalse(err)
        self.assertEqual(rows[0]['group'], '7')
        rows = [r[1:] for r in self.ROWS[:3]]
        wiz = self.env['rss.import.wizard'].create({'group_id': self.g3.id, 'filename': 'g3.xlsx',
                                                    'file': base64.b64encode(_xlsx(rows, self.HEADERS[1:]))})
        wiz.action_load()
        batch = self.env['rss.import.batch'].search([('office_load', '=', True)])
        self.assertEqual(batch.group_id, self.g3, 'Con grupo elegido, todo va a ese grupo')
        self.assertEqual(batch.line_count, 3)

    def _csrf(self, url):
        return re.search(r'name="csrf_token" value="([^"]+)"', self.url_open(url).text).group(1)

    def test_advisor_uploads_and_saves(self):
        self.authenticate('asesor.carga@example.com', 'asesor-carga-1')
        self.assertIn('/seguro/cargar', self.url_open('/seguro/asegurados').text)
        self.assertTrue(self.url_open('/seguro/plantilla.xlsx').content.startswith(b'PK'))
        res = self.url_open('/seguro/cargar', data={'csrf_token': self._csrf('/seguro/cargar'), 'group_id': ''},
                            files={'file': ('base.xlsx', _xlsx(self.ROWS, self.HEADERS), 'application/octet-stream')})
        self.assertEqual(res.status_code, 200)
        batches = self.env['rss.import.batch'].search([('office_load', '=', True)])
        self.assertEqual(len(batches), 2)
        self.assertEqual(batches.advisor_id, self.advisor)
        b3 = batches.filtered(lambda b: b.group_id == self.g3)
        page = self.url_open('/seguro/cargas/%s' % b3.id).text
        self.assertIn('Guardar en preinscripción', page)
        form = {'csrf_token': self._csrf('/seguro/cargas/%s' % b3.id), 'action': 'save'}
        for line in b3.line_ids:
            form['l%s_include' % line.id] = '1'
        self.url_open('/seguro/cargas/%s' % b3.id, data=form)
        self.assertEqual(b3.state, 'done')
        saved = self.env['rss.insured'].search([('import_batch_id', '=', b3.id)])
        self.assertEqual(len(saved), 2)
        self.assertEqual(set(saved.mapped('capture_source')), {'excel'})


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestBoardsAndCredential(_RssPortalCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()
        cls._member(cls.g7, 'leader', 'cred7@example.com')
        mx = cls.env.ref('base.mx')
        puebla = cls.env['res.country.state'].search([('country_id', '=', mx.id), ('name', 'ilike', 'Puebla')], limit=1)
        cls.g7.write({'city': 'Puebla', 'state_id': puebla.id})
        partner = cls.env['res.partner'].create({'name': 'Asesor Cred', 'email': 'asesor.cred@example.com'})
        cls.advisor = cls.env['rss.advisor'].create({'partner_id': partner.id})
        cls.advisor.action_grant_portal()
        cls.advisor.user_id.password = 'asesor-cred-1'

    def test_board_data_and_render(self):
        board = self.env.ref('red_seguro_scout.board_padron')
        groups = self.env['rss.scout.group'].search([])
        data = board._board_data(groups, 'active_draft')
        self.assertEqual(data['total'], sum(r['count'] for r in data['groups']))
        self.assertEqual(sum(s['count'] for s in data['sections']), data['total'])
        self.assertNotIn('comunidad', [s['key'] for s in data['sections']])
        labels = [s['name'] for s in data['sections_sex']]
        self.assertIn('Gacelas', labels)
        self.assertIn('Tropa Femenil', labels)
        self.assertTrue(data['findings'])
        self.assertEqual(self.g7.seat_label, 'Puebla, Pue.')
        html = board.render_html(self.g7.ids)
        self.assertIn('Asegurados por Grupo', html)
        self.assertIn('Padrón Oficial de la Red', html)
        self.assertNotIn('Lan Nakuh', html, 'Filtrado a un grupo')
        # bloque nuevo creado por la administración
        self.env['rss.board.block'].create({'board_id': board.id, 'block_type': 'text', 'title': 'Avisos',
                                            'body': 'Renovación en agosto'})
        self.assertIn('Renovación en agosto', board.render_html())

    def test_section_by_age(self):
        from odoo.addons.red_seguro_scout.models.rss_common import section_for_age
        self.assertEqual([section_for_age(a) for a in (4, 8, 16, 20, 30)],
                         ['castores', 'manada', 'tropa', 'clan', 'dirigente'])

    def test_credential_values_and_pages(self):
        vals = self.maria._credential_values()
        self.assertIn('María', vals['first'])
        self.assertTrue(vals['qr'].startswith('data:image/png;base64,'))
        self.assertFalse(vals['photo'])
        self.assertTrue(self.maria.credential_token)
        # sin tutor: contacto de emergencia = jefe de grupo
        self.maria.write({'tutor_name': False, 'tutor_phone': False})
        self.g7.write({'leader_name': 'Jefe Siete', 'leader_phone': '2220001111'})
        vals = self.maria._credential_values()
        self.assertEqual((vals['em_name'], vals['em_role'], vals['em_phone']), ('Jefe Siete', 'Jefe de grupo', '2220001111'))
        # página pública del QR
        public = self.url_open('/credencial/%s' % self.maria.credential_token)
        self.assertEqual(public.status_code, 200)
        self.assertIn('Datos del<br/>Seguro', public.text)
        self.assertIn('no existe', self.url_open('/credencial/xxxxxxxxxxxxxxxx').text)
        # portal del grupo: ver y subir foto
        self.authenticate('cred7@example.com', 'clave-cred7')
        url = '/mi-grupo/asegurados/%s/credencial' % self.maria.id
        page = self.url_open(url)
        self.assertIn('Credencial del seguro', page.text)
        self.assertIn('nophoto', page.text, 'Sin foto usa el acomodo centrado')
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        import base64, io
        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGB', (60, 80), (200, 120, 90)).save(buf, 'PNG')
        self.url_open(url, data={'csrf_token': csrf}, files={'photo': ('foto.png', buf.getvalue(), 'image/png')})
        self.assertTrue(self.maria.photo)
        self.assertNotIn('who nophoto', self.url_open(url).text)
        # Foto ya reducida por el navegador (cámara del celular)
        self.maria.photo = False
        page = self.url_open(url)
        self.assertIn('capture="environment"', page.text)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        self.url_open(url, data={'csrf_token': csrf, 'photo_data': TestPortalAlta._jpeg_data_url()})
        self.assertTrue(self.maria.photo)
        # otro grupo: no
        self.assertIn('/mi-grupo/asegurados', self.url_open('/mi-grupo/asegurados/%s/credencial' % self.ana_a.id).url)
        # tablero del portal con pestañas
        res = self.url_open('/mi-grupo/tablero')
        self.assertIn('Informe de Asegurados', res.text)
        self.assertIn('Indicadores', res.text)

    def test_advisor_board_tabs(self):
        self.authenticate('asesor.cred@example.com', 'asesor-cred-1')
        res = self.url_open('/seguro/tablero')
        self.assertIn('Asegurados por Grupo', res.text)
        self.assertIn('Operación del Seguro', res.text)
        self.assertIn('/credencial', self.url_open('/seguro/asegurados').text)


@tagged('post_install', '-at_install', 'red_seguro_scout')
class TestCredentialDownload(_RssPortalCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()

    def test_backend_download_filename(self):
        """La descarga desde la plataforma (botón Credencial / Imprimir) arma el nombre del archivo."""
        from odoo.tools.safe_eval import safe_eval, time
        report = self.env.ref('red_seguro_scout.action_report_rss_credential')
        name = safe_eval(report.print_report_name, {'object': self.maria, 'time': time})
        self.assertIn('Credencial - ', name)
        board = self.env.ref('red_seguro_scout.action_report_rss_board')
        self.assertIn('Tablero', safe_eval(board.print_report_name,
                                           {'object': self.env.ref('red_seguro_scout.board_padron'), 'time': time}))
        action = self.maria.action_print_credential()
        self.assertIn(action['type'], ('ir.actions.report', 'ir.actions.act_window'))  # sin diseño de documento configurado, Odoo pide configurarlo primero
