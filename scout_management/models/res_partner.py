# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain

from .scout_common import BLOOD_TYPES, MEDICAL_SERVICES, PATROL_ROLES, SHIRT_SIZES

# Campos que solo el jefe de grupo cambia directamente; los demás lo solicitan (baja / cambio de sección).
PROTECTED_FIELDS = ('scout_section_id', 'scout_state')
MEDICAL_GROUP = 'scout_management.group_scout_leader'


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # --- Identidad scout ------------------------------------------------
    is_scout = fields.Boolean(string='Miembro scout', index=True,
                              help='Marca el contacto como miembro del grupo (beneficiario o dirigente).')
    scout_type = fields.Selection([('youth', 'Miembro de sección'), ('leader', 'Dirigente')],
                                  string='Tipo', default='youth', tracking=True)
    scout_first_name = fields.Char(string='Nombre(s)')
    scout_last_name = fields.Char(string='Apellido paterno')
    scout_last_name2 = fields.Char(string='Apellido materno')
    scout_name = fields.Char(string='Nombre scout', help='Nombre de tótem o nombre en la sección (ej. OSI, CORA).')
    scout_birthdate = fields.Date(string='Fecha de nacimiento', tracking=True)
    scout_age = fields.Integer(string='Edad', compute='_compute_scout_age')
    scout_gender = fields.Selection([('m', 'Masculino'), ('f', 'Femenino')], string='Sexo')
    scout_shirt_size = fields.Selection(SHIRT_SIZES, string='Talla')

    # --- Ubicación en el grupo ------------------------------------------
    scout_section_id = fields.Many2one('scout.section', string='Sección', tracking=True, index=True)
    scout_patrol_id = fields.Many2one('scout.patrol', string='Equipo', tracking=True,
                                      domain="[('section_id', '=', scout_section_id)]")
    scout_position_id = fields.Many2one('scout.position', string='Rol', tracking=True,
                                        domain="[('applies_to', 'in', ('both', scout_type))]",
                                        help='Guía, subguía, integrante, scouter, subjefe, jefe, secretaría, tesorería…')
    scout_patrol_role = fields.Selection(PATROL_ROLES, string='Rol en el equipo', compute='_compute_patrol_role',
                                         store=True)
    scout_leading_section_ids = fields.Many2many('scout.section', 'res_partner_scout_leading_section_rel',
                                                 'partner_id', 'section_id', string='Secciones a cargo',
                                                 help='Dirigentes: secciones que atiende (aparece en sus listas de asistencia).')
    scout_state = fields.Selection([('trial', 'En prueba'), ('active', 'Activo'), ('inactive', 'Baja')],
                                   string='Estado scout', default='trial', tracking=True, index=True)
    scout_join_date = fields.Date(string='Fecha de ingreso', default=fields.Date.context_today,
                                  help='Se propone la fecha del día; captura la real si el miembro ingresó antes.')
    scout_leave_date = fields.Date(string='Fecha de baja')
    scout_transition_date = fields.Date(string='Inicio de ruta de enlace', tracking=True)
    scout_next_section_due = fields.Boolean(string='Próximo a pasar', compute='_compute_next_section_due',
                                            search='_search_next_section_due')
    scout_notes = fields.Text(string='Observaciones scout')

    # --- Padres / tutor y emergencia ----------------------------------
    scout_guardian_id = fields.Many2one('res.partner', string='Padre, madre o tutor', tracking=True,
                                        help='Recibe avisos, paga los registros y accede al portal.')
    scout_guardian_relationship = fields.Char(string='Parentesco')
    scout_guardian_phone = fields.Char(related='scout_guardian_id.phone', string='Teléfono del tutor', readonly=False)
    scout_guardian_email = fields.Char(related='scout_guardian_id.email', string='Correo del tutor', readonly=False)
    scout_guardian_same_address = fields.Boolean(string='Mismo domicilio que el miembro', default=True)
    scout_guardian_street = fields.Char(related='scout_guardian_id.street', string='Calle (tutor)', readonly=False)
    scout_guardian_street2 = fields.Char(related='scout_guardian_id.street2', string='Colonia (tutor)', readonly=False)
    scout_guardian_city = fields.Char(related='scout_guardian_id.city', string='Ciudad (tutor)', readonly=False)
    scout_guardian_zip = fields.Char(related='scout_guardian_id.zip', string='C.P. (tutor)', readonly=False)
    scout_guardian_phone_alt = fields.Char(string='Teléfono alterno')
    scout_guardian2_id = fields.Many2one('res.partner', string='Segundo padre / madre')
    scout_guardian2_relationship = fields.Char(string='Parentesco (2)')
    scout_emergency_ids = fields.One2many('scout.emergency.contact', 'partner_id', string='Contactos de emergencia')

    # --- Ficha médica (restringida) -------------------------------------
    scout_medical_service = fields.Selection(MEDICAL_SERVICES, string='Servicio médico', groups=MEDICAL_GROUP)
    scout_medical_policy = fields.Char(string='No. de afiliación o póliza', groups=MEDICAL_GROUP)
    scout_blood_type = fields.Selection(BLOOD_TYPES, string='Tipo de sangre', groups=MEDICAL_GROUP)
    scout_allergies = fields.Text(string='Alergias', groups=MEDICAL_GROUP)
    scout_medications = fields.Text(string='Medicamentos permanentes', groups=MEDICAL_GROUP)
    scout_medical_conditions = fields.Text(string='Condiciones médicas relevantes', groups=MEDICAL_GROUP,
                                           help='Asma, diabetes, cirugías, hospitalizaciones, restricciones físicas…')
    scout_doctor = fields.Char(string='Médico / hospital de preferencia', groups=MEDICAL_GROUP)

    # --- Registro, progresión, adeudos, asistencia ------------------------
    scout_registration_ids = fields.One2many('scout.registration', 'member_id', string='Fichas de registro')
    scout_current_registration_id = fields.Many2one('scout.registration', compute='_compute_scout_registration',
                                                    string='Ficha del ciclo actual')
    scout_registration_paid = fields.Boolean(compute='_compute_scout_registration', string='Registro pagado')
    scout_registration_status = fields.Selection([
        ('none', 'Sin ficha'), ('pending', 'Ficha en revisión'), ('not_paid', 'Pendiente de pago'),
        ('partial', 'Pago parcial'), ('paid', 'Pagado'), ('exempt', 'Exento'),
    ], string='Registro', compute='_compute_scout_registration',
        help='Estado del registro del ciclo actual según la ficha y lo cargado por tesorería.')
    scout_progress_ids = fields.One2many('scout.member.progress', 'member_id', string='Adelantos e insignias')
    scout_promise_date = fields.Date(string='Fecha de Promesa', compute='_compute_scout_promise', store=True,
                                     inverse='_inverse_scout_promise_date')
    scout_last_progress_stage_id = fields.Many2one('scout.progression.stage', string='Último adelanto',
                                                   compute='_compute_scout_promise', store=True,
                                                   inverse='_inverse_scout_last_progress',
                                                   domain="[('stage_type', '!=', 'promise')]")
    scout_last_progress_date = fields.Date(string='Fecha del último adelanto', compute='_compute_scout_promise',
                                           store=True, inverse='_inverse_scout_last_progress')
    scout_last_progress = fields.Char(related='scout_last_progress_stage_id.name', string='Último adelanto (texto)')
    scout_invoice_ids = fields.One2many('account.move', 'scout_member_id', string='Cargos del miembro')
    scout_charge_line_ids = fields.One2many('account.move.line', 'scout_member_id', string='Detalle de cargos',
                                            domain=[('display_type', '=', 'product'),
                                                    ('move_id.move_type', 'in', ('out_invoice', 'out_refund')),
                                                    ('parent_state', '!=', 'cancel')])
    scout_amount_due = fields.Monetary(compute='_compute_scout_amount_due', string='Adeudo pendiente',
                                       currency_field='scout_currency_id')
    scout_currency_id = fields.Many2one('res.currency', compute='_compute_scout_currency')
    scout_attendance_line_ids = fields.One2many('scout.attendance.line', 'member_id', string='Asistencias')
    scout_attendance_count = fields.Integer(compute='_compute_scout_attendance', string='Asistencias (ciclo)')
    scout_attendance_total = fields.Integer(compute='_compute_scout_attendance', string='Sesiones (ciclo)')
    scout_attendance_pct = fields.Float(compute='_compute_scout_attendance', string='% Asistencia (ciclo)')
    scout_conversation_id = fields.Many2one('scout.family.conversation', string='Chat con la familia', copy=False)
    scout_change_ids = fields.One2many('scout.member.change', 'member_id', string='Solicitudes de cambio')

    # ------------------------------------------------------------------
    @api.depends('scout_position_id.kind')
    def _compute_patrol_role(self):
        for partner in self:
            kind = partner.scout_position_id.kind
            partner.scout_patrol_role = {'guide': 'leader', 'subguide': 'sub'}.get(kind, 'member')

    @api.depends('scout_birthdate')
    def _compute_scout_age(self):
        today = fields.Date.context_today(self)
        for partner in self:
            partner.scout_age = relativedelta(today, partner.scout_birthdate).years if partner.scout_birthdate else 0

    @api.depends('scout_birthdate', 'scout_section_id.age_max', 'scout_section_id.transition_age',
                 'scout_section_id.transition_notice_months', 'scout_type', 'is_scout')
    def _compute_next_section_due(self):
        today = fields.Date.context_today(self)
        for partner in self:
            section = partner.scout_section_id
            age = section.transition_age or section.age_max
            partner.scout_next_section_due = bool(
                partner.is_scout and partner.scout_type == 'youth' and age and partner.scout_birthdate
                and partner.scout_birthdate + relativedelta(years=age)
                <= today + relativedelta(months=section.transition_notice_months or 0))

    def _search_next_section_due(self, operator, value):
        if operator not in ('in', 'not in'):
            return NotImplemented
        today = fields.Date.context_today(self)
        domains = []
        for section in self.env['scout.section'].search(['|', ('age_max', '>', 0), ('transition_age', '>', 0)]):
            age = section.transition_age or section.age_max
            cutoff = today + relativedelta(months=section.transition_notice_months or 0) - relativedelta(years=age)
            domains.append(Domain([('scout_section_id', '=', section.id), ('scout_birthdate', '<=', cutoff),
                                   ('scout_type', '=', 'youth'), ('is_scout', '=', True)]))
        domain = Domain.OR(domains) if domains else Domain.FALSE
        positive = True in value
        if operator == 'not in':
            positive = not positive
        return domain if positive else ~domain

    def _compute_scout_registration(self):
        cycle = self.env['scout.cycle']._get_current()
        for partner in self:
            reg = partner.sudo().scout_registration_ids.filtered(
                lambda r: r.cycle_id == cycle and r.state != 'cancel')[:1]
            partner.scout_current_registration_id = reg
            partner.scout_registration_paid = reg.payment_state in ('paid', 'exempt') if reg else False
            if not reg:
                status = 'none'
            elif reg.state != 'validated' and reg.payment_state not in ('paid', 'exempt'):
                status = 'pending'
            else:
                status = reg.payment_state
            partner.scout_registration_status = status

    @api.depends('scout_progress_ids.state', 'scout_progress_ids.date_achieved', 'scout_progress_ids.stage_type')
    def _compute_scout_promise(self):
        for partner in self:
            achieved = partner.scout_progress_ids.filtered(lambda p: p.state == 'achieved')
            promise = achieved.filtered(lambda p: p.stage_type == 'promise')[:1]
            partner.scout_promise_date = promise.date_achieved
            last = achieved.filtered(lambda p: p.stage_type in ('stage', 'link', 'terminal')).sorted(
                lambda p: (p.date_achieved or fields.Date.today(), p.id))[-1:]
            partner.scout_last_progress_stage_id = last.stage_id
            partner.scout_last_progress_date = last.date_achieved

    def _inverse_scout_promise_date(self):
        """Capturar la fecha de Promesa crea (o corrige) el adelanto 'Promesa' del miembro."""
        promise = self.env.ref('scout_management.stage_promise', raise_if_not_found=False)
        Progress = self.env['scout.member.progress'].sudo()
        for partner in self.filtered('is_scout'):
            if not promise:
                continue
            line = partner.scout_progress_ids.filtered(lambda p: p.stage_id == promise)[:1]
            if partner.scout_promise_date:
                vals = {'state': 'achieved', 'date_achieved': partner.scout_promise_date}
                if line:
                    line.write(vals)
                else:
                    Progress.create(dict(vals, member_id=partner.id, stage_id=promise.id))
            elif line:
                line.unlink()

    def _inverse_scout_last_progress(self):
        """Capturar el último adelanto y su fecha lo registra como logrado en el historial."""
        Progress = self.env['scout.member.progress'].sudo()
        for partner in self.filtered(lambda p: p.is_scout and p.scout_last_progress_stage_id):
            stage = partner.scout_last_progress_stage_id
            date = partner.scout_last_progress_date or fields.Date.context_today(partner)
            line = partner.scout_progress_ids.filtered(lambda p: p.stage_id == stage)[:1]
            vals = {'state': 'achieved', 'date_achieved': date}
            if line:
                line.write(vals)
            else:
                Progress.create(dict(vals, member_id=partner.id, stage_id=stage.id))

    def _compute_scout_currency(self):
        for partner in self:
            partner.scout_currency_id = (partner.company_id or self.env.company).currency_id

    @api.depends('scout_invoice_ids.amount_residual', 'scout_invoice_ids.state')
    def _compute_scout_amount_due(self):
        for partner in self:
            invoices = partner.sudo().scout_invoice_ids.filtered(
                lambda m: m.state == 'posted' and m.move_type == 'out_invoice'
                and m.payment_state in ('not_paid', 'partial'))
            partner.scout_amount_due = sum(invoices.mapped('amount_residual'))

    def _scout_attendance_stats(self, date_from=None, date_to=None):
        """{partner_id: (asistencias, sesiones_contables)} usando las listas cerradas del calendario."""
        domain = Domain([('member_id', 'in', self.ids), ('event_id.scout_attendance_closed', '=', True)])
        if date_from:
            domain &= Domain('date', '>=', date_from)
        if date_to:
            domain &= Domain('date', '<=', date_to)
        rows = self.env['scout.attendance.line'].sudo()._read_group(
            domain, ['member_id'], ['attended:sum', 'counts:sum'])
        return {member.id: (int(att or 0), int(cnt or 0)) for member, att, cnt in rows}

    def _compute_scout_attendance(self):
        scouts = self.filtered('is_scout')
        (self - scouts).update({'scout_attendance_count': 0, 'scout_attendance_total': 0, 'scout_attendance_pct': 0.0})
        if not scouts:
            return
        cycle = self.env['scout.cycle']._get_current()
        stats = scouts._scout_attendance_stats(cycle.date_start, cycle.date_end) if cycle \
            else scouts._scout_attendance_stats()
        for partner in scouts:
            att, cnt = stats.get(partner.id, (0, 0))
            partner.scout_attendance_count = att
            partner.scout_attendance_total = cnt
            partner.scout_attendance_pct = (att / cnt * 100.0) if cnt else 0.0

    # ------------------------------------------------------------------
    @staticmethod
    def _scout_compose_name(first, last, last2):
        return ' '.join(p.strip() for p in (first, last, last2) if p and p.strip())

    @api.onchange('scout_first_name', 'scout_last_name', 'scout_last_name2')
    def _onchange_scout_name_parts(self):
        name = self._scout_compose_name(self.scout_first_name, self.scout_last_name, self.scout_last_name2)
        if name:
            self.name = name

    @api.onchange('scout_section_id')
    def _onchange_scout_section_id(self):
        if self.scout_patrol_id and self.scout_patrol_id.section_id != self.scout_section_id:
            self.scout_patrol_id = False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('scout_first_name') and not vals.get('name'):
                vals['name'] = self._scout_compose_name(
                    vals.get('scout_first_name'), vals.get('scout_last_name'), vals.get('scout_last_name2'))
        partners = super().create(vals_list)
        partners.filtered('is_scout')._scout_subscribe_followers()
        partners.filtered('is_scout')._scout_sync_guardian_address()
        return partners

    def write(self, vals):
        if not self.env.context.get('scout_change_approved') and set(PROTECTED_FIELDS) & set(vals) \
                and not self.env['scout.approval.mixin']._scout_is_manager():
            for partner in self.filtered('is_scout'):
                for fname in PROTECTED_FIELDS:
                    if fname in vals and partner[fname] and (
                            (partner[fname].id if fname == 'scout_section_id' else partner[fname]) != vals[fname]):
                        raise UserError(_(
                            'Las bajas y los cambios de sección necesitan aprobación del jefe de grupo. '
                            'Usa el botón "Solicitar baja" o "Solicitar cambio de sección" en el miembro %s.',
                            partner.display_name))
        if vals.get('scout_state') == 'inactive' and 'scout_leave_date' not in vals:
            vals['scout_leave_date'] = fields.Date.context_today(self)
        name_parts = {'scout_first_name', 'scout_last_name', 'scout_last_name2'} & set(vals)
        res = super().write(vals)
        if name_parts:
            for partner in self.filtered('scout_first_name'):
                name = self._scout_compose_name(partner.scout_first_name, partner.scout_last_name,
                                                partner.scout_last_name2)
                if name and name != partner.name:
                    super(ResPartner, partner).write({'name': name})
        if {'scout_section_id', 'scout_guardian_id', 'is_scout'} & set(vals):
            self.filtered('is_scout')._scout_subscribe_followers()
        if {'scout_guardian_same_address', 'scout_guardian_id', 'street', 'street2', 'city', 'zip', 'state_id'} & set(vals):
            self.filtered('is_scout')._scout_sync_guardian_address()
        return res

    def _scout_sync_guardian_address(self):
        """Si el tutor vive en el mismo domicilio, se le copia la dirección del miembro."""
        fnames = ('street', 'street2', 'city', 'zip', 'state_id', 'country_id')
        for partner in self.filtered(lambda p: p.scout_guardian_same_address and p.scout_guardian_id and p.street):
            guardian = partner.scout_guardian_id.sudo()
            vals = {f: partner[f].id if partner._fields[f].type == 'many2one' else partner[f] for f in fnames}
            current = {f: guardian[f].id if guardian._fields[f].type == 'many2one' else guardian[f] for f in fnames}
            if vals != current:
                guardian.write(vals)

    def _scout_subscribe_followers(self):
        """Los dirigentes de la sección siguen al miembro y a su chat; el tutor sigue su chat."""
        for partner in self:
            leaders = partner.scout_section_id.leader_ids.partner_id
            if leaders:
                partner.sudo().message_subscribe(partner_ids=leaders.ids)
            if partner.scout_conversation_id:
                partner.scout_conversation_id.sudo()._sync_followers()

    # ------------------------------------------------------------------ acciones
    def action_scout_request_leave(self):
        return self._scout_open_change('leave')

    def action_scout_request_section_change(self):
        return self._scout_open_change('section')

    def _scout_open_change(self, change_type):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Solicitar baja') if change_type == 'leave' else _('Solicitar cambio de sección'),
            'res_model': 'scout.member.change',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_member_id': self.id,
                'default_change_type': change_type,
                'default_new_section_id': self.scout_section_id.next_section_id.id if change_type == 'section' else False,
            },
        }

    def action_scout_activate(self):
        self.with_context(scout_change_approved=True).filtered(
            lambda p: p.scout_state == 'trial').write({'scout_state': 'active'})

    def action_scout_new_registration(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'scout.registration',
            'view_mode': 'form',
            'context': {'default_member_id': self.id},
        }

    def action_scout_view_attendance(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Asistencia de %s', self.name),
            'res_model': 'scout.attendance.line',
            'view_mode': 'list,pivot',
            'domain': [('member_id', '=', self.id)],
        }

    def action_scout_view_invoices(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('account.action_move_out_invoice_type')
        action['domain'] = [('scout_member_id', '=', self.id)]
        action['context'] = {'default_move_type': 'out_invoice', 'default_scout_member_id': self.id,
                             'default_partner_id': (self.scout_guardian_id or self).id}
        return action

    def action_scout_open_conversation(self):
        self.ensure_one()
        conversation = self.env['scout.family.conversation'].sudo()._get_or_create_for_member(self)
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'scout.family.conversation',
            'res_id': conversation.id,
            'view_mode': 'form',
        }


class ScoutEmergencyContact(models.Model):
    _name = 'scout.emergency.contact'
    _description = 'Contacto de emergencia'
    _order = 'sequence, id'

    partner_id = fields.Many2one('res.partner', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Nombre', required=True)
    relationship = fields.Char(string='Parentesco')
    phone = fields.Char(string='Teléfono', required=True)
    phone_alt = fields.Char(string='Teléfono alterno')
    note = fields.Char(string='Nota')
