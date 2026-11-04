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
    registration_fee = fields.Monetary(string='Cuota de registro', currency_field='currency_id',
                                       help='Cuota normal (por ejemplo, a partir de enero).')
    early_fee = fields.Monetary(string='Cuota anticipada', currency_field='currency_id',
                                help='Cuota más baja mientras dura el periodo anticipado (ej. octubre a diciembre).')
    early_from = fields.Date(string='Anticipada desde')
    early_until = fields.Date(string='Anticipada hasta', help='Después de esta fecha se cobra la cuota normal.')
    leader_fee = fields.Monetary(string='Cuota dirigentes e hijos de dirigentes', currency_field='currency_id',
                                 help='Se aplica a dirigentes y a los miembros cuyo tutor es dirigente '
                                      '(o marcados como "Hijo(a) de dirigente"), en cualquier fecha.')
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

    # ------------------------------------------------------------------ cuotas
    @staticmethod
    def _is_leader_family(member):
        member = member.sudo() if member else member
        if not member:
            return False
        if member.scout_type == 'leader' or getattr(member, 'scout_leader_child', False):
            return True
        guardian = member.scout_guardian_id
        return bool(guardian and guardian.is_scout and guardian.scout_type == 'leader'
                    and guardian.scout_state != 'inactive')

    def _in_early(self, on_date=None):
        self.ensure_one()
        if not self.early_fee or not self.early_until:
            return False
        day = on_date or fields.Date.context_today(self)
        return (not self.early_from or day >= self.early_from) and day <= self.early_until

    def _fee_for(self, member=None, on_date=None):
        """Cuota que le toca a este miembro hoy (o en la fecha dada)."""
        self.ensure_one()
        if self.leader_fee and self._is_leader_family(member):
            return self.leader_fee
        if self._in_early(on_date):
            return self.early_fee
        return self.registration_fee

    @staticmethod
    def _money(value):
        value = value or 0.0
        return ('${:,.0f}' if float(value).is_integer() else '${:,.2f}').format(value)

    @staticmethod
    def _month_label(day, start=True):
        months = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre',
                  'octubre', 'noviembre', 'diciembre']
        if not day:
            return ''
        first = day.day == 1 if start else (day + __import__('datetime').timedelta(days=1)).day == 1
        return months[day.month - 1] if first else '%s de %s' % (day.day, months[day.month - 1])

    def _fee_text(self, member=None):
        """Texto de cuotas para mensajes y fichas, p. ej.:
        'Cuota de registro: $250 de octubre a diciembre; a partir del 1 de enero $300. Dirigentes e hijos de
        dirigentes: $200.'"""
        self.ensure_one()
        from datetime import timedelta
        parts = []
        if self.early_fee and self.early_until:
            since = self.early_until + timedelta(days=1)
            if self.early_from:
                period = 'de %s a %s' % (self._month_label(self.early_from), self._month_label(self.early_until, False))
            else:
                period = 'hasta el %s' % self.early_until.strftime('%d/%m/%Y')
            months = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre',
                      'octubre', 'noviembre', 'diciembre']
            parts.append('Cuota de registro: %s %s; a partir del %s de %s %s' % (
                self._money(self.early_fee), period, since.day, months[since.month - 1],
                self._money(self.registration_fee)))
        elif self.registration_fee:
            parts.append('Cuota de registro: %s' % self._money(self.registration_fee))
        if self.leader_fee:
            parts.append('Dirigentes e hijos de dirigentes: %s' % self._money(self.leader_fee))
        text = '. '.join(parts)
        if text and member:
            fee = self._fee_for(member)
            if fee:
                text += '. A %s le corresponden %s' % (member.name, self._money(fee))
        return (text + '.') if text else ''

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
