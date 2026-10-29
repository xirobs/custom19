# -*- coding: utf-8 -*-
from odoo import api, fields, models, _

from .rss_common import name_key, normalize_text


class RssInsuredExt(models.Model):
    _inherit = 'rss.insured'

    capture_source = fields.Selection([
        ('backend', 'Oficina'),
        ('portal', 'Portal del grupo'),
        ('excel', 'Excel'),
        ('pdf', 'PDF'),
    ], string='Origen de la captura', default='backend', readonly=True)
    captured_by_id = fields.Many2one('res.users', string='Capturado por', readonly=True,
                                     default=lambda self: self.env.user)
    import_batch_id = fields.Many2one('rss.import.batch', string='Importación', readonly=True,
                                      ondelete='set null')
    duplicate_key = fields.Char('Llave de duplicado', compute='_compute_duplicate_key', store=True,
                                index=True)
    # Certificado de la póliza
    certificate_number = fields.Char('Folio / certificado', tracking=True)
    certificate_attachment_id = fields.Many2one('ir.attachment', string='Certificado (PDF)',
                                                ondelete='set null', tracking=True)
    certificate_date = fields.Date('Certificado recibido el', tracking=True)
    has_certificate = fields.Boolean('Con certificado', compute='_compute_has_certificate', store=True)

    @api.depends('first_name', 'second_name', 'last_name_father', 'last_name_mother', 'birthdate')
    def _compute_duplicate_key(self):
        for rec in self:
            rec.duplicate_key = name_key(rec.first_name, rec.second_name, rec.last_name_father,
                                         rec.last_name_mother, rec.birthdate)

    @api.depends('certificate_attachment_id')
    def _compute_has_certificate(self):
        for rec in self:
            rec.has_certificate = bool(rec.certificate_attachment_id)

    @api.model
    def _find_duplicates(self, first_name, second_name, last_name_father, last_name_mother,
                         birthdate, company=None):
        """Asegurados (de cualquier grupo) con el mismo nombre completo y fecha de nacimiento."""
        key = name_key(first_name, second_name, last_name_father, last_name_mother, birthdate)
        domain = [('duplicate_key', '=', key)]
        if company:
            domain.append(('company_id', '=', company.id))
        return self.sudo().with_context(active_test=False).search(domain)

    def _name_variants(self):
        """Formas en que el nombre puede aparecer en un certificado (normalizadas)."""
        self.ensure_one()
        names = normalize_text(' '.join(p for p in (self.first_name, self.second_name) if p))
        first = normalize_text(self.first_name)
        surnames = normalize_text(' '.join(p for p in (self.last_name_father, self.last_name_mother) if p))
        variants = {
            '%s %s' % (names, surnames),
            '%s %s' % (surnames, names),
        }
        if first != names:
            variants |= {'%s %s' % (first, surnames), '%s %s' % (surnames, first)}
        return {v.strip() for v in variants if v.strip() and len(v.split()) >= 2}

    def action_download_certificate(self):
        self.ensure_one()
        if not self.certificate_attachment_id:
            return False
        return {'type': 'ir.actions.act_url', 'target': 'self',
                'url': '/web/content/%s?download=true' % self.certificate_attachment_id.id}
