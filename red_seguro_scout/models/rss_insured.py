# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .rss_common import (
    CURP_RE, SECTION_SELECTION, SEX_SELECTION, age_on, check_email, section_for_age,
)

_logger = logging.getLogger(__name__)


class RssInsured(models.Model):
    _name = 'rss.insured'
    _description = 'Asegurado scout'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'group_id, last_name_father, last_name_mother, first_name'
    _rec_names_search = ['name', 'curp', 'member_number', 'tutor_name']

    # Identificación ------------------------------------------------------
    name = fields.Char('Nombre completo', compute='_compute_name', store=True, index=True)
    first_name = fields.Char('Primer nombre', required=True, tracking=True)
    second_name = fields.Char('Segundo nombre', tracking=True)
    last_name_father = fields.Char('Apellido paterno', required=True, tracking=True)
    last_name_mother = fields.Char('Apellido materno', tracking=True)
    birthdate = fields.Date('Fecha de nacimiento', required=True, tracking=True)
    age = fields.Integer('Edad', compute='_compute_age', store=True, aggregator='avg',
                         help='Se recalcula diariamente.')
    is_minor = fields.Boolean('Menor de edad', compute='_compute_age', store=True)
    sex = fields.Selection(SEX_SELECTION, string='Sexo', required=True, tracking=True)
    curp = fields.Char('CURP', size=18)
    member_number = fields.Char('Núm. de credencial')

    # Grupo ---------------------------------------------------------------
    group_id = fields.Many2one('rss.scout.group', string='Grupo', required=True,
                               ondelete='restrict', tracking=True, index=True)
    cluster_id = fields.Many2one(related='group_id.cluster_id', store=True,
                                 string='Agrupación')
    policy_id = fields.Many2one(related='group_id.policy_id', store=True, string='Póliza')
    company_id = fields.Many2one(related='group_id.company_id', store=True,
                                 string='Organización')
    section = fields.Selection(SECTION_SELECTION, string='Rama / sección',
                               compute='_compute_section', store=True, readonly=False,
                               tracking=True)

    # Tutor ---------------------------------------------------------------
    tutor_name = fields.Char('Nombre completo del tutor', tracking=True,
                             help='Padre, madre o tutor legal.')
    tutor_relationship = fields.Selection([
        ('father', 'Padre'),
        ('mother', 'Madre'),
        ('tutor', 'Tutor(a) legal'),
        ('other', 'Otro'),
    ], string='Parentesco')
    tutor_phone = fields.Char('Teléfono de emergencia', tracking=True,
                              help='Teléfono o WhatsApp del padre, madre o tutor para emergencias.')
    tutor_email = fields.Char('Correo del tutor', tracking=True)

    # Cobertura -----------------------------------------------------------
    state = fields.Selection([
        ('draft', 'Pre-registro'),
        ('active', 'Asegurado'),
        ('inactive', 'Baja'),
    ], string='Estatus', default='draft', required=True, tracking=True, index=True)
    date_start = fields.Date('Fecha de alta', tracking=True,
                             help='Desde esta fecha el miembro aparece en la relación mensual.')
    date_end = fields.Date('Fecha de baja', tracking=True)
    claim_ids = fields.One2many('rss.claim', 'insured_id', string='Siniestros')
    claim_count = fields.Integer(compute='_compute_claim_count')
    notes = fields.Text('Observaciones')
    color = fields.Integer('Color')
    active = fields.Boolean(default=True)

    _curp_uniq = models.Constraint(
        'UNIQUE (curp)', 'Ya existe un asegurado con esa CURP.',
    )

    # ------------------------------------------------------------------
    # Cómputos
    # ------------------------------------------------------------------
    @api.depends('first_name', 'second_name', 'last_name_father', 'last_name_mother')
    def _compute_name(self):
        for rec in self:
            parts = [rec.first_name, rec.second_name, rec.last_name_father, rec.last_name_mother]
            rec.name = ' '.join(p.strip() for p in parts if p and p.strip())

    @api.depends('birthdate')
    def _compute_age(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.age = age_on(rec.birthdate, today)
            rec.is_minor = bool(rec.birthdate) and rec.age < 18

    @api.depends('birthdate')
    def _compute_section(self):
        for rec in self:
            if rec.birthdate and not rec.section:
                rec.section = section_for_age(rec.age)

    def _compute_claim_count(self):
        data = self.env['rss.claim']._read_group(
            [('insured_id', 'in', self.ids)], ['insured_id'], ['__count'])
        counts = {insured.id: count for insured, count in data}
        for rec in self:
            rec.claim_count = counts.get(rec.id, 0)

    # ------------------------------------------------------------------
    # Validaciones
    # ------------------------------------------------------------------
    @api.constrains('birthdate')
    def _check_birthdate(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.birthdate and rec.birthdate > today:
                raise ValidationError(_('La fecha de nacimiento de %s no puede ser futura.',
                                        rec.name))

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for rec in self:
            if rec.date_start and rec.date_end and rec.date_end < rec.date_start:
                raise ValidationError(_('La fecha de baja de %s es anterior a su alta.',
                                        rec.name))

    @api.constrains('tutor_email')
    def _check_tutor_email(self):
        for rec in self:
            check_email(rec.tutor_email, _('el tutor de %s', rec.name))

    @api.constrains('curp')
    def _check_curp(self):
        for rec in self:
            if rec.curp and not CURP_RE.match(rec.curp):
                raise ValidationError(_('La CURP "%s" no tiene un formato válido.', rec.curp))

    @api.constrains('birthdate', 'tutor_name', 'tutor_phone', 'state')
    def _check_tutor(self):
        for rec in self:
            if rec.is_minor and rec.state == 'active' and not (rec.tutor_name and rec.tutor_phone):
                raise ValidationError(_(
                    '%s es menor de edad: captura el nombre y teléfono del tutor antes '
                    'de darlo de alta en el seguro.', rec.name))

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    @api.model
    def _normalize_vals(self, vals):
        if vals.get('curp'):
            vals['curp'] = vals['curp'].strip().upper()
        for key in ('first_name', 'second_name', 'last_name_father', 'last_name_mother',
                    'tutor_name'):
            if vals.get(key):
                vals[key] = ' '.join(vals[key].split())
        if vals.get('tutor_email'):
            vals['tutor_email'] = vals['tutor_email'].strip().lower()
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._normalize_vals(vals) for vals in vals_list])

    def write(self, vals):
        return super().write(self._normalize_vals(vals))

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def action_activate(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.write({
                'state': 'active',
                'date_start': rec.date_start or today,
                'date_end': False,
            })

    def action_deactivate(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.state != 'active':
                raise UserError(_('Solo se puede dar de baja a un asegurado activo (%s).',
                                  rec.name))
            rec.write({'state': 'inactive', 'date_end': rec.date_end or today})

    def action_reset_draft(self):
        self.write({'state': 'draft'})

    def action_view_claims(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Siniestros de %s', self.name),
            'res_model': 'rss.claim',
            'view_mode': 'list,form',
            'domain': [('insured_id', '=', self.id)],
            'context': {'default_insured_id': self.id},
        }

    def action_new_claim(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reportar siniestro'),
            'res_model': 'rss.claim',
            'view_mode': 'form',
            'context': {'default_insured_id': self.id},
        }

    def is_covered_on(self, day):
        """True si el asegurado tenía cobertura vigente en ``day``."""
        self.ensure_one()
        return bool(self.date_start and self.date_start <= day
                    and (not self.date_end or self.date_end >= day)
                    and self.state in ('active', 'inactive'))

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------
    @api.model
    def _cron_update_ages(self):
        """Recalcula la edad (y por tanto menor/mayor de edad) cada día."""
        today = fields.Date.context_today(self)
        records = self.with_context(active_test=False).search([('birthdate', '!=', False)])
        changed = records.filtered(lambda r: r.age != age_on(r.birthdate, today))
        if changed:
            self.env.add_to_compute(self._fields['age'], changed)
            self.env.add_to_compute(self._fields['is_minor'], changed)
            changed.flush_recordset(['age', 'is_minor'])
            _logger.info('Red Seguro Scout: edad actualizada para %s asegurados.', len(changed))
