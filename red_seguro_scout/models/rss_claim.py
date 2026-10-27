# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .rss_common import INJURY_SELECTION


class RssClaim(models.Model):
    """Siniestro: accidente de un asegurado durante una actividad scout."""
    _name = 'rss.claim'
    _description = 'Siniestro (accidente)'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_accident desc, id desc'

    name = fields.Char('Folio', default=lambda self: _('Nuevo'), readonly=True, copy=False)
    insured_id = fields.Many2one('rss.insured', string='Asegurado', required=True,
                                 tracking=True, index=True)
    group_id = fields.Many2one(related='insured_id.group_id', store=True, string='Grupo')
    cluster_id = fields.Many2one(related='insured_id.cluster_id', store=True,
                                 string='Agrupación')
    policy_id = fields.Many2one(related='insured_id.policy_id', store=True, string='Póliza')
    company_id = fields.Many2one(related='insured_id.company_id', store=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    tutor_name = fields.Char(related='insured_id.tutor_name')
    tutor_phone = fields.Char(related='insured_id.tutor_phone')
    date_accident = fields.Date('Fecha del accidente', required=True, tracking=True,
                                default=fields.Date.context_today)
    activity_type = fields.Selection([
        ('weekly', 'Reunión semanal'),
        ('camp', 'Campamento'),
        ('hike', 'Excursión / caminata'),
        ('sport', 'Actividad deportiva'),
        ('service', 'Servicio comunitario'),
        ('travel', 'Traslado a la actividad'),
        ('other', 'Otra'),
    ], string='Actividad', required=True, default='weekly', tracking=True)
    place = fields.Char('Lugar')
    injury_type = fields.Selection(INJURY_SELECTION, string='Tipo de lesión', required=True,
                                   tracking=True)
    body_part = fields.Char('Parte del cuerpo')
    description = fields.Text('¿Qué pasó?')
    medical_center = fields.Char('Hospital / clínica')
    amount_claimed = fields.Monetary('Monto reclamado', tracking=True)
    amount_paid = fields.Monetary('Monto pagado por la aseguradora', tracking=True)
    insurer_claim_number = fields.Char('Número de siniestro (aseguradora)', tracking=True)
    covered_on_date = fields.Boolean('Con cobertura ese día', compute='_compute_covered',
                                     store=True)
    state = fields.Selection([
        ('reported', 'Reportado'),
        ('in_process', 'En trámite'),
        ('approved', 'Aprobado'),
        ('paid', 'Pagado'),
        ('rejected', 'Rechazado'),
    ], string='Estatus', default='reported', required=True, tracking=True)
    attachment_ids = fields.Many2many('ir.attachment', 'rss_claim_attachment_rel',
                                      'claim_id', 'attachment_id',
                                      string='Documentos (informe médico, facturas, fotos)')

    @api.depends('insured_id.date_start', 'insured_id.date_end', 'insured_id.state',
                 'date_accident')
    def _compute_covered(self):
        for claim in self:
            claim.covered_on_date = bool(claim.insured_id and claim.date_accident
                                         and claim.insured_id.is_covered_on(claim.date_accident))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('Nuevo')) in (_('Nuevo'), 'Nuevo', False):
                vals['name'] = self.env['ir.sequence'].next_by_code('rss.claim') or _('Nuevo')
        claims = super().create(vals_list)
        for claim in claims.filtered(lambda c: not c.covered_on_date):
            claim.message_post(body=_('Atención: %(name)s no tenía cobertura vigente el '
                                      '%(date)s.', name=claim.insured_id.name,
                                      date=claim.date_accident.strftime('%d/%m/%Y')))
        return claims

    def action_in_process(self):
        self.write({'state': 'in_process'})

    def action_approve(self):
        self.write({'state': 'approved'})

    def action_paid(self):
        for claim in self:
            if not claim.amount_paid:
                raise UserError(_('Captura el monto pagado por la aseguradora.'))
        self.write({'state': 'paid'})

    def action_reject(self):
        self.write({'state': 'rejected'})

    def action_reopen(self):
        self.write({'state': 'reported'})
