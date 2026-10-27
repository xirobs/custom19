# -*- coding: utf-8 -*-
from odoo import api, fields, models, _

from .rss_common import check_pdf_attachments


class RssGroupCluster(models.Model):
    """Agrupación de grupos (distrito, provincia, región...).

    Sirve para reunir varios grupos y adjuntar documentos PDF que aplican a
    todos ellos (póliza firmada, condiciones generales, circulares, etc.).
    """
    _name = 'rss.group.cluster'
    _description = 'Agrupación de grupos scouts'
    _inherit = ['mail.thread']
    _order = 'name'

    name = fields.Char('Agrupación', required=True, tracking=True)
    code = fields.Char('Clave')
    kind = fields.Selection([
        ('district', 'Distrito'),
        ('province', 'Provincia'),
        ('region', 'Región'),
        ('other', 'Otra'),
    ], string='Tipo', default='district', required=True)
    responsible_name = fields.Char('Responsable')
    responsible_email = fields.Char('Correo del responsable')
    group_ids = fields.One2many('rss.scout.group', 'cluster_id', string='Grupos')
    group_count = fields.Integer('Núm. de grupos', compute='_compute_totals')
    member_count = fields.Integer('Asegurados activos', compute='_compute_totals')
    attachment_ids = fields.Many2many(
        'ir.attachment', 'rss_cluster_attachment_rel', 'cluster_id', 'attachment_id',
        string='Documentos PDF de la agrupación')
    notes = fields.Html('Notas')
    company_id = fields.Many2one('res.company', string='Organización',
                                 default=lambda self: self.env.company)
    color = fields.Integer('Color')
    active = fields.Boolean(default=True)

    @api.depends('group_ids.member_count')
    def _compute_totals(self):
        for cluster in self:
            cluster.group_count = len(cluster.group_ids)
            cluster.member_count = sum(cluster.group_ids.mapped('member_count'))

    @api.constrains('attachment_ids')
    def _check_attachment_pdf(self):
        for cluster in self:
            check_pdf_attachments(cluster.attachment_ids, _('la agrupación'))

    def action_attach_pdf_to_groups(self):
        """Abre el asistente para copiar un PDF a todos los grupos de la agrupación."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Adjuntar PDF a grupos'),
            'res_model': 'rss.attach.pdf.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_cluster_id': self.id,
                'default_group_ids': self.group_ids.ids,
            },
        }

    def action_view_groups(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.name,
            'res_model': 'rss.scout.group',
            'view_mode': 'kanban,list,form,graph',
            'domain': [('cluster_id', '=', self.id)],
            'context': {'default_cluster_id': self.id},
        }
