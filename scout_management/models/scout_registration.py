# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .scout_common import BLOOD_TYPES, MEDICAL_SERVICES

# Campos que se copian de la ficha al expediente del miembro al validar.
MEMBER_SYNC_FIELDS = {
    'first_name': 'scout_first_name', 'last_name': 'scout_last_name', 'last_name2': 'scout_last_name2',
    'birthdate': 'scout_birthdate', 'street': 'street', 'section_id': 'scout_section_id', 'gender': 'scout_gender',
    'guardian_relationship': 'scout_guardian_relationship', 'guardian_phone_alt': 'scout_guardian_phone_alt',
    'medical_service': 'scout_medical_service', 'medical_policy': 'scout_medical_policy',
    'blood_type': 'scout_blood_type', 'medical_conditions': 'scout_medical_conditions',
}


class ScoutRegistration(models.Model):
    _name = 'scout.registration'
    _description = 'Ficha única de registro, consentimiento y responsiva'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'portal.mixin', 'scout.approval.mixin']
    _order = 'cycle_id desc, name desc'

    name = fields.Char(string='Folio', readonly=True, copy=False, default=lambda self: _('Nuevo'))
    cycle_id = fields.Many2one('scout.cycle', string='Ciclo scout', required=True, tracking=True,
                               default=lambda self: self.env['scout.cycle']._get_current())
    state = fields.Selection([
        ('draft', 'Borrador'), ('submitted', 'Recibida'), ('reviewed', 'Revisada (por validar)'),
        ('validated', 'Validada'), ('cancel', 'Cancelada'),
    ], string='Estado', default='draft', required=True, tracking=True, copy=False)
    member_id = fields.Many2one('res.partner', string='Miembro', tracking=True, index=True,
                                domain="[('is_scout', '=', True)]",
                                help='Se crea automáticamente al validar si es un aspirante nuevo.')

    # 1. Datos de registro
    first_name = fields.Char(string='Nombres', required=True)
    last_name = fields.Char(string='Apellido paterno')
    last_name2 = fields.Char(string='Apellido materno')
    birthdate = fields.Date(string='Fecha de nacimiento')
    age = fields.Integer(string='Edad', compute='_compute_age')
    gender = fields.Selection([('m', 'Masculino'), ('f', 'Femenino')], string='Sexo')
    section_id = fields.Many2one('scout.section', string='Sección')
    leadership_position_id = fields.Many2one('scout.position', string='Sección / Jefatura (a cargo)',
                                             domain="[('applies_to', 'in', ('leader', 'both'))]",
                                             help='Solo dirigentes: cargo que atiende (jefe, subjefe, secretaría, tesorería…). '
                                                  'El catálogo lo amplía la jefatura de grupo.')
    leading_section_ids = fields.Many2many('scout.section', 'scout_registration_leading_section_rel',
                                           'registration_id', 'section_id', string='Secciones a cargo')
    street = fields.Char(string='Domicilio')

    # 2. Padre, madre o tutor
    guardian_id = fields.Many2one('res.partner', string='Contacto del tutor',
                                  help='Se crea o vincula al validar para darle acceso al portal.')
    guardian_name = fields.Char(string='Nombre completo del tutor')
    guardian_relationship = fields.Char(string='Parentesco')
    guardian_phone = fields.Char(string='Teléfono principal (celular)')
    guardian_phone_alt = fields.Char(string='Teléfono alterno')
    guardian_email = fields.Char(string='Correo electrónico')
    guardian_street = fields.Char(string='Domicilio (si es distinto)')

    # 3. Información médica (restringida)
    medical_service = fields.Selection(MEDICAL_SERVICES, string='Servicio médico',
                                       groups='scout_management.group_scout_leader')
    medical_policy = fields.Char(string='No. de afiliación o póliza',
                                 groups='scout_management.group_scout_leader')
    blood_type = fields.Selection(BLOOD_TYPES, string='Tipo de sangre',
                                  groups='scout_management.group_scout_leader')
    medical_conditions = fields.Text(string='Condiciones médicas relevantes',
                                     groups='scout_management.group_scout_leader')

    # 4. Pago de registro
    payment_method = fields.Selection([('online', 'En línea (Mercado Pago)'), ('cash', 'Efectivo'),
                                       ('transfer', 'Transferencia')], string='Forma de pago', tracking=True,
                                      help='Lo que eligió la familia al llenar la ficha.')
    declared_paid = fields.Boolean(string='¿Pagó su registro?')
    payment_reference = fields.Char(string='No. de transferencia')
    payment_date = fields.Date(string='Fecha de pago')
    payment_amount = fields.Monetary(string='Monto pagado', currency_field='currency_id')
    payment_proof = fields.Binary(string='Comprobante', attachment=True)
    payment_proof_name = fields.Char()
    registration_fee = fields.Monetary(string='Cuota de registro', currency_field='currency_id',
                                       compute='_compute_registration_fee', store=True, readonly=False)
    invoice_id = fields.Many2one('account.move', string='Cargo de registro (CxC)', readonly=True, copy=False)
    reviewed_by_id = fields.Many2one('res.users', string='Revisó', readonly=True, copy=False)
    validated_by_id = fields.Many2one('res.users', string='Validó', readonly=True, copy=False)
    payment_state = fields.Selection([
        ('not_paid', 'Sin pagar'), ('partial', 'Pago parcial'), ('paid', 'Pagado'), ('exempt', 'Exento'),
    ], compute='_compute_payment_state', string='Estado de pago', store=True)

    # 5. Consentimientos
    consent_participation = fields.Boolean(string='Autoriza participación en actividades')
    consent_first_aid = fields.Boolean(string='Autoriza primeros auxilios y traslado médico')
    consent_image = fields.Boolean(string='Autoriza uso de imagen')
    consent_data = fields.Boolean(string='Autoriza tratamiento de datos personales')
    consent_risk = fields.Boolean(string='Reconoce riesgos y libera de responsabilidad')
    consent_update = fields.Boolean(string='Se compromete a mantener datos actualizados')
    consent_complete = fields.Boolean(compute='_compute_consent_complete', string='Consentimientos completos')

    # Identificación oficial del tutor (la firma debe ser igual a la de su INE)
    guardian_id_front = fields.Binary(string='INE del tutor (frente)', attachment=True, copy=False)
    guardian_id_front_name = fields.Char(copy=False)
    guardian_id_back = fields.Binary(string='INE del tutor (reverso)', attachment=True, copy=False)
    guardian_id_back_name = fields.Char(copy=False)
    kind = fields.Selection([('new', 'Nuevo ingreso'), ('renewal', 'Renovación'), ('return', 'Reingreso')],
                            string='Tipo de registro', default='new', tracking=True)

    # Firmas
    guardian_signature = fields.Binary(string='Firma del padre, madre o tutor', copy=False, attachment=True)
    guardian_signed_by = fields.Char(string='Nombre de quien firma (tutor)', copy=False)
    member_signature = fields.Binary(string='Firma del aspirante', copy=False, attachment=True)
    member_signed_by = fields.Char(string='Nombre del aspirante (firma)', copy=False)
    leader_signature = fields.Binary(string='Firma del dirigente que recibe', copy=False, attachment=True)
    leader_user_id = fields.Many2one('res.users', string='Dirigente que recibe', copy=False)
    signed_date = fields.Date(string='Fecha de firma', copy=False)
    source = fields.Selection([('backend', 'Captura interna'), ('portal', 'Portal'), ('import', 'Carga masiva')],
                              default='backend', readonly=True)
    manual_payment_state = fields.Selection([
        ('not_paid', 'Sin pagar'), ('partial', 'Pago parcial'), ('paid', 'Pagado'), ('exempt', 'Exento')],
        string='Pago (histórico)', help='Para fichas anteriores al sistema o cargadas en masa, sin cargo contable.')

    company_id = fields.Many2one('res.company', string='Grupo', required=True,
                                 default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')

    _member_cycle_uniq = models.Constraint(
        'UNIQUE (member_id, cycle_id)', 'El miembro ya tiene una ficha en este ciclo.')

    # ------------------------------------------------------------------------
    @api.depends('birthdate')
    def _compute_age(self):
        from dateutil.relativedelta import relativedelta
        today = fields.Date.context_today(self)
        for reg in self:
            reg.age = relativedelta(today, reg.birthdate).years if reg.birthdate else 0

    @api.depends('cycle_id')
    def _compute_registration_fee(self):
        for reg in self:
            reg.registration_fee = reg.cycle_id.registration_fee

    @api.depends('invoice_id.payment_state', 'invoice_id.state', 'registration_fee', 'manual_payment_state')
    def _compute_payment_state(self):
        for reg in self:
            inv = reg.invoice_id
            if not inv and reg.manual_payment_state:
                reg.payment_state = reg.manual_payment_state
            elif not reg.registration_fee:
                reg.payment_state = 'exempt'
            elif inv.state == 'posted' and inv.payment_state in ('paid', 'in_payment', 'reversed'):
                reg.payment_state = 'paid'
            elif inv.state == 'posted' and inv.payment_state == 'partial':
                reg.payment_state = 'partial'
            else:
                reg.payment_state = 'not_paid'

    @api.depends('consent_participation', 'consent_first_aid', 'consent_image',
                 'consent_data', 'consent_risk', 'consent_update')
    def _compute_consent_complete(self):
        for reg in self:
            reg.consent_complete = all([reg.consent_participation, reg.consent_first_aid, reg.consent_image,
                                        reg.consent_data, reg.consent_risk, reg.consent_update])

    @api.onchange('member_id')
    def _onchange_member_id(self):
        member = self.member_id.sudo()
        if not member:
            return
        for reg_field, member_field in MEMBER_SYNC_FIELDS.items():
            self[reg_field] = member[member_field]
        if member.scout_guardian_id:
            self.guardian_id = member.scout_guardian_id
            self.guardian_name = member.scout_guardian_id.name
            self.guardian_phone = member.scout_guardian_id.phone
            self.guardian_email = member.scout_guardian_id.email

    @api.onchange('birthdate', 'gender')
    def _onchange_suggest_section(self):
        """Propone la sección que le corresponde por edad y sexo (se puede cambiar)."""
        if self.birthdate and not self.leadership_position_id:
            section = self.env['scout.section']._scout_suggest(self.birthdate, self.gender)
            if section:
                self.section_id = section

    @api.onchange('guardian_id')
    def _onchange_guardian_id(self):
        if self.guardian_id:
            self.guardian_name = self.guardian_id.name
            self.guardian_phone = self.guardian_id.phone or self.guardian_phone
            self.guardian_email = self.guardian_id.email or self.guardian_email

    @api.constrains('payment_amount')
    def _check_amount(self):
        if any(reg.payment_amount < 0 for reg in self):
            raise ValidationError(_('El monto pagado no puede ser negativo.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['ir.sequence'].next_by_code('scout.registration') or _('Nuevo')
        return super().create(vals_list)

    def _compute_access_url(self):
        super()._compute_access_url()
        for reg in self:
            reg.access_url = f'/my/scouts/registration/{reg.id}'

    def _get_report_base_filename(self):
        self.ensure_one()
        return f'Ficha {self.name} - {self.first_name} {self.last_name or ""}'.strip()

    # ------------------------------------------------------------------------
    def action_submit(self):
        self.filtered(lambda r: r.state == 'draft').write({'state': 'submitted'})

    def action_review(self):
        """Secretaría revisa la ficha y la turna al jefe de grupo para su validación."""
        for reg in self.filtered(lambda r: r.state in ('draft', 'submitted')):
            reg._check_complete()
            reg.write({'state': 'reviewed', 'reviewed_by_id': self.env.user.id})
            reg._scout_request_approval(_('Validar ficha %(folio)s de %(name)s', folio=reg.name,
                                          name=reg.first_name))

    # ------------------------------------------------------------------ pago (en línea o efectivo)
    def _scout_prepare_for_payment(self):
        """Deja listos tutor, miembro (en prueba) y cargo de la cuota para poder pagar en línea o en efectivo."""
        for reg in self.sudo():
            if not reg.guardian_id:
                reg.guardian_id = reg._get_or_create_guardian()
            if not reg.member_id:
                vals = reg._prepare_member_vals()
                vals.update(scout_state='trial', scout_guardian_id=reg.guardian_id.id or False)
                reg.member_id = self.env['res.partner'].sudo().with_context(scout_change_approved=True).create(vals)
            elif reg.guardian_id and not reg.member_id.scout_guardian_id:
                reg.member_id.with_context(scout_change_approved=True).scout_guardian_id = reg.guardian_id
            reg._create_registration_invoice()
        return True

    def _scout_set_payment_method(self, method):
        """La familia elige cómo paga; si es efectivo se avisa a tesorería para cobrarlo."""
        treasurers = self.env.ref('scout_management.group_scout_treasurer').sudo().all_user_ids.filtered(
            lambda u: u.active and not u.share and u.id != 1)
        todo = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        for reg in self.sudo():
            reg.payment_method = method
            if method == 'cash' and reg.invoice_id and reg.payment_state not in ('paid', 'exempt'):
                note = _('%(name)s pagará su registro en efectivo (%(amount)s). Al recibirlo usa "Registrar pago en '
                         'efectivo" en la ficha %(folio)s.', name=reg.member_id.name or reg.first_name,
                         amount='$%.2f' % reg.invoice_id.amount_residual, folio=reg.name)
                for user in treasurers[:3]:
                    reg.activity_schedule(activity_type_id=todo.id if todo else False,
                                          summary=_('Cobrar registro en efectivo'), note=note, user_id=user.id)
                reg.message_post(body=note)

    def action_register_cash_payment(self):
        """Tesorería recibió el efectivo: registra el pago del cargo en la caja."""
        if not self.env.user.has_group('scout_management.group_scout_treasurer'):
            raise UserError(_('El pago en efectivo lo registra tesorería.'))
        for reg in self:
            reg._scout_prepare_for_payment()
            invoice = reg.invoice_id
            if not invoice or invoice.payment_state in ('paid', 'in_payment'):
                continue
            cash = self.env['account.journal'].search([('type', '=', 'cash'),
                                                       ('company_id', '=', reg.company_id.id)], limit=1)
            if not cash:
                cash = self.env['account.journal'].sudo().create({
                    'name': _('Caja (efectivo)'), 'type': 'cash', 'code': 'CAJA',
                    'company_id': reg.company_id.id})
            self.env['account.payment.register'].with_context(
                active_model='account.move', active_ids=invoice.ids).create({
                    'journal_id': cash.id, 'payment_date': fields.Date.context_today(reg),
                    'amount': invoice.amount_residual}).action_create_payments()
            reg.payment_method = 'cash'
            reg.activity_ids.filtered(lambda a: a.summary == _('Cobrar registro en efectivo')).action_feedback(
                feedback=_('Pagado en efectivo'))
            reg.message_post(body=_('Pago en efectivo registrado por %s.', self.env.user.name))
        return True

    def _scout_payment_path(self):
        """Página pública para elegir pago en línea o en efectivo (liga para la familia)."""
        self.ensure_one()
        self.sudo()._portal_ensure_token()
        return f'/scout/ficha/pago/{self.id}?access_token={self.sudo().access_token}'

    def _scout_payment_url(self):
        self.ensure_one()
        return self.get_base_url().rstrip('/') + self._scout_payment_path()

    def action_cancel(self):
        self.write({'state': 'cancel'})
        self._scout_close_approval_activities(_('Cancelada'))

    def action_draft(self):
        self.write({'state': 'draft'})

    def _check_complete(self):
        self.ensure_one()
        if not self.consent_complete:
            raise UserError(_('La ficha %s no tiene marcados todos los consentimientos obligatorios.', self.name))
        if not self.guardian_signature and not self.guardian_signed_by:
            raise UserError(_('Falta la firma del padre, madre o tutor en la ficha %s.', self.name))
        if self.source == 'portal' and not self.guardian_id_front:
            raise UserError(_('Falta la INE del tutor en la ficha %s.', self.name))
        if not self.section_id:
            raise UserError(_('Indica la sección en la ficha %s.', self.name))

    def _get_or_create_guardian(self):
        self.ensure_one()
        if self.guardian_id:
            return self.guardian_id
        if self.member_id.scout_guardian_id:
            return self.member_id.scout_guardian_id
        if not self.guardian_name:
            return self.env['res.partner']
        # Evita duplicar tutores: busca por correo, luego por teléfono, luego por nombre exacto.
        Partner = self.env['res.partner'].sudo()
        partner = Partner
        if self.guardian_email:
            partner = Partner.search([('email', '=ilike', self.guardian_email)], limit=1)
        if not partner and self.guardian_phone:
            partner = Partner.search([('phone', '=', self.guardian_phone)], limit=1)
        if not partner:
            partner = Partner.search([('name', '=ilike', self.guardian_name)], limit=1)
        if not partner:
            partner = partner.create({
                'name': self.guardian_name,
                'phone': self.guardian_phone,
                'email': self.guardian_email,
                'street': self.guardian_street or self.street,
            })
        return partner

    def _prepare_member_vals(self):
        self.ensure_one()
        reg = self.sudo()
        vals = {'is_scout': True}
        for reg_field, member_field in MEMBER_SYNC_FIELDS.items():
            value = reg[reg_field]
            if not value:
                continue
            vals[member_field] = value.id if reg._fields[reg_field].type == 'many2one' else value
        if reg.section_id.is_leadership or reg.leadership_position_id:
            vals['scout_type'] = 'leader'
        if reg.leadership_position_id:
            vals['scout_position_id'] = reg.leadership_position_id.id
        if reg.leading_section_ids:
            vals['scout_leading_section_ids'] = [fields.Command.set(reg.leading_section_ids.ids)]
        return vals

    def action_validate(self):
        """Validación final (jefe de grupo): da de alta / actualiza al miembro y genera el cargo de registro."""
        self._scout_check_manager(_('validar fichas de registro'))
        for reg in self:
            if reg.state not in ('draft', 'submitted', 'reviewed'):
                continue
            reg._check_complete()
            guardian = reg._get_or_create_guardian()
            member_vals = reg._prepare_member_vals()
            if guardian:
                member_vals['scout_guardian_id'] = guardian.id
            Partner = self.env['res.partner'].sudo().with_context(scout_change_approved=True)
            if reg.member_id:
                member = reg.member_id
                if member.scout_state == 'inactive':
                    # Reingreso: la validación del jefe de grupo lo reactiva (puede volver a otra sección)
                    member_vals.update({'scout_state': 'active', 'scout_leave_date': False})
                elif member.scout_section_id and member.scout_section_id != reg.section_id:
                    member_vals.pop('scout_section_id', None)  # el cambio de sección va por solicitud aparte
                Partner.browse(member.id).write(member_vals)
            else:
                member_vals.setdefault('scout_state', 'trial')
                member = Partner.create(member_vals)
            reg.write({
                'member_id': member.id,
                'guardian_id': guardian.id if guardian else False,
                'state': 'validated',
                'validated_by_id': self.env.user.id,
                'leader_user_id': reg.leader_user_id.id or self.env.user.id,
                'signed_date': reg.signed_date or fields.Date.context_today(reg),
            })
            reg._create_registration_invoice()
            reg._scout_close_approval_activities(_('Validada'))
            member.sudo().message_post(body=_('Ficha de registro %(folio)s validada para el ciclo %(cycle)s.',
                                              folio=reg.name, cycle=reg.cycle_id.name))
        return True

    @api.model
    def _scout_sale_journal_message(self, company):
        return _('La empresa "%s" no tiene la contabilidad configurada (no hay diario de ventas), así que no se '
                 'puede generar el cargo. Cambia a esa empresa (arriba a la derecha) y en Facturación o Contabilidad → '
                 'Configuración → Ajustes elige la localización fiscal (México) y guarda; eso crea los diarios de '
                 'ventas, compras, banco y efectivo.', company.name)

    @api.model
    def _scout_check_sale_journal(self, company):
        if not self.env['account.journal'].sudo().search_count(
                [('type', '=', 'sale'), ('company_id', '=', company.id)], limit=1):
            raise UserError(self._scout_sale_journal_message(company))

    def _create_registration_invoice(self):
        """Cargo (factura de cliente) por la cuota de registro, a nombre del tutor y ligada al miembro."""
        product = self.env.ref('scout_management.product_registration', raise_if_not_found=False)
        treasurers = self.env.ref('scout_management.group_scout_treasurer').sudo().all_user_ids.filtered(
            lambda u: not u.share and u.id != 1)
        for reg in self:
            if reg.invoice_id or not reg.registration_fee or reg.manual_payment_state:
                continue
            try:
                reg._scout_check_sale_journal(reg.company_id)
            except UserError as e:
                # La ficha se guarda igual; tesorería recibe el aviso para configurar la contabilidad.
                reg.sudo().message_post(body=str(e.args[0]))
                todo = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
                for user in treasurers[:3]:
                    reg.sudo().activity_schedule(activity_type_id=todo.id if todo else False, user_id=user.id,
                                                 summary=_('Configurar contabilidad para cobrar registros'),
                                                 note=str(e.args[0]))
                continue
            payer = reg.guardian_id or reg.member_id
            invoice = self.env['account.move'].sudo().with_company(reg.company_id).create({
                'move_type': 'out_invoice',
                'partner_id': payer.id,
                'scout_member_id': reg.member_id.id,
                'invoice_origin': reg.name,
                'invoice_date': fields.Date.context_today(reg),
                'invoice_line_ids': [fields.Command.create({
                    'product_id': product.id if product else False,
                    'name': _('Registro ciclo %(cycle)s - %(name)s', cycle=reg.cycle_id.name,
                              name=reg.member_id.name),
                    'quantity': 1,
                    'price_unit': reg.registration_fee,
                    'tax_ids': [fields.Command.clear()],
                })],
            })
            invoice.action_post()
            reg.invoice_id = invoice
            if reg.declared_paid:
                note = _('El tutor declaró el pago: transferencia %(ref)s del %(date)s por %(amount)s. '
                         'Verifica en el banco y registra el pago en el cargo.',
                         ref=reg.payment_reference or '-', date=reg.payment_date or '-', amount=reg.payment_amount)
                todo = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
                for user in treasurers[:2]:
                    invoice.activity_schedule(activity_type_id=todo.id if todo else False,
                                              summary=_('Conciliar pago de registro'), note=note, user_id=user.id)
                invoice.message_post(body=note)


class ReportScoutRegistration(models.AbstractModel):
    _name = 'report.scout_management.report_registration_document'
    _description = 'Reporte: ficha de registro'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['scout.registration'].browse(docids)
        sections = self.env['scout.section'].search([], order='is_leadership, sequence')
        return {
            'doc_ids': docids,
            'doc_model': 'scout.registration',
            'docs': docs,
            'sections': sections,
            'can_see_medical': self.env.user.has_group('scout_management.group_scout_leader'),
        }
