# -*- coding: utf-8 -*-
import base64

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class RssAttachPdfWizard(models.TransientModel):
    """Adjunta un mismo PDF al expediente de varios grupos a la vez
    (o a la agrupación completa)."""
    _name = 'rss.attach.pdf.wizard'
    _description = 'Adjuntar PDF a grupos'

    cluster_id = fields.Many2one('rss.group.cluster', string='Agrupación')
    group_ids = fields.Many2many('rss.scout.group', string='Grupos')
    file = fields.Binary('Archivo PDF', required=True)
    filename = fields.Char('Nombre del archivo')
    description = fields.Char('Descripción')
    attach_to_cluster = fields.Boolean(
        'Guardar también en la agrupación', default=True,
        help='El PDF queda en los documentos de la agrupación y lo ven todos sus grupos.')

    @api.onchange('cluster_id')
    def _onchange_cluster(self):
        if self.cluster_id and not self.group_ids:
            self.group_ids = self.cluster_id.group_ids

    def action_attach(self):
        self.ensure_one()
        name = self.filename or 'documento.pdf'
        raw = base64.b64decode(self.file or b'')
        if not name.lower().endswith('.pdf') or not raw.startswith(b'%PDF'):
            raise UserError(_('El archivo debe ser un PDF.'))
        if not self.group_ids and not (self.cluster_id and self.attach_to_cluster):
            raise UserError(_('Selecciona al menos un grupo o una agrupación.'))
        Attachment = self.env['ir.attachment']
        vals = {'name': name, 'raw': raw, 'mimetype': 'application/pdf',
                'description': self.description}
        for group in self.group_ids:
            att = Attachment.create(dict(vals, res_model='rss.scout.group', res_id=group.id))
            group.attachment_ids = [(4, att.id)]
            group.message_post(body=_('PDF "%s" agregado al expediente.', name),
                               attachment_ids=[])
        if self.cluster_id and self.attach_to_cluster:
            att = Attachment.create(dict(vals, res_model='rss.group.cluster',
                                         res_id=self.cluster_id.id))
            self.cluster_id.attachment_ids = [(4, att.id)]
            self.cluster_id.message_post(body=_('PDF "%s" agregado a la agrupación.', name))
        return {'type': 'ir.actions.act_window_close'}
