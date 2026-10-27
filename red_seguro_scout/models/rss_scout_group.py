# -*- coding: utf-8 -*-
from odoo import api, fields, models, _

from .rss_common import check_email, check_pdf_attachments


class RssScoutGroup(models.Model):
    _name = 'rss.scout.group'
    _description = 'Grupo scout'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, number, name'

    sequence = fields.Integer(default=10)
    name = fields.Char('Grupo', required=True, tracking=True,
                       help='Nombre con el que se conoce al grupo, p. ej. "Grupo 7 Ethnos".')
    number = fields.Integer('Número', tracking=True)
    cluster_id = fields.Many2one('rss.group.cluster', string='Agrupación', tracking=True,
                                 help='Distrito, provincia o región al que pertenece el grupo.')
    policy_id = fields.Many2one('rss.policy', string='Póliza', tracking=True,
                                default=lambda self: self._default_policy())
    company_id = fields.Many2one('res.company', string='Organización',
                                 default=lambda self: self.env.company, required=True)
    leader_name = fields.Char('Jefe de grupo', tracking=True)
    leader_phone = fields.Char('Teléfono del jefe')
    leader_email = fields.Char('Correo del jefe')
    treasurer_name = fields.Char('Tesorero(a)')
    city = fields.Char('Ciudad')
    meeting_place = fields.Char('Lugar de reunión')
    color = fields.Integer('Color')
    active = fields.Boolean(default=True)
    notes = fields.Html('Notas')
    user_ids = fields.Many2many(
        'res.users', 'rss_scout_group_users_rel', 'group_id', 'user_id',
        string='Usuarios con acceso',
        help='Usuarios del sistema (jefe de grupo, tesorero...) que reciben acceso a '
             'Red Seguro Scout cuando el grupo paga su seguro.')

    member_ids = fields.One2many('rss.insured', 'group_id', string='Miembros')
    member_count = fields.Integer('Asegurados activos', compute='_compute_members',
                                  store=True, aggregator='sum')
    draft_count = fields.Integer('Pre-registros', compute='_compute_members',
                                 store=True, aggregator='sum')
    male_count = fields.Integer('Hombres', compute='_compute_members', store=True,
                                aggregator='sum')
    female_count = fields.Integer('Mujeres', compute='_compute_members', store=True,
                                  aggregator='sum')
    minor_count = fields.Integer('Menores de edad', compute='_compute_members', store=True,
                                 aggregator='sum')
    adult_count = fields.Integer('Adultos', compute='_compute_members', store=True,
                                 aggregator='sum')

    payment_ids = fields.One2many('rss.payment', 'group_id', string='Pagos')
    payment_count = fields.Integer(compute='_compute_payment_info')
    paid_current_year = fields.Boolean('Pagado este año', compute='_compute_payment_info',
                                       search='_search_paid_current_year')
    last_payment_date = fields.Date('Último pago', compute='_compute_payment_info')

    attachment_ids = fields.Many2many(
        'ir.attachment', 'rss_group_attachment_rel', 'group_id', 'attachment_id',
        string='Expediente PDF del grupo')
    cluster_attachment_ids = fields.Many2many(
        related='cluster_id.attachment_ids', string='Documentos de la agrupación')
    document_count = fields.Integer('Documentos', compute='_compute_document_count')

    _name_company_uniq = models.Constraint(
        'UNIQUE (name, company_id)',
        'Ya existe un grupo con ese nombre en la organización.',
    )

    @api.model
    def _default_policy(self):
        return self.env['rss.policy'].search(
            [('company_id', 'in', [self.env.company.id, False])], limit=1)

    @api.depends('member_ids.state', 'member_ids.sex', 'member_ids.age')
    def _compute_members(self):
        for group in self:
            active = group.member_ids.filtered(lambda m: m.state == 'active')
            group.member_count = len(active)
            group.draft_count = len(group.member_ids.filtered(lambda m: m.state == 'draft'))
            group.male_count = len(active.filtered(lambda m: m.sex == 'male'))
            group.female_count = len(active.filtered(lambda m: m.sex == 'female'))
            group.minor_count = len(active.filtered(lambda m: m.age < 18))
            group.adult_count = group.member_count - group.minor_count

    def _get_paid_years(self):
        self.ensure_one()
        return set(self.payment_ids.filtered(
            lambda p: p.state in ('confirmed', 'remitted')).mapped('year'))

    @api.depends('payment_ids.state', 'payment_ids.year', 'payment_ids.date')
    def _compute_payment_info(self):
        year = fields.Date.context_today(self).year
        for group in self:
            valid = group.payment_ids.filtered(lambda p: p.state in ('confirmed', 'remitted'))
            group.payment_count = len(group.payment_ids)
            group.paid_current_year = year in valid.mapped('year')
            group.last_payment_date = max(valid.mapped('date')) if valid else False

    def _search_paid_current_year(self, operator, value):
        if operator not in ('=', '!='):
            return NotImplemented
        year = fields.Date.context_today(self).year
        paid = self.env['rss.payment'].search([
            ('state', 'in', ('confirmed', 'remitted')), ('year', '=', year),
        ]).group_id
        positive = (operator == '=') == bool(value)
        return [('id', 'in' if positive else 'not in', paid.ids)]

    @api.depends('attachment_ids', 'cluster_attachment_ids')
    def _compute_document_count(self):
        for group in self:
            group.document_count = len(group.attachment_ids) + len(group.cluster_attachment_ids)

    @api.constrains('attachment_ids')
    def _check_attachment_pdf(self):
        for group in self:
            check_pdf_attachments(group.attachment_ids, _('el expediente del grupo'))

    @api.constrains('leader_email')
    def _check_leader_email(self):
        for group in self:
            check_email(group.leader_email, _('el jefe de grupo'))

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------
    def action_view_members(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Asegurados de %s', self.name),
            'res_model': 'rss.insured',
            'view_mode': 'list,form,pivot,graph',
            'domain': [('group_id', '=', self.id)],
            'context': {'default_group_id': self.id},
        }

    def action_view_payments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Pagos de %s', self.name),
            'res_model': 'rss.payment',
            'view_mode': 'list,form',
            'domain': [('group_id', '=', self.id)],
            'context': {'default_group_id': self.id},
        }

    def action_new_payment(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Registrar pago del seguro'),
            'res_model': 'rss.payment',
            'view_mode': 'form',
            'context': {'default_group_id': self.id},
        }

    def action_attach_pdf(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Adjuntar PDF a grupos'),
            'res_model': 'rss.attach.pdf.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_group_ids': self.ids},
        }
