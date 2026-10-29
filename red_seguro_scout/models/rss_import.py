# -*- coding: utf-8 -*-
import base64
import logging
from datetime import datetime

import pytz

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .rss_common import SEX_SELECTION, EMAIL_RE, age_on, name_key
from .rss_parsers import parse_excel, parse_pdf

_logger = logging.getLogger(__name__)

MAX_ROWS = 500


class RssImportBatch(models.Model):
    """Lista de asegurados que captura un grupo (Excel, PDF o a mano).

    Flujo: revisión → por pagar (ventana de pago) → comprobante en revisión (transferencia)
    → pagada (se crean los asegurados) | vencida | cancelada.

    NADIE queda asegurado hasta que el pago se confirma, y el pago debe hacerse el mismo día
    de la captura: si no, la carga vence.
    """
    _name = 'rss.import.batch'
    _description = 'Carga de asegurados'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'

    name = fields.Char('Carga', compute='_compute_name', store=True)
    reference = fields.Char('Referencia de pago', compute='_compute_reference', store=True,
                            help='Concepto que el grupo pone en su transferencia.')
    group_id = fields.Many2one('rss.scout.group', string='Grupo', required=True, index=True,
                               ondelete='cascade')
    company_id = fields.Many2one(related='group_id.company_id', store=True, string='Organización')
    policy_id = fields.Many2one(related='group_id.policy_id', string='Póliza')
    currency_id = fields.Many2one(related='company_id.currency_id', string='Moneda')
    member_id = fields.Many2one('rss.group.member', string='Capturó (representante)', ondelete='set null')
    user_id = fields.Many2one('res.users', string='Usuario', default=lambda self: self.env.user)
    source = fields.Selection([('excel', 'Excel'), ('pdf', 'PDF'), ('manual', 'Captura manual')],
                              string='Origen', required=True)
    filename = fields.Char('Archivo')
    attachment_id = fields.Many2one('ir.attachment', string='Archivo original', ondelete='set null')
    line_ids = fields.One2many('rss.import.line', 'batch_id', string='Filas')
    state = fields.Selection([
        ('review', 'En revisión'),
        ('payment', 'Por pagar'),
        ('verifying', 'Comprobante en revisión'),
        ('done', 'Pagada'),
        ('expired', 'Vencida (sin pago)'),
        ('cancel', 'Cancelada'),
    ], string='Estatus', default='review', required=True, tracking=True, index=True)
    line_count = fields.Integer('Núm. de filas', compute='_compute_counts')
    ok_count = fields.Integer('Listas', compute='_compute_counts')
    error_count = fields.Integer('Con error', compute='_compute_counts')
    duplicate_count = fields.Integer('Repetidas', compute='_compute_counts')
    created_count = fields.Integer('Asegurados creados', readonly=True)
    privacy_accepted = fields.Boolean('Aviso de privacidad aceptado', readonly=True)

    # Cobro
    tariff_name = fields.Char('Tarifa aplicada', readonly=True)
    unit_amount = fields.Monetary('Importe por persona', readonly=True)
    quantity = fields.Integer('Personas a pagar', readonly=True)
    amount_total = fields.Monetary('Total del seguro', readonly=True, tracking=True)
    mp_commission = fields.Monetary('Comisión Mercado Pago', readonly=True)
    amount_mp_total = fields.Monetary('Total con Mercado Pago', readonly=True)
    payment_deadline = fields.Datetime('Pagar a más tardar', readonly=True, tracking=True)
    payment_method = fields.Selection([('transfer', 'Transferencia'), ('mercadopago', 'Mercado Pago')],
                                      string='Forma de pago', readonly=True, tracking=True)
    transfer_reference = fields.Char('Referencia / rastreo de la transferencia', tracking=True)
    transfer_date = fields.Date('Fecha de la transferencia')
    proof_attachment_ids = fields.Many2many('ir.attachment', 'rss_import_batch_proof_rel', 'batch_id',
                                            'attachment_id', string='Comprobantes de pago')
    rejection_reason = fields.Char('Motivo del rechazo del pago', tracking=True)
    rss_payment_id = fields.Many2one('rss.payment', string='Pago / recibo', readonly=True, ondelete='set null')
    transaction_ref = fields.Char('Transacción Mercado Pago', readonly=True)
    paid_date = fields.Datetime('Pagada el', readonly=True)

    @api.depends('group_id', 'filename', 'source', 'create_date')
    def _compute_name(self):
        for batch in self:
            label = batch.filename or (_('Captura manual') if batch.source == 'manual' else _('Captura'))
            batch.name = '%s · %s' % (batch.group_id.name or '', label)

    @api.depends('group_id.number')
    def _compute_reference(self):
        for batch in self:
            batch.reference = 'RSS-G%s-%s' % (batch.group_id.number or batch.group_id.id or 0, batch.id or 0)

    @api.depends('line_ids.status', 'line_ids.include')
    def _compute_counts(self):
        for batch in self:
            lines = batch.line_ids
            batch.line_count = len(lines)
            batch.ok_count = len(lines.filtered(lambda l: l.status == 'ok' and l.include))
            batch.error_count = len(lines.filtered(lambda l: l.status == 'error'))
            batch.duplicate_count = len(lines.filtered(lambda l: l.status == 'duplicate'))

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------
    @api.model
    def _detect_source(self, filename, data):
        name = (filename or '').lower()
        if name.endswith('.pdf') or data[:4] == b'%PDF':
            return 'pdf'
        if name.endswith(('.xlsx', '.xlsm')) or data[:2] == b'PK':
            return 'excel'
        raise UserError(_('Sube la lista en Excel (.xlsx) o en PDF.'))

    @api.model
    def create_from_file(self, group, filename, data, member=None, privacy_accepted=False):
        """Lee el archivo y crea la carga con sus filas para revisión."""
        source = self._detect_source(filename, data)
        rows, error = (parse_pdf if source == 'pdf' else parse_excel)(data)
        if error:
            raise UserError(error)
        if len(rows) > MAX_ROWS:
            raise UserError(_('El archivo tiene %(n)s personas; el máximo por carga es %(max)s.',
                              n=len(rows), max=MAX_ROWS))
        attachment = self.env['ir.attachment'].sudo().create({
            'name': filename or 'lista', 'raw': data, 'res_model': self._name,
        })
        batch = self.create({
            'group_id': group.id,
            'member_id': member.id if member else False,
            'source': source,
            'filename': filename,
            'attachment_id': attachment.id,
            'privacy_accepted': privacy_accepted,
            'line_ids': [fields.Command.create(self.env['rss.import.line']._vals_from_row(r, i))
                         for i, r in enumerate(rows, start=1)],
        })
        attachment.res_id = batch.id
        batch.action_validate()
        return batch

    @api.model
    def create_manual(self, group, member=None, privacy_accepted=False, rows=1):
        """Captura a mano: una tabla vacía con las mismas validaciones y el mismo cobro."""
        return self.create({
            'group_id': group.id,
            'member_id': member.id if member else False,
            'source': 'manual',
            'privacy_accepted': privacy_accepted,
            'line_ids': [fields.Command.create({'sequence': i}) for i in range(1, rows + 1)],
        })

    def action_add_row(self):
        for batch in self.filtered(lambda b: b.state == 'review'):
            seq = max(batch.line_ids.mapped('sequence') or [0]) + 1
            self.env['rss.import.line'].create({'batch_id': batch.id, 'sequence': seq})

    # ------------------------------------------------------------------
    # Validación
    # ------------------------------------------------------------------
    def action_validate(self):
        for batch in self.filtered(lambda b: b.state in ('review', 'payment', 'verifying')):
            seen = {}
            for line in batch.line_ids.sorted('sequence'):
                line._validate(seen)
        return True

    def _ready_lines(self):
        self.ensure_one()
        return self.line_ids.filtered(lambda l: l.status == 'ok' and l.include)

    # ------------------------------------------------------------------
    # Cobro
    # ------------------------------------------------------------------
    def _rss_tz(self):
        """Zona horaria de la organización (no la de quien ejecuta la acción)."""
        company = self[:1].company_id or self.env.company
        name = company.partner_id.tz or 'America/Mexico_City'
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone('America/Mexico_City')

    def _deadline_label(self):
        self.ensure_one()
        if not self.payment_deadline:
            return ''
        local = pytz.UTC.localize(self.payment_deadline).astimezone(self._rss_tz())
        return local.strftime('%d/%m/%Y %H:%M')

    def _end_of_today(self):
        """Fin del día (23:59:59) en la hora de la organización, guardado en UTC."""
        tz = self._rss_tz()
        local_now = datetime.now(tz)
        local_end = local_now.replace(hour=23, minute=59, second=59, microsecond=0)
        return local_end.astimezone(pytz.UTC).replace(tzinfo=None)

    def action_request_payment(self):
        """Congela la lista, calcula el importe y abre la ventana de pago.
        A partir de aquí la carga queda bloqueada hasta que se pague."""
        for batch in self:
            if batch.state != 'review':
                raise UserError(_('Esta carga ya no está en revisión.'))
            batch.action_validate()
            lines = batch._ready_lines()
            if not lines:
                raise UserError(_('No hay personas listas para pagar. Corrige los errores marcados.'))
            policy = batch.group_id.sudo().policy_id
            if not policy:
                raise UserError(_('El grupo no tiene póliza asignada; avisa a la administración central.'))
            today = fields.Date.context_today(batch)
            tariff, unit = policy._tariff_for(today)
            if not unit:
                raise UserError(_('No hay tarifa vigente para hoy; avisa a la administración central.'))
            total = batch.currency_id.round(unit * len(lines))
            mp_total, commission = policy._mercado_pago_total(total)
            batch.write({
                'state': 'payment',
                'tariff_name': tariff.name if tariff else _('Cuota anual'),
                'unit_amount': unit,
                'quantity': len(lines),
                'amount_total': total,
                'mp_commission': commission,
                'amount_mp_total': mp_total,
                'payment_deadline': batch._end_of_today(),
                'rejection_reason': False,
            })
            batch.message_post(body=_('%(n)s personas por %(unit)s = %(total)s. Pago pendiente; '
                                      'vence hoy a las 23:59.', n=len(lines),
                                      unit=_money(batch, unit), total=_money(batch, total)))
        return True

    def action_back_to_review(self):
        """Antes de pagar, el capturista puede regresar a corregir la lista."""
        self.filtered(lambda b: b.state == 'payment').write({'state': 'review', 'payment_method': False})

    def action_submit_transfer(self, reference, transfer_date=None, files=()):
        """El grupo informa su transferencia y adjunta el comprobante."""
        self.ensure_one()
        if self.state != 'payment':
            raise UserError(_('Esta carga no está esperando pago.'))
        if fields.Datetime.now() > self.payment_deadline:
            self._expire()
            raise UserError(_('El plazo de pago venció; la carga se canceló. Captúrala de nuevo.'))
        if not files:
            raise UserError(_('Adjunta el comprobante de la transferencia.'))
        if not (reference or '').strip():
            raise UserError(_('Escribe la referencia o clave de rastreo de la transferencia.'))
        attachments = self.env['ir.attachment'].sudo()
        for name, data, mimetype in files:
            attachments |= attachments.create({'name': name, 'raw': data, 'mimetype': mimetype,
                                               'res_model': self._name, 'res_id': self.id})
        self.write({
            'state': 'verifying',
            'payment_method': 'transfer',
            'transfer_reference': reference.strip(),
            'transfer_date': transfer_date or fields.Date.context_today(self),
            'proof_attachment_ids': [fields.Command.link(a.id) for a in attachments],
        })
        self.message_post(body=_('Transferencia informada por %(who)s (ref. %(ref)s). Por validar.',
                                 who=self.member_id.name or self.user_id.name, ref=reference.strip()),
                          attachment_ids=attachments.ids)
        manager_group = self.env.ref('red_seguro_scout.group_rss_manager')
        responsible = (manager_group.user_ids.filtered(lambda u: not u.share and u.active)[:1]
                       or self.env.ref('base.user_admin'))
        self.activity_schedule('mail.mail_activity_data_todo', user_id=responsible.id,
                               summary=_('Validar transferencia %s', self.reference),
                               note=_('%(group)s · %(total)s · %(n)s personas',
                                      group=self.group_id.name, total=_money(self, self.amount_total),
                                      n=self.quantity))
        return True

    def action_validate_transfer(self):
        """Administración central: el dinero sí llegó → se crean los asegurados."""
        for batch in self:
            if batch.state != 'verifying':
                raise UserError(_('Solo se validan cargas con comprobante en revisión.'))
            batch._mark_paid('transfer', batch.transfer_reference, batch.amount_total,
                             attachments=batch.proof_attachment_ids)
            batch.activity_ids.action_feedback(feedback=_('Pago validado.'))
        return True

    def action_reject_transfer(self):
        """Administración central: el pago no se vio reflejado. El grupo tiene solo hoy
        para volver a pagar; si no, la carga vence."""
        for batch in self:
            if batch.state != 'verifying':
                raise UserError(_('Solo se rechazan cargas con comprobante en revisión.'))
            reason = batch.rejection_reason or _('El pago no se vio reflejado en la cuenta.')
            batch.write({'state': 'payment', 'payment_deadline': batch._end_of_today(),
                         'rejection_reason': reason})
            batch.activity_ids.action_feedback(feedback=_('Pago rechazado: %s', reason))
            partners = batch.member_id.partner_id
            batch.message_post(
                body=_('Tu pago de la carga %(ref)s no se vio reflejado (%(reason)s). Tienes solo hoy, hasta '
                       'las 23:59, para pagar; de lo contrario los asegurados no serán tomados en cuenta.',
                       ref=batch.reference, reason=reason),
                partner_ids=partners.ids, message_type='comment', subtype_xmlid='mail.mt_comment')
        return True

    def _mark_paid(self, method, reference, amount, attachments=None, transaction_ref=None):
        """Pago confirmado: se crean los asegurados, se registra el pago con su recibo y se activan."""
        self.ensure_one()
        if self.state == 'done':
            return self.rss_payment_id
        Insured = self.env['rss.insured'].sudo()
        self.action_validate()     # por si alguien más registró a la misma persona mientras tanto
        lines = self._ready_lines()
        skipped = self.quantity - len(lines)
        source = {'pdf': 'pdf', 'excel': 'excel'}.get(self.source, 'portal')
        today = fields.Date.context_today(self)
        if not lines:
            self.write({'state': 'done', 'payment_method': method, 'paid_date': fields.Datetime.now(),
                        'transaction_ref': transaction_ref or False})
            self.message_post(body=_('Pago recibido (%s), pero todas las personas ya estaban registradas '
                                     'por otra carga. Revisa la devolución.', reference))
            return self.env['rss.payment']
        created = Insured.browse()
        for line in lines:
            created |= Insured.with_context(mail_create_nolog=True).create({
                'first_name': line.first_name,
                'second_name': line.second_name or False,
                'last_name_father': line.last_name_father,
                'last_name_mother': line.last_name_mother or False,
                'birthdate': line.birthdate,
                'sex': line.sex,
                'tutor_name': line.tutor_name,
                'tutor_phone': line.tutor_phone,
                'tutor_email': line.tutor_email or False,
                'group_id': self.group_id.id,
                'state': 'draft',
                'date_start': today,
                'capture_source': source,
                'captured_by_id': self.user_id.id,
                'import_batch_id': self.id,
            })
            line.insured_id = created[-1]
        payment = self.env['rss.payment'].sudo().create({
            'group_id': self.group_id.id,
            'year': today.year,
            'date': today,
            'payer_name': self.member_id.name or self.user_id.name,
            'payment_method': method,
            'reference': reference,
            'amount_per_member': self.unit_amount,
            'member_ids': [fields.Command.set(created.ids)],
            'amount': amount,
            'import_batch_id': self.id,
            'mp_commission': self.mp_commission if method == 'mercadopago' else 0.0,
            'attachment_ids': [fields.Command.set(attachments.ids)] if attachments else [],
        })
        payment.action_confirm()          # emite el recibo y activa a los asegurados
        self.write({
            'state': 'done',
            'payment_method': method,
            'created_count': len(created),
            'rss_payment_id': payment.id,
            'paid_date': fields.Datetime.now(),
            'transaction_ref': transaction_ref or False,
        })
        body = _('Pago confirmado (%(method)s). %(n)s personas aseguradas. Recibo %(rec)s.',
                 method=dict(self._fields['payment_method'].selection).get(method), n=len(created),
                 rec=payment.name)
        if skipped:
            body += ' ' + _('%s personas se omitieron porque ya estaban registradas; revisa la devolución.',
                            skipped)
        self.message_post(body=body, partner_ids=self.member_id.partner_id.ids,
                          message_type='comment', subtype_xmlid='mail.mt_comment')
        self.group_id.message_post(body=_('Carga %(ref)s pagada: %(n)s asegurados nuevos (%(who)s).',
                                          ref=self.reference, n=len(created),
                                          who=self.member_id.name or self.user_id.name))
        return payment

    def _mercadopago_paid(self, transaction):
        """Llamado al confirmarse la transacción de Mercado Pago."""
        self.ensure_one()
        if self.state not in ('payment', 'expired'):
            return
        self._mark_paid('mercadopago', transaction.reference, self.amount_total,
                        transaction_ref=transaction.reference)

    def _expire(self):
        for batch in self:
            batch.write({'state': 'expired'})
            batch.message_post(
                body=_('La carga %s venció sin pago: estas personas NO quedaron aseguradas. '
                       'Si aún deben asegurarse, captúralas de nuevo y paga el mismo día.', batch.reference),
                partner_ids=batch.member_id.partner_id.ids, message_type='comment',
                subtype_xmlid='mail.mt_comment')

    @api.model
    def _cron_expire_unpaid(self):
        """Cargas sin pagar al terminar el día: vencen (el pago debe ser el mismo día)."""
        overdue = self.search([('state', '=', 'payment'), ('payment_deadline', '<', fields.Datetime.now())])
        overdue._expire()
        if overdue:
            _logger.info('Red Seguro Scout: %s cargas vencidas sin pago.', len(overdue))
        return True

    def action_cancel(self):
        self.filtered(lambda b: b.state in ('review', 'payment')).write({'state': 'cancel'})


def _money(record, amount):
    symbol = record.currency_id.symbol or '$'
    return '%s %s' % (symbol, '{:,.2f}'.format(amount or 0.0))


class RssImportLine(models.Model):
    _name = 'rss.import.line'
    _description = 'Fila de importación de asegurados'
    _order = 'batch_id, sequence'

    batch_id = fields.Many2one('rss.import.batch', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer('Fila')
    include = fields.Boolean('Incluir', default=True)
    first_name = fields.Char('Nombre')
    second_name = fields.Char('Segundo nombre')
    last_name_father = fields.Char('Apellido paterno')
    last_name_mother = fields.Char('Apellido materno')
    birthdate = fields.Date('Fecha de nacimiento')
    age = fields.Integer('Edad', compute='_compute_age')
    sex = fields.Selection(SEX_SELECTION, string='Sexo')
    tutor_name = fields.Char('Padre o tutor')
    tutor_phone = fields.Char('Teléfono de emergencia')
    tutor_email = fields.Char('Correo')
    raw_sex = fields.Char('Sexo (como venía)')
    raw_birthdate = fields.Char('Fecha (como venía)')
    status = fields.Selection([
        ('ok', 'Lista'),
        ('error', 'Con error'),
        ('duplicate', 'Repetida'),
    ], string='Revisión', default='ok')
    message = fields.Char('Observaciones')
    duplicate_insured_id = fields.Many2one('rss.insured', string='Ya registrado como',
                                           ondelete='set null')
    insured_id = fields.Many2one('rss.insured', string='Asegurado creado', ondelete='set null')

    @api.depends('birthdate')
    def _compute_age(self):
        for line in self:
            line.age = age_on(line.birthdate) if line.birthdate else 0

    @api.model
    def _vals_from_row(self, row, sequence):
        return {
            'sequence': sequence,
            'first_name': row.get('first_name') or False,
            'second_name': row.get('second_name') or False,
            'last_name_father': row.get('last_name_father') or False,
            'last_name_mother': row.get('last_name_mother') or False,
            'birthdate': row.get('birthdate') or False,
            'sex': row.get('sex') or False,
            'tutor_name': row.get('tutor_name') or False,
            'tutor_phone': row.get('tutor_phone') or False,
            'tutor_email': row.get('tutor_email') or False,
            'raw_sex': row.get('raw_sex') or False,
            'raw_birthdate': row.get('raw_birthdate') or False,
        }

    def _is_blank(self):
        self.ensure_one()
        return not any(self[f] for f in ('first_name', 'second_name', 'last_name_father', 'last_name_mother',
                                         'birthdate', 'sex', 'tutor_name', 'tutor_phone', 'tutor_email'))

    def _validate(self, seen):
        """Marca errores y repetidos. ``seen`` detecta repetidos dentro del mismo archivo."""
        self.ensure_one()
        problems = []
        if not self.first_name:
            problems.append(_('falta el nombre'))
        if not self.last_name_father:
            problems.append(_('falta el apellido paterno'))
        if not self.birthdate:
            problems.append(_('fecha de nacimiento no válida%s') %
                            (' ("%s")' % self.raw_birthdate if self.raw_birthdate else ''))
        if not self.sex:
            problems.append(_('sexo no reconocido%s, usa Hombre o Mujer') %
                            (' ("%s")' % self.raw_sex if self.raw_sex else ''))
        if self.birthdate and age_on(self.birthdate) < 18:
            if not self.tutor_name:
                problems.append(_('falta el padre o tutor'))
            if not self.tutor_phone:
                problems.append(_('falta el teléfono de emergencia'))
        if self.tutor_email and not EMAIL_RE.match(self.tutor_email.strip()):
            problems.append(_('correo no válido'))
        vals = {'duplicate_insured_id': False}
        if problems:
            msg = ', '.join(problems)
            vals.update(status='error', message=msg[:1].upper() + msg[1:] + '.')
        else:
            key = name_key(self.first_name, self.second_name, self.last_name_father,
                           self.last_name_mother, self.birthdate)
            dup = self.env['rss.insured']._find_duplicates(
                self.first_name, self.second_name, self.last_name_father, self.last_name_mother,
                self.birthdate, company=self.batch_id.company_id)[:1]
            if dup:
                where = dup.group_id.name if dup.group_id != self.batch_id.group_id else _('tu grupo')
                vals.update(status='duplicate', include=False, duplicate_insured_id=dup.id,
                            message=_('Ya está registrado en %s.', where))
            elif key in seen:
                vals.update(status='duplicate', include=False,
                            message=_('Repetido en este archivo (fila %s).', seen[key]))
            else:
                seen[key] = self.sequence
                vals.update(status='ok', message=False)
                if self.status == 'duplicate':
                    vals['include'] = True
        self.write(vals)
