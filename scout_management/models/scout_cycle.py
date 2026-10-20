# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ScoutCycle(models.Model):
    _name = 'scout.cycle'
    _description = 'Ciclo scout'
    _order = 'date_start desc'

    name = fields.Char(string='Ciclo', required=True, help='Ej. 2026')
    date_start = fields.Date(string='Inicio', required=True)
    date_end = fields.Date(string='Fin', required=True)
    registration_fee = fields.Monetary(string='Cuota de registro', currency_field='currency_id')
    is_current = fields.Boolean(string='Ciclo actual', help='Ciclo que se usa por defecto en reportes y credenciales.')
    registration_open = fields.Boolean(
        string='Inscripciones abiertas',
        help='Las fichas de nuevos ingresos y las reinscripciones se hacen para este ciclo (por ejemplo, abre 2027 '
             'mientras 2026 sigue como ciclo actual). Si ningún ciclo lo tiene, se usa el ciclo actual.')
    company_id = fields.Many2one('res.company', string='Grupo', required=True,
                                 default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    active = fields.Boolean(default=True)

    _name_company_uniq = models.Constraint(
        'UNIQUE (name, company_id)', 'Ya existe un ciclo con ese nombre en este grupo.')

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for cycle in self:
            if cycle.date_end < cycle.date_start:
                raise ValidationError(_('La fecha de fin del ciclo debe ser posterior a la de inicio.'))

    @api.constrains('is_current', 'company_id')
    def _check_single_current(self):
        for cycle in self.filtered('is_current'):
            others = self.search_count([
                ('is_current', '=', True),
                ('company_id', '=', cycle.company_id.id),
                ('id', '!=', cycle.id),
            ])
            if others:
                raise ValidationError(_('Solo puede haber un ciclo actual por grupo.'))

    @api.model
    def _get_current(self, company=None):
        company = company or self.env.company
        cycle = self.search([('is_current', '=', True), ('company_id', '=', company.id)], limit=1)
        if not cycle:
            today = fields.Date.context_today(self)
            cycle = self.search([
                ('date_start', '<=', today), ('date_end', '>=', today),
                ('company_id', '=', company.id),
            ], limit=1)
        if not cycle:
            # Formularios públicos: la compañía del sitio web puede no ser la del ciclo
            cycle = self.search([('is_current', '=', True)], limit=1)
        return cycle

    @api.model
    def _get_registration(self, company=None):
        """Ciclo al que van las fichas nuevas y las reinscripciones."""
        company = company or self.env.company
        cycle = self.search([('registration_open', '=', True), ('company_id', '=', company.id)],
                            order='date_start desc', limit=1)
        if not cycle:
            cycle = self.search([('registration_open', '=', True)], order='date_start desc', limit=1)
        return cycle or self._get_current(company)
