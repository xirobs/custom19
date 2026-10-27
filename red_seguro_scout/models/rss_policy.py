# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

from .rss_common import INJURY_SELECTION, check_email


class RssPolicy(models.Model):
    _name = 'rss.policy'
    _description = 'Póliza de seguro scout'
    _inherit = ['mail.thread']
    _order = 'date_start desc, id desc'

    name = fields.Char('Póliza', required=True, tracking=True)
    policy_number = fields.Char('Número de póliza', tracking=True)
    insurer_id = fields.Many2one('res.partner', string='Aseguradora', tracking=True)
    insurer_email = fields.Char(
        'Correo para relaciones', compute='_compute_insurer_email', store=True,
        readonly=False, tracking=True,
        help='Correo al que se envía la relación mensual de asegurados.')
    insurer_cc = fields.Char('Copia (CC)', help='Correos adicionales separados por coma.')
    coverage_type = fields.Char(
        'Tipo de cobertura', default='Accidentes personales en actividades scouts')
    coverage_line_ids = fields.One2many('rss.policy.coverage', 'policy_id', string='Coberturas')
    coverage_notes = fields.Html('Condiciones / exclusiones')
    company_id = fields.Many2one('res.company', string='Organización',
                                 default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related='company_id.currency_id', string='Moneda')
    premium_per_member = fields.Monetary('Cuota anual por miembro', tracking=True)
    date_start = fields.Date('Vigencia desde', tracking=True)
    date_end = fields.Date('Vigencia hasta', tracking=True)
    report_day = fields.Integer(
        'Entregar relación a más tardar el día', default=10, tracking=True,
        help='La relación mensual se genera automáticamente a partir del día 1 y '
             'debe enviarse a la aseguradora a más tardar este día del mes.')
    auto_generate = fields.Boolean('Generar relación automáticamente', default=True)
    auto_send = fields.Boolean(
        'Enviar automáticamente a la aseguradora', default=False,
        help='Si se activa, la relación generada por el sistema se envía por correo '
             'a la aseguradora con el Excel y el PDF adjuntos.')
    only_paid_groups = fields.Boolean(
        'Solo grupos con pago del año', default=False,
        help='Incluir en la relación únicamente a los grupos con pago confirmado del ejercicio.')
    group_ids = fields.One2many('rss.scout.group', 'policy_id', string='Grupos')
    group_count = fields.Integer(compute='_compute_counts')
    member_count = fields.Integer('Asegurados activos', compute='_compute_counts')
    active = fields.Boolean(default=True)

    _report_day_range = models.Constraint(
        'CHECK (report_day BETWEEN 1 AND 28)',
        'El día límite de la relación debe estar entre 1 y 28.',
    )

    @api.depends('insurer_id.email')
    def _compute_insurer_email(self):
        for policy in self:
            if policy.insurer_id.email:
                policy.insurer_email = policy.insurer_id.email
            elif not policy.insurer_email:
                policy.insurer_email = False

    @api.depends('group_ids.member_count')
    def _compute_counts(self):
        for policy in self:
            policy.group_count = len(policy.group_ids)
            policy.member_count = sum(policy.group_ids.mapped('member_count'))

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for policy in self:
            if policy.date_start and policy.date_end and policy.date_end < policy.date_start:
                raise ValidationError(_('La vigencia final no puede ser anterior a la inicial.'))

    @api.constrains('insurer_email', 'insurer_cc')
    def _check_emails(self):
        for policy in self:
            check_email(policy.insurer_email, _('la aseguradora'))
            for cc in (policy.insurer_cc or '').split(','):
                check_email(cc.strip(), _('copia'))

    def action_view_groups(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Grupos'),
            'res_model': 'rss.scout.group',
            'view_mode': 'kanban,list,form',
            'domain': [('policy_id', '=', self.id)],
            'context': {'default_policy_id': self.id},
        }


class RssPolicyCoverage(models.Model):
    _name = 'rss.policy.coverage'
    _description = 'Cobertura de la póliza'
    _order = 'sequence, id'

    sequence = fields.Integer(default=10)
    policy_id = fields.Many2one('rss.policy', required=True, ondelete='cascade')
    name = fields.Char('Cobertura', required=True)
    injury_type = fields.Selection(INJURY_SELECTION, string='Tipo de lesión')
    currency_id = fields.Many2one(related='policy_id.currency_id')
    sum_insured = fields.Monetary('Suma asegurada')
    deductible = fields.Monetary('Deducible')
    notes = fields.Char('Notas')
