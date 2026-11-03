# -*- coding: utf-8 -*-
"""Datos que se leen de cada certificado y quedan en la ficha del asegurado:
póliza y certificado, vigencia, contratante y coberturas."""
import io
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .rss_cert_parser import parse_certificate

_logger = logging.getLogger(__name__)

CERT_FIELDS = (
    # campo en la línea leída -> campo en el asegurado
    ('policy_number', 'cert_policy_number'),
    ('insurer_name', 'cert_insurer'),
    ('product', 'cert_product'),
    ('date_start', 'cert_date_start'),
    ('date_end', 'cert_date_end'),
    ('policy_date_start', 'cert_policy_date_start'),
    ('policy_date_end', 'cert_policy_date_end'),
    ('contractor_name', 'contractor_name'),
    ('contractor_rfc', 'contractor_rfc'),
    ('contractor_address', 'contractor_address'),
    ('name_read', 'cert_insured_name'),
)


class RssInsuredCoverage(models.Model):
    _name = 'rss.insured.coverage'
    _description = 'Cobertura del certificado del asegurado'
    _order = 'insured_id, sequence, id'

    insured_id = fields.Many2one('rss.insured', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char('Cobertura', required=True)
    currency_id = fields.Many2one(related='insured_id.company_id.currency_id')
    sum_insured = fields.Monetary('Suma asegurada')
    sum_insured_text = fields.Char('Suma asegurada (como viene)')
    deductible = fields.Monetary('Deducible')
    deductible_text = fields.Char('Deducible (como viene)')
    waiting_period = fields.Char('Periodo de espera')


class RssInsuredCertificateData(models.Model):
    _inherit = 'rss.insured'

    cert_policy_number = fields.Char('Póliza No.', tracking=True)
    cert_insurer = fields.Char('Aseguradora (certificado)')
    cert_product = fields.Char('Producto')
    cert_date_start = fields.Date('Alta del certificado')
    cert_date_end = fields.Date('Fin del certificado')
    cert_policy_date_start = fields.Date('Vigencia de la póliza desde')
    cert_policy_date_end = fields.Date('Vigencia de la póliza hasta')
    cert_insured_name = fields.Char('Nombre en el certificado')
    contractor_name = fields.Char('Contratante (nombre o razón social)', tracking=True)
    contractor_rfc = fields.Char('RFC del contratante')
    contractor_address = fields.Char('Domicilio del contratante')
    coverage_ids = fields.One2many('rss.insured.coverage', 'insured_id', string='Coberturas')
    coverage_total = fields.Monetary('Suma asegurada total', compute='_compute_coverage_total',
                                     currency_field='coverage_currency_id')
    coverage_currency_id = fields.Many2one(related='company_id.currency_id')

    @api.depends('coverage_ids.sum_insured')
    def _compute_coverage_total(self):
        for rec in self:
            rec.coverage_total = sum(rec.coverage_ids.mapped('sum_insured'))

    def _apply_certificate_line(self, line):
        """Copia a la ficha lo leído en el certificado y reemplaza sus coberturas."""
        self.ensure_one()
        vals = {target: line[source] for source, target in CERT_FIELDS if line[source]}
        self._write_certificate_vals(vals, line.coverage_data or [])

    def action_reread_certificate(self):
        """Vuelve a leer el PDF de certificado ya anexado y actualiza los datos de la ficha
        (póliza, vigencia, contratante y coberturas). No cambia a quién pertenece."""
        from odoo.tools.pdf import PdfFileReader
        from .rss_certificate import _page_text, _line_data
        done = 0
        for rec in self.filtered('certificate_attachment_id'):
            try:
                reader = PdfFileReader(io.BytesIO(rec.certificate_attachment_id.sudo().raw), strict=False)
            except Exception:  # noqa: BLE001
                _logger.warning('No se pudo abrir el certificado de %s', rec.display_name)
                continue
            merged = {}
            for page in reader.pages:
                data = parse_certificate(_page_text(page))
                for key, value in data.items():
                    merged.setdefault(key, value)
            if not merged:
                continue
            line_vals = _line_data(merged)
            line_vals['name_read'] = merged.get('insured_name') or False
            vals = {target: line_vals.get(source) for source, target in CERT_FIELDS if line_vals.get(source)}
            if merged.get('certificate_number'):
                vals['certificate_number'] = merged['certificate_number']
            rec._write_certificate_vals(vals, merged.get('coverages') or [])
            done += 1
        if not done and len(self) == 1:
            raise UserError(_('No se pudieron leer datos del certificado anexado.'))
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'type': 'success', 'message': _('Datos del certificado actualizados en %s asegurados.', done),
                           'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    def _write_certificate_vals(self, vals, coverages):
        self.ensure_one()
        if coverages:
            vals['coverage_ids'] = [fields.Command.clear()] + [fields.Command.create({
                'sequence': 10 * (n + 1),
                'name': c.get('name'),
                'sum_insured': c.get('sum_insured') or 0.0,
                'sum_insured_text': c.get('sum_insured_text'),
                'deductible': c.get('deductible') or 0.0,
                'deductible_text': c.get('deductible_text'),
                'waiting_period': c.get('waiting_period'),
            }) for n, c in enumerate(coverages) if c.get('name')]
        if vals:
            self.write(vals)


class RssCertificateLineData(models.Model):
    _inherit = 'rss.certificate.line'

    policy_number = fields.Char('Póliza No.')
    insurer_name = fields.Char('Aseguradora')
    product = fields.Char('Producto')
    date_start = fields.Date('Alta del certificado')
    date_end = fields.Date('Fin del certificado')
    policy_date_start = fields.Date('Vigencia desde')
    policy_date_end = fields.Date('Vigencia hasta')
    contractor_name = fields.Char('Contratante')
    contractor_rfc = fields.Char('RFC contratante')
    contractor_address = fields.Char('Domicilio contratante')
    birthdate_read = fields.Date('Nacimiento leído')
    sex_read = fields.Selection([('male', 'Masculino'), ('female', 'Femenino')], string='Sexo leído')
    coverage_data = fields.Json('Coberturas leídas')
    coverage_summary = fields.Char('Coberturas', compute='_compute_coverage_summary')

    @api.depends('coverage_data')
    def _compute_coverage_summary(self):
        for line in self:
            rows = line.coverage_data or []
            line.coverage_summary = ' · '.join(
                '%s %s' % (r.get('name'), r.get('sum_insured_text') or '') for r in rows) or False
