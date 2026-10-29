# -*- coding: utf-8 -*-
import base64
import io
import re
from datetime import date

from odoo.exceptions import ValidationError
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
            [5, 'Solís', 'Mar', 'Iker', '09/01/2016', 10, 'H', '', '', ''],                                     # falta tutor
        ]
        batch = self.env['rss.import.batch'].create_from_file(self.g7, 'lista.xlsx', _xlsx(rows, headers))
        lines = batch.line_ids.sorted('sequence')
        self.assertEqual(lines.mapped('status'), ['duplicate', 'ok', 'duplicate', 'error', 'error'])
        self.assertEqual(lines[0].duplicate_insured_id, self.maria)
        self.assertEqual(lines[1].sex, 'female', 'Con H/M en la columna, la M es Mujer')
        # Corrige en la tabla y guarda
        lines[3].birthdate = date(2017, 2, 2)
        lines[4].write({'tutor_name': 'Ana Mar', 'tutor_phone': '2229990000'})
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
        # El jefe no es capturista: no ve el formulario de carga
        self.assertIn('Solo el capturista', self.url_open('/mi-grupo/importar').text)

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
