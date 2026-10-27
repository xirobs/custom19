# -*- coding: utf-8 -*-
import base64
import io
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .rss_common import (
    SECTION_SELECTION, SEX_SELECTION, age_on, month_bounds, month_label, overlap_days,
)

_logger = logging.getLogger(__name__)


class RssReport(models.Model):
    """Relación mensual de asegurados que se envía a la aseguradora.

    El cron la genera automáticamente entre los días 1 y ``policy.report_day``
    (10 por defecto) de cada mes. Cada línea es una "foto" del asegurado en ese
    mes, con los días que estuvo cubierto; con ellas se analizan los
    asegurados por mes, trimestre y año.
    """
    _name = 'rss.report'
    _description = 'Relación mensual de asegurados'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'rss.branding.mixin']
    _order = 'period_date desc, id desc'

    name = fields.Char('Relación', compute='_compute_name', store=True)
    policy_id = fields.Many2one('rss.policy', string='Póliza', required=True,
                                tracking=True, ondelete='restrict')
    company_id = fields.Many2one(related='policy_id.company_id', store=True,
                                 string='Organización')
    period_date = fields.Date('Periodo (mes)', required=True, tracking=True,
                              default=lambda self: fields.Date.context_today(self).replace(day=1),
                              help='Se toma el mes de esta fecha.')
    date_from = fields.Date('Del', compute='_compute_period', store=True)
    date_to = fields.Date('Al', compute='_compute_period', store=True)
    deadline = fields.Date('Fecha límite de envío', compute='_compute_period', store=True)
    group_ids = fields.Many2many('rss.scout.group', string='Grupos',
                                 help='Déjalo vacío para incluir todos los grupos de la póliza.')
    only_paid_groups = fields.Boolean('Solo grupos con pago del año')
    line_ids = fields.One2many('rss.report.line', 'report_id', string='Asegurados del mes')
    insured_count = fields.Integer('Asegurados', compute='_compute_totals', store=True)
    group_count = fields.Integer('Grupos incluidos', compute='_compute_totals', store=True)
    new_count = fields.Integer('Altas del mes', compute='_compute_totals', store=True)
    drop_count = fields.Integer('Bajas del mes', compute='_compute_totals', store=True)
    total_days = fields.Integer('Días-asegurado', compute='_compute_totals', store=True)
    unpaid_group_names = fields.Char('Grupos sin pago del año', compute='_compute_totals',
                                     store=True)
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('generated', 'Generada'),
        ('sent', 'Enviada'),
        ('acknowledged', 'Acuse recibido'),
    ], string='Estatus', default='draft', required=True, tracking=True, copy=False)
    auto_generated = fields.Boolean('Generada por el sistema', readonly=True, copy=False)
    date_generated = fields.Datetime('Generada el', readonly=True, copy=False)
    date_sent = fields.Datetime('Enviada el', readonly=True, copy=False, tracking=True)
    insurer_ack = fields.Char('Acuse / folio de la aseguradora', tracking=True, copy=False)
    is_late = fields.Boolean('Vencida', compute='_compute_is_late')
    xlsx_file = fields.Binary('Excel', attachment=True, readonly=True, copy=False)
    xlsx_filename = fields.Char(readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Cómputos
    # ------------------------------------------------------------------
    @api.depends('period_date', 'policy_id.name')
    def _compute_name(self):
        for rep in self:
            rep.name = _('Relación %(month)s · %(policy)s',
                         month=month_label(rep.period_date), policy=rep.policy_id.name or '')

    @api.depends('period_date', 'policy_id.report_day')
    def _compute_period(self):
        for rep in self:
            if not rep.period_date:
                rep.date_from = rep.date_to = rep.deadline = False
                continue
            rep.date_from, rep.date_to = month_bounds(rep.period_date)
            rep.deadline = rep.date_from.replace(day=rep.policy_id.report_day or 10)

    @api.depends('line_ids.group_id', 'line_ids.is_new', 'line_ids.is_drop',
                 'line_ids.days_covered', 'line_ids.group_paid')
    def _compute_totals(self):
        for rep in self:
            lines = rep.line_ids
            rep.insured_count = len(lines)
            rep.group_count = len(lines.group_id)
            rep.new_count = len(lines.filtered('is_new'))
            rep.drop_count = len(lines.filtered('is_drop'))
            rep.total_days = sum(lines.mapped('days_covered'))
            unpaid = lines.filtered(lambda l: not l.group_paid).group_id
            rep.unpaid_group_names = ', '.join(unpaid.mapped('name')) or False

    def _rss_brand(self):
        """La relación sale con la identidad de la organización; si la relación
        es de un solo grupo, con la de ese grupo."""
        self.ensure_one()
        groups = self.group_ids or self.line_ids.group_id
        group = groups if len(groups) == 1 else self.env['rss.scout.group']
        return self._rss_brand_from(group=group, company=self.company_id)

    def _period_label(self):
        self.ensure_one()
        return month_label(self.date_from)

    def _compute_is_late(self):
        today = fields.Date.context_today(self)
        for rep in self:
            rep.is_late = bool(rep.deadline and rep.deadline < today
                               and rep.state in ('draft', 'generated'))

    @api.onchange('policy_id')
    def _onchange_policy(self):
        self.only_paid_groups = self.policy_id.only_paid_groups

    # ------------------------------------------------------------------
    # Generación
    # ------------------------------------------------------------------
    def _get_groups(self):
        self.ensure_one()
        groups = self.group_ids or self.env['rss.scout.group'].search(
            [('policy_id', '=', self.policy_id.id)])
        if self.only_paid_groups:
            year = self.date_from.year
            groups = groups.filtered(lambda g: year in g._get_paid_years())
        return groups

    def _get_insured(self):
        self.ensure_one()
        return self.env['rss.insured'].search([
            ('group_id', 'in', self._get_groups().ids),
            ('state', 'in', ('active', 'inactive')),
            ('date_start', '!=', False),
            ('date_start', '<=', self.date_to),
            '|', ('date_end', '=', False), ('date_end', '>=', self.date_from),
        ])

    def _prepare_line_vals(self, insured):
        self.ensure_one()
        year = self.date_from.year
        paid_years = {}
        vals_list = []
        for ins in insured:
            if ins.group_id.id not in paid_years:
                paid_years[ins.group_id.id] = year in ins.group_id._get_paid_years()
            vals_list.append({
                'report_id': self.id,
                'insured_id': ins.id,
                'group_id': ins.group_id.id,
                'first_name': ins.first_name,
                'second_name': ins.second_name,
                'last_name_father': ins.last_name_father,
                'last_name_mother': ins.last_name_mother,
                'birthdate': ins.birthdate,
                'age': age_on(ins.birthdate, self.date_from),
                'sex': ins.sex,
                'section': ins.section,
                'curp': ins.curp,
                'tutor_name': ins.tutor_name,
                'tutor_phone': ins.tutor_phone,
                'tutor_email': ins.tutor_email,
                'date_start': ins.date_start,
                'date_end': ins.date_end,
                'days_covered': overlap_days(ins.date_start, ins.date_end,
                                             self.date_from, self.date_to),
                'is_new': self.date_from <= ins.date_start <= self.date_to,
                'is_drop': bool(ins.date_end and self.date_from <= ins.date_end <= self.date_to),
                'group_paid': paid_years[ins.group_id.id],
            })
        return vals_list

    def action_generate(self):
        for rep in self:
            if rep.state in ('sent', 'acknowledged'):
                raise UserError(_('La relación %s ya fue enviada; no se puede regenerar.',
                                  rep.name))
            rep.line_ids.unlink()
            insured = rep._get_insured()
            self.env['rss.report.line'].create(rep._prepare_line_vals(insured))
            rep.write({
                'state': 'generated',
                'date_generated': fields.Datetime.now(),
                'xlsx_file': False,
                'xlsx_filename': False,
            })
            rep.message_post(body=_('Relación generada con %(count)s asegurados de '
                                    '%(groups)s grupos.',
                                    count=rep.insured_count, groups=rep.group_count))
        return True

    def action_reset_draft(self):
        self.write({'state': 'draft', 'date_sent': False})

    def action_mark_ack(self):
        for rep in self:
            if not rep.insurer_ack:
                raise UserError(_('Captura el acuse o folio que te dio la aseguradora.'))
            rep.state = 'acknowledged'

    def action_mark_sent(self):
        self.write({'state': 'sent', 'date_sent': fields.Datetime.now()})

    # ------------------------------------------------------------------
    # Excel
    # ------------------------------------------------------------------
    def _ensure_generated(self):
        for rep in self:
            if rep.state == 'draft' or not rep.line_ids:
                raise UserError(_('Primero genera la relación %s.', rep.name))

    def _build_xlsx(self):
        """Construye el Excel de la relación con xlsxwriter (incluido en Odoo)."""
        self.ensure_one()
        import xlsxwriter

        output = io.BytesIO()
        book = xlsxwriter.Workbook(output, {'in_memory': True})
        brand = self._rss_brand()
        main, light, line_c = brand['primary'], brand['tint'], brand['tint_strong']
        title_f = book.add_format({'bold': True, 'font_size': 15, 'font_color': main})
        sub_f = book.add_format({'font_size': 10, 'font_color': '#4A5568'})
        head_f = book.add_format({'bold': True, 'font_color': brand['on_primary'], 'bg_color': main,
                                  'border': 1, 'border_color': line_c, 'text_wrap': True,
                                  'valign': 'vcenter', 'align': 'center'})
        cell_f = book.add_format({'border': 1, 'border_color': line_c})
        zebra_f = book.add_format({'border': 1, 'border_color': line_c, 'bg_color': light})
        date_f = book.add_format({'border': 1, 'border_color': line_c,
                                  'num_format': 'dd/mm/yyyy', 'align': 'center'})
        date_z = book.add_format({'border': 1, 'border_color': line_c, 'bg_color': light,
                                  'num_format': 'dd/mm/yyyy', 'align': 'center'})
        num_f = book.add_format({'border': 1, 'border_color': line_c, 'align': 'center'})
        num_z = book.add_format({'border': 1, 'border_color': line_c, 'bg_color': light,
                                 'align': 'center'})
        total_f = book.add_format({'bold': True, 'top': 2, 'top_color': brand['secondary'],
                                   'font_color': main})

        sex = dict(SEX_SELECTION)
        section = dict(SECTION_SELECTION)
        policy = self.policy_id

        # Hoja 1: relación -------------------------------------------------
        ws = book.add_worksheet('Relación')
        ws.hide_gridlines(2)
        ws.write('A1', _('Relación de asegurados · %s', month_label(self.date_from)), title_f)
        ws.write('A2', '%s · %s %s · %s' % (
            self.company_id.name, _('Póliza'), policy.policy_number or policy.name,
            policy.coverage_type or ''), sub_f)
        self._xlsx_insert_logo(ws, brand, 'P1')
        ws.write('A3', _('Periodo: %(start)s al %(end)s · Asegurados: %(count)s',
                         start=self.date_from.strftime('%d/%m/%Y'),
                         end=self.date_to.strftime('%d/%m/%Y'),
                         count=self.insured_count), sub_f)
        headers = [
            ('#', 5), (_('Grupo'), 18), (_('Apellido paterno'), 18),
            (_('Apellido materno'), 18), (_('Nombre(s)'), 22), (_('Fecha de nacimiento'), 13),
            (_('Edad'), 7), (_('Sexo'), 11), (_('Rama'), 20), (_('CURP'), 21),
            (_('Tutor'), 28), (_('Teléfono tutor'), 15), (_('Correo tutor'), 28),
            (_('Alta'), 12), (_('Baja'), 12), (_('Días cubiertos'), 10), (_('Movimiento'), 12),
        ]
        hrow = 4
        ws.set_row(hrow, 32)
        for col, (label, width) in enumerate(headers):
            ws.write(hrow, col, label, head_f)
            ws.set_column(col, col, width)
        lines = self.line_ids.sorted(lambda l: (l.group_id.number or 0, l.group_id.name or '',
                                                l.last_name_father or '',
                                                l.last_name_mother or '', l.first_name or ''))
        for idx, line in enumerate(lines, start=1):
            r = hrow + idx
            z = idx % 2 == 0
            cf, df, nf = (zebra_f, date_z, num_z) if z else (cell_f, date_f, num_f)
            move = _('Alta') if line.is_new else (_('Baja') if line.is_drop else '')
            ws.write_number(r, 0, idx, nf)
            ws.write(r, 1, line.group_id.name or '', cf)
            ws.write(r, 2, (line.last_name_father or '').upper(), cf)
            ws.write(r, 3, (line.last_name_mother or '').upper(), cf)
            ws.write(r, 4, ' '.join(filter(None, [line.first_name, line.second_name])).upper(), cf)
            ws.write_datetime(r, 5, fields.Datetime.to_datetime(line.birthdate), df)
            ws.write_number(r, 6, line.age, nf)
            ws.write(r, 7, sex.get(line.sex, ''), cf)
            ws.write(r, 8, section.get(line.section, ''), cf)
            ws.write(r, 9, line.curp or '', cf)
            ws.write(r, 10, line.tutor_name or '', cf)
            ws.write(r, 11, line.tutor_phone or '', cf)
            ws.write(r, 12, line.tutor_email or '', cf)
            ws.write_datetime(r, 13, fields.Datetime.to_datetime(line.date_start), df)
            if line.date_end:
                ws.write_datetime(r, 14, fields.Datetime.to_datetime(line.date_end), df)
            else:
                ws.write(r, 14, '', cf)
            ws.write_number(r, 15, line.days_covered, nf)
            ws.write(r, 16, move, cf)
        last = hrow + len(lines)
        ws.write(last + 2, 1, _('Total asegurados'), total_f)
        ws.write_number(last + 2, 2, len(lines), total_f)
        ws.write(last + 2, 14, _('Días'), total_f)
        ws.write_number(last + 2, 15, self.total_days, total_f)
        ws.freeze_panes(hrow + 1, 0)
        if lines:
            ws.autofilter(hrow, 0, last, len(headers) - 1)
        ws.set_landscape()
        ws.set_paper(9)
        ws.fit_to_pages(1, 0)
        ws.repeat_rows(hrow)

        # Hoja 2: resumen por grupo ---------------------------------------
        ws2 = book.add_worksheet(_('Resumen por grupo'))
        ws2.hide_gridlines(2)
        ws2.write('A1', _('Resumen por grupo · %s', month_label(self.date_from)), title_f)
        sum_headers = [(_('Grupo'), 26), (_('Agrupación'), 22), (_('Asegurados'), 12),
                       (_('Hombres'), 10), (_('Mujeres'), 10), (_('Altas'), 9),
                       (_('Bajas'), 9), (_('Días-asegurado'), 14), (_('Pago del año'), 13)]
        for col, (label, width) in enumerate(sum_headers):
            ws2.write(2, col, label, head_f)
            ws2.set_column(col, col, width)
        row = 3
        for group in lines.group_id.sorted(lambda g: (g.number or 0, g.name or '')):
            gl = lines.filtered(lambda l, g=group: l.group_id == g)
            values = [group.name, group.cluster_id.name or '', len(gl),
                      len(gl.filtered(lambda l: l.sex == 'male')),
                      len(gl.filtered(lambda l: l.sex == 'female')),
                      len(gl.filtered('is_new')), len(gl.filtered('is_drop')),
                      sum(gl.mapped('days_covered')),
                      _('Sí') if gl[:1].group_paid else _('No')]
            for col, value in enumerate(values):
                fmt = num_f if isinstance(value, int) else cell_f
                ws2.write(row, col, value, fmt)
            row += 1
        ws2.write(row + 1, 0, _('Total'), total_f)
        ws2.write_number(row + 1, 2, len(lines), total_f)
        ws2.write_number(row + 1, 7, self.total_days, total_f)

        book.close()
        return output.getvalue()

    @staticmethod
    def _xlsx_insert_logo(ws, brand, cell):
        """Logo del grupo/organización (o emblema Red Seguro Scout) en el Excel."""
        from PIL import Image
        raw = base64.b64decode(brand['logo'] or brand['emblem'])
        try:
            img = Image.open(io.BytesIO(raw))
            img.thumbnail((180, 58))
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            ws.insert_image(cell, 'logo.png', {'image_data': buf, 'object_position': 3})
        except Exception:  # noqa: BLE001 - un logo dañado no debe impedir el Excel
            _logger.warning('Red Seguro Scout: no se pudo insertar el logo en el Excel')

    def _get_xlsx_filename(self):
        self.ensure_one()
        return 'Relacion_asegurados_%s_%02d.xlsx' % (self.date_from.year, self.date_from.month)

    def _generate_xlsx(self):
        for rep in self:
            rep._ensure_generated()
            rep.write({
                'xlsx_file': base64.b64encode(rep._build_xlsx()),
                'xlsx_filename': rep._get_xlsx_filename(),
            })

    def action_export_xlsx(self):
        self.ensure_one()
        self._generate_xlsx()
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/rss.report/%s/xlsx_file/%s?download=true'
                   % (self.id, self.xlsx_filename),
            'target': 'self',
        }

    def action_print_pdf(self):
        self._ensure_generated()
        return self.env.ref('red_seguro_scout.action_report_relacion_mensual').report_action(self)

    # ------------------------------------------------------------------
    # Envío a la aseguradora
    # ------------------------------------------------------------------
    def _create_send_attachments(self):
        """Crea los adjuntos (PDF + Excel) que viajan en el correo."""
        self.ensure_one()
        self._ensure_generated()
        self._generate_xlsx()
        Attachment = self.env['ir.attachment']
        pdf, _fmt = self.env['ir.actions.report']._render_qweb_pdf(
            'red_seguro_scout.action_report_relacion_mensual', self.ids)
        base = 'Relacion_asegurados_%s_%02d' % (self.date_from.year, self.date_from.month)
        pdf_att = Attachment.create({
            'name': base + '.pdf',
            'type': 'binary',
            'raw': pdf,
            'mimetype': 'application/pdf',
            'res_model': self._name,
            'res_id': self.id,
        })
        xlsx_att = Attachment.create({
            'name': base + '.xlsx',
            'type': 'binary',
            'raw': base64.b64decode(self.xlsx_file),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'res_model': self._name,
            'res_id': self.id,
        })
        return pdf_att | xlsx_att

    def _get_recipient_partner(self):
        self.ensure_one()
        policy = self.policy_id
        if policy.insurer_id and policy.insurer_id.email == policy.insurer_email:
            return policy.insurer_id
        if policy.insurer_email:
            return self.env['res.partner'].find_or_create(policy.insurer_email)
        return self.env['res.partner']

    def action_send(self):
        """Abre el redactor de correo con la plantilla, el Excel y el PDF."""
        self.ensure_one()
        if not self.policy_id.insurer_email:
            raise UserError(_('Captura el correo de la aseguradora en la póliza %s.',
                              self.policy_id.name))
        attachments = self._create_send_attachments()
        template = self.env.ref('red_seguro_scout.mail_template_relacion_mensual',
                                raise_if_not_found=False)
        subject = body = False
        if template:
            subject = template._render_field('subject', self.ids)[self.id]
            body = template._render_field('body_html', self.ids)[self.id]
        partner = self._get_recipient_partner()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mail.compose.message',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_model': self._name,
                'default_res_ids': self.ids,
                'default_composition_mode': 'comment',
                'default_subject': subject,
                'default_body': body,
                'default_partner_ids': partner.ids,
                'default_attachment_ids': attachments.ids,
                'mark_rss_report_sent': True,
            },
        }

    def _send_to_insurer_auto(self):
        """Envío sin intervención (cron) usando la plantilla."""
        template = self.env.ref('red_seguro_scout.mail_template_relacion_mensual')
        for rep in self:
            if not rep.policy_id.insurer_email:
                rep.message_post(body=_('No se envió automáticamente: la póliza no tiene '
                                        'correo de la aseguradora.'))
                continue
            attachments = rep._create_send_attachments()
            template.send_mail(rep.id, email_values={
                'attachment_ids': [(6, 0, attachments.ids)],
            })
            rep.message_post(body=_('Relación enviada automáticamente a %s.',
                                    rep.policy_id.insurer_email),
                             attachment_ids=attachments.ids)
            rep.action_mark_sent()

    def message_post(self, **kwargs):
        if self.env.context.get('mark_rss_report_sent'):
            self.filtered(lambda r: r.state in ('draft', 'generated')).action_mark_sent()
        return super().message_post(**kwargs)

    # ------------------------------------------------------------------
    # Cron: días 1 al 10
    # ------------------------------------------------------------------
    @api.model
    def _cron_generate_monthly(self):
        """Corre a diario; del día 1 al día límite genera la relación del mes
        si aún no existe, y la envía si la póliza lo indica."""
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        policies = self.env['rss.policy'].search([('auto_generate', '=', True)])
        for policy in policies:
            if today.day > policy.report_day:
                continue
            if self.search_count([('policy_id', '=', policy.id),
                                  ('period_date', '>=', month_start),
                                  ('period_date', '<=', month_bounds(month_start)[1])]):
                continue
            report = self.create({
                'policy_id': policy.id,
                'period_date': month_start,
                'only_paid_groups': policy.only_paid_groups,
                'auto_generated': True,
            })
            report.action_generate()
            _logger.info('Red Seguro Scout: relación %s generada (%s asegurados).',
                         report.name, report.insured_count)
            if policy.auto_send:
                try:
                    report._send_to_insurer_auto()
                except Exception as exc:  # noqa: BLE001
                    _logger.exception('Red Seguro Scout: error al enviar %s', report.name)
                    report.message_post(body=_('Error en el envío automático: %s', exc))
            else:
                report.activity_schedule(
                    'mail.mail_activity_data_todo',
                    date_deadline=report.deadline,
                    summary=_('Enviar relación mensual a la aseguradora'),
                    user_id=policy.create_uid.id or self.env.user.id,
                )
        return True


class RssReportLine(models.Model):
    _name = 'rss.report.line'
    _description = 'Asegurado en relación mensual'
    _order = 'group_id, last_name_father, last_name_mother, first_name'

    report_id = fields.Many2one('rss.report', required=True, ondelete='cascade', index=True)
    period_date = fields.Date(related='report_id.date_from', store=True, string='Mes')
    policy_id = fields.Many2one(related='report_id.policy_id', store=True, string='Póliza')
    company_id = fields.Many2one(related='report_id.company_id', store=True)
    report_state = fields.Selection(related='report_id.state', string='Estatus relación')
    insured_id = fields.Many2one('rss.insured', string='Asegurado', required=True,
                                 ondelete='restrict', index=True)
    group_id = fields.Many2one('rss.scout.group', string='Grupo', required=True, index=True)
    cluster_id = fields.Many2one(related='group_id.cluster_id', store=True,
                                 string='Agrupación')
    first_name = fields.Char('Primer nombre')
    second_name = fields.Char('Segundo nombre')
    last_name_father = fields.Char('Apellido paterno')
    last_name_mother = fields.Char('Apellido materno')
    name = fields.Char('Nombre', related='insured_id.name')
    birthdate = fields.Date('Fecha de nacimiento')
    age = fields.Integer('Edad', aggregator='avg')
    sex = fields.Selection(SEX_SELECTION, string='Sexo')
    section = fields.Selection(SECTION_SELECTION, string='Rama')
    curp = fields.Char('CURP')
    tutor_name = fields.Char('Tutor')
    tutor_phone = fields.Char('Teléfono tutor')
    tutor_email = fields.Char('Correo tutor')
    date_start = fields.Date('Alta')
    date_end = fields.Date('Baja')
    days_covered = fields.Integer('Días cubiertos', aggregator='sum')
    is_new = fields.Boolean('Alta en el mes')
    is_drop = fields.Boolean('Baja en el mes')
    group_paid = fields.Boolean('Grupo con pago del año')
