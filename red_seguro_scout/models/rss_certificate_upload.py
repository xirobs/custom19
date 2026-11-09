# -*- coding: utf-8 -*-
"""Subir a mano el certificado (PDF) de un asegurado desde su ficha."""
import base64

from odoo import _, fields, models
from odoo.exceptions import UserError


class RssInsuredCertificateUpload(models.Model):
    _inherit = 'rss.insured'

    certificate_upload = fields.Binary('Subir certificado (PDF)', compute='_compute_certificate_upload',
                                       inverse='_inverse_certificate_upload',
                                       help='Sube el PDF del certificado de esta persona; se anexa y se leen sus datos.')
    certificate_upload_name = fields.Char('Nombre del archivo', compute='_compute_certificate_upload',
                                          inverse='_inverse_certificate_upload')

    def _compute_certificate_upload(self):
        for rec in self:
            rec.certificate_upload = False
            rec.certificate_upload_name = False

    def _inverse_certificate_upload(self):
        for rec in self.filtered('certificate_upload'):
            data = base64.b64decode(rec.certificate_upload)
            if not data.startswith(b'%PDF'):
                raise UserError(_('El certificado debe ser un archivo PDF.'))
            name = rec.certificate_upload_name or 'Certificado_%s.pdf' % (rec.name or rec.id)
            if not name.lower().endswith('.pdf'):
                name += '.pdf'
            attachment = self.env['ir.attachment'].sudo().create({
                'name': name, 'raw': data, 'mimetype': 'application/pdf',
                'res_model': rec._name, 'res_id': rec.id,
            })
            rec.sudo().write({'certificate_attachment_id': attachment.id,
                              'certificate_date': fields.Date.context_today(rec)})
            rec.message_post(body=_('Certificado subido a mano: %s', name), attachment_ids=attachment.ids)
            try:
                with self.env.cr.savepoint():
                    rec.action_reread_certificate()
            except UserError:
                rec.message_post(body=_('El certificado se anexó, pero no se pudieron leer sus datos '
                                        '(póliza, vigencia, coberturas). Revísalos a mano.'))
