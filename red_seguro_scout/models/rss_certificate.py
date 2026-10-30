# -*- coding: utf-8 -*-
"""Certificados de la póliza: se sube el PDF que manda la aseguradora (todos los
certificados en un solo archivo), se identifica a quién pertenece cada página por el
nombre que aparece en ella y se anexa a cada asegurado SOLO su certificado."""
import io
import logging
import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .rss_common import normalize_text

_logger = logging.getLogger(__name__)

FOLIO_LABEL_RE = re.compile(r'CERTIFICADO|FOLIO|CONSECUTIVO')
FOLIO_CODE_RE = re.compile(r'(?<![A-Z0-9])([A-Z]{0,6}[-/]?\d[A-Z0-9\-/]{2,24})')
NAME_LABEL_RE = re.compile(
    r'(?:NOMBRE DEL ASEGURADO|ASEGURADO TITULAR|NOMBRE DEL TITULAR|ASEGURADO|NOMBRE)\s*[:.]?\s*'
    r'([A-Z ]{6,70})')
DATE_RE = re.compile(r'\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b')
FOLIO_STOPWORDS = {'DE', 'DEL', 'NO', 'NUM', 'NUMERO', 'INDIVIDUAL', 'POLIZA', 'ASEGURADO'}


def _pdf_reader(data):
    from odoo.tools.pdf import PdfFileReader
    return PdfFileReader(io.BytesIO(data), strict=False)


class RssCertificateBatch(models.Model):
    _name = 'rss.certificate.batch'
    _description = 'Carga de certificados de póliza'
    _inherit = ['mail.thread']
    _order = 'create_date desc, id desc'

    name = fields.Char('Carga', required=True, default=lambda self: _('Certificados %s') %
                       fields.Date.context_today(self).strftime('%d/%m/%Y'))
    company_id = fields.Many2one('res.company', string='Organización', required=True,
                                 default=lambda self: self.env.company)
    policy_id = fields.Many2one('rss.policy', string='Póliza')
    group_ids = fields.Many2many('rss.scout.group', string='Buscar solo en estos grupos',
                                 help='Vacío: busca en todos los asegurados de la organización.')
    file_ids = fields.Many2many('ir.attachment', 'rss_certificate_batch_file_rel', 'batch_id',
                                'attachment_id', string='Archivos PDF de la aseguradora')
    line_ids = fields.One2many('rss.certificate.line', 'batch_id', string='Certificados encontrados')
    state = fields.Selection([
        ('draft', 'Por leer'),
        ('review', 'En revisión'),
        ('done', 'Anexados'),
    ], string='Estatus', default='draft', required=True, tracking=True)
    matched_count = fields.Integer('Identificados', compute='_compute_counts')
    pending_count = fields.Integer('Por asignar', compute='_compute_counts')
    attached_count = fields.Integer('Anexados', compute='_compute_counts')
    page_count = fields.Integer('Páginas leídas', readonly=True)

    @api.depends('line_ids.insured_id', 'line_ids.attached')
    def _compute_counts(self):
        for batch in self:
            batch.matched_count = len(batch.line_ids.filtered('insured_id'))
            batch.pending_count = len(batch.line_ids.filtered(lambda l: not l.insured_id and not l.skip))
            batch.attached_count = len(batch.line_ids.filtered('attached'))

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------
    def _candidate_insured(self):
        self.ensure_one()
        domain = [('company_id', '=', self.company_id.id), ('state', 'in', ('draft', 'active', 'inactive'))]
        if self.group_ids:
            domain.append(('group_id', 'in', self.group_ids.ids))
        return self.env['rss.insured'].sudo().with_context(active_test=False).search(domain)

    def _build_index(self):
        """variante normalizada del nombre -> asegurados."""
        index = {}
        for insured in self._candidate_insured():
            for variant in insured._name_variants():
                index.setdefault(variant, set()).add(insured.id)
        return index

    @staticmethod
    def _match_page(text_norm, raw_upper, index, Insured):
        """Asegurado de la página: la variante de nombre más larga que aparece en el texto.
        Si hay varios con el mismo nombre, desempata la fecha de nacimiento."""
        padded = ' %s ' % text_norm
        best_len, best = 0, set()
        for variant, ids in index.items():
            if len(variant) < best_len:
                continue
            if ' %s ' % variant in padded:
                if len(variant) > best_len:
                    best_len, best = len(variant), set(ids)
                else:
                    best |= ids
        if len(best) <= 1:
            return Insured.browse(list(best)), Insured.browse()
        candidates = Insured.browse(list(best))
        dates = set()
        for d, m, y in DATE_RE.findall(raw_upper):
            try:
                dates.add('%04d-%02d-%02d' % (int(y), int(m), int(d)))
            except ValueError:
                continue
        by_date = candidates.filtered(lambda i: i.birthdate and i.birthdate.isoformat() in dates)
        if len(by_date) == 1:
            return by_date, Insured.browse()
        return Insured.browse(), candidates

    @staticmethod
    def _folio(raw_upper):
        """Primer código con número que aparece poco después de «Certificado/Folio»."""
        for label in FOLIO_LABEL_RE.finditer(raw_upper):
            window = raw_upper[label.end():label.end() + 45]
            code = FOLIO_CODE_RE.search(window)
            if code:
                value = code.group(1).strip('-/')
                if not DATE_RE.fullmatch(value) and len(value) >= 3:
                    return value
        return False

    @staticmethod
    def _label_name(raw_upper):
        """Nombre que aparece junto a la etiqueta «Asegurado/Nombre», solo en ese renglón."""
        for line in raw_upper.splitlines():
            match = NAME_LABEL_RE.search(normalize_text(line))
            if match:
                words = [w for w in match.group(1).split() if not w.isdigit()]
                if len(words) >= 2:
                    return ' '.join(words[:6]).title()
        return False

    def action_read(self):
        Line = self.env['rss.certificate.line']
        Insured = self.env['rss.insured'].sudo()
        for batch in self:
            pdfs = batch.file_ids.filtered(
                lambda a: a.mimetype == 'application/pdf' or (a.name or '').lower().endswith('.pdf'))
            if not pdfs:
                raise UserError(_('Sube el PDF de certificados que mandó la aseguradora.'))
            batch.line_ids.filtered(lambda l: not l.attached).unlink()
            index = batch._build_index()
            if not index:
                raise UserError(_('No hay asegurados registrados donde buscar los nombres.'))
            pages_read = 0
            for pdf in pdfs:
                try:
                    reader = _pdf_reader(pdf.raw)
                except Exception:  # noqa: BLE001
                    raise UserError(_('No se pudo abrir %s; revisa que sea un PDF válido.', pdf.name))
                current = None
                for number, page in enumerate(reader.pages, start=1):
                    pages_read += 1
                    raw = (page.extract_text() or '').upper()
                    text_norm = normalize_text(raw)
                    found, candidates = self._match_page(text_norm, raw, index, Insured)
                    folio = batch._folio(raw)
                    label = batch._label_name(raw)
                    same_folio = bool(current and folio and folio == current.certificate_number)
                    if current is not None and not (found or candidates) and (
                            not text_norm or same_folio or not (folio or label)):
                        # Página sin otro asegurado (reverso, condiciones, escaneo): continúa el anterior.
                        # Una página con folio o etiqueta de asegurado distinta NUNCA se pega a otro.
                        current.page_to = number
                        continue
                    if found and current and current.insured_id == found and current.attachment_id == pdf:
                        current.page_to = number
                        continue
                    current = Line.create({
                        'batch_id': batch.id,
                        'attachment_id': pdf.id,
                        'page_from': number,
                        'page_to': number,
                        'insured_id': found.id or False,
                        'candidate_ids': [fields.Command.set(candidates.ids)],
                        'certificate_number': folio,
                        'name_read': label or (found.name if found else False),
                        'no_text': not text_norm,
                    })
            batch.write({'state': 'review', 'page_count': pages_read})
            batch.message_post(body=_('%(pages)s páginas leídas: %(ok)s certificados identificados y '
                                      '%(pending)s por asignar.', pages=pages_read,
                                      ok=batch.matched_count, pending=batch.pending_count))
        return True

    def action_attach(self):
        """Separa el PDF y anexa a cada asegurado únicamente sus páginas."""
        from odoo.tools.pdf import PdfFileWriter
        Attachment = self.env['ir.attachment'].sudo()
        today = fields.Date.context_today(self)
        for batch in self:
            lines = batch.line_ids.filtered(lambda l: l.insured_id and not l.attached and not l.skip)
            if not lines:
                raise UserError(_('No hay certificados identificados por anexar.'))
            dup = lines.insured_id.filtered(lambda i: len(lines.filtered(lambda l: l.insured_id == i)) > 1)
            if dup:
                raise UserError(_('%s tiene más de un certificado en esta carga; deja solo uno '
                                  '(marca «Omitir» en el otro).', ', '.join(dup.mapped('name'))))
            readers = {}
            for line in lines:
                pdf = line.attachment_id
                if pdf.id not in readers:
                    readers[pdf.id] = _pdf_reader(pdf.raw)
                reader = readers[pdf.id]
                writer = PdfFileWriter()
                for index in range(line.page_from - 1, line.page_to):
                    writer.add_page(reader.pages[index])
                out = io.BytesIO()
                writer.write(out)
                insured = line.insured_id.sudo()
                filename = 'Certificado_%s%s.pdf' % (
                    re.sub(r'[^A-Za-z0-9]+', '_', normalize_text(insured.name)).strip('_'),
                    ('_' + re.sub(r'[^A-Za-z0-9]+', '', line.certificate_number))
                    if line.certificate_number else '')
                attachment = Attachment.create({
                    'name': filename,
                    'raw': out.getvalue(),
                    'mimetype': 'application/pdf',
                    'res_model': 'rss.insured',
                    'res_id': insured.id,
                })
                insured.write({
                    'certificate_attachment_id': attachment.id,
                    'certificate_number': line.certificate_number or insured.certificate_number,
                    'certificate_date': today,
                })
                insured.message_post(body=_('Certificado de la póliza anexado (%s).', batch.name),
                                     attachment_ids=attachment.ids)
                line.write({'attached': True, 'result_attachment_id': attachment.id})
            batch.state = 'done' if not batch.pending_count else 'review'
            batch.message_post(body=_('%s certificados anexados a sus asegurados.', len(lines)))
        return True


class RssCertificateLine(models.Model):
    _name = 'rss.certificate.line'
    _description = 'Certificado encontrado en el PDF'
    _order = 'batch_id, attachment_id, page_from'

    batch_id = fields.Many2one('rss.certificate.batch', required=True, ondelete='cascade', index=True)
    attachment_id = fields.Many2one('ir.attachment', string='Archivo', required=True, ondelete='cascade')
    page_from = fields.Integer('Página desde')
    page_to = fields.Integer('Página hasta')
    pages = fields.Char('Páginas', compute='_compute_pages')
    name_read = fields.Char('Nombre leído')
    certificate_number = fields.Char('Folio')
    insured_id = fields.Many2one('rss.insured', string='Asegurado', ondelete='set null')
    group_id = fields.Many2one(related='insured_id.group_id', string='Grupo')
    candidate_ids = fields.Many2many('rss.insured', 'rss_certificate_line_candidate_rel', 'line_id',
                                     'insured_id', string='Posibles (mismo nombre)')
    status = fields.Selection([
        ('matched', 'Identificado'),
        ('ambiguous', 'Varios posibles'),
        ('unmatched', 'Sin identificar'),
    ], string='Resultado', compute='_compute_status', store=True)
    no_text = fields.Boolean('Página sin texto (escaneo)')
    skip = fields.Boolean('Omitir')
    attached = fields.Boolean('Anexado', readonly=True)
    result_attachment_id = fields.Many2one('ir.attachment', string='Certificado anexado', readonly=True)

    @api.depends('page_from', 'page_to')
    def _compute_pages(self):
        for line in self:
            line.pages = (str(line.page_from) if line.page_from == line.page_to
                          else '%s–%s' % (line.page_from, line.page_to))

    @api.depends('insured_id', 'candidate_ids')
    def _compute_status(self):
        for line in self:
            if line.insured_id:
                line.status = 'matched'
            elif line.candidate_ids:
                line.status = 'ambiguous'
            else:
                line.status = 'unmatched'

    def action_preview(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'target': 'new',
                'url': '/web/content/%s#page=%s' % (self.attachment_id.id, self.page_from)}


class RssCertificateBatchPortal(models.Model):
    _inherit = 'rss.certificate.batch'

    source = fields.Selection([('office', 'Oficina'), ('group', 'Portal del grupo'), ('advisor', 'Portal del asesor')],
                              string='Cargado desde', default='office', readonly=True)
    member_id = fields.Many2one('rss.group.member', string='Subió (representante)', readonly=True,
                                ondelete='set null')
    advisor_id = fields.Many2one('rss.advisor', string='Subió (asesor)', readonly=True, ondelete='set null')

    @api.model
    def create_from_portal(self, groups, files, member=None, advisor=None):
        """Crea la carga desde un portal, la lee y la deja en revisión.

        ``groups``: grupos a los que puede pertenecer cada certificado (los del usuario).
        ``files``: lista de (nombre, bytes).
        """
        if not groups:
            raise UserError(_('No tienes grupos asignados.'))
        Attachment = self.env['ir.attachment'].sudo()
        attachments = Attachment.browse()
        for name, data in files:
            if not (data or b'').startswith(b'%PDF'):
                raise UserError(_('«%s» no es un PDF. Sube el PDF de certificados que envió la aseguradora.', name))
            attachments |= Attachment.create({'name': name, 'raw': data, 'mimetype': 'application/pdf',
                                              'res_model': self._name})
        if not attachments:
            raise UserError(_('Elige el PDF de certificados.'))
        label = groups[0].name if len(groups) == 1 else _('%s grupos', len(groups))
        batch = self.sudo().create({
            'name': _('Certificados %(who)s · %(date)s', who=label,
                      date=fields.Date.context_today(self).strftime('%d/%m/%Y')),
            'company_id': groups[0].company_id.id,
            'group_ids': [fields.Command.set(groups.ids)],
            'file_ids': [fields.Command.set(attachments.ids)],
            'source': 'advisor' if advisor else 'group',
            'member_id': member.id if member else False,
            'advisor_id': advisor.id if advisor else False,
        })
        attachments.write({'res_id': batch.id})
        batch.action_read()
        return batch

    def _portal_allowed_insured(self):
        """Asegurados a los que puede asignarse un certificado de esta carga."""
        self.ensure_one()
        return self._candidate_insured()

    def portal_apply(self, assignments, skips):
        """Aplica lo que el usuario eligió en la revisión del portal.

        ``assignments``: {línea_id: asegurado_id o 0}; ``skips``: ids de líneas a omitir.
        Solo se aceptan asegurados de los grupos de la carga.
        """
        self.ensure_one()
        allowed = self._portal_allowed_insured()
        for line in self.line_ids.filtered(lambda l: not l.attached):
            vals = {'skip': line.id in skips}
            if line.id in assignments:
                insured = self.env['rss.insured'].sudo().browse(assignments[line.id]).exists()
                if insured and insured not in allowed:
                    raise UserError(_('Ese asegurado no pertenece a tus grupos.'))
                vals['insured_id'] = insured.id or False
            line.write(vals)
        return True
