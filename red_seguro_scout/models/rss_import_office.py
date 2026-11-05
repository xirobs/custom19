# -*- coding: utf-8 -*-
"""Carga de listas (Excel o PDF) por la plataforma o el asesor.

A diferencia de la carga del grupo, no pasa por el cobro: las personas quedan en
preinscripción, igual que al darlas de alta una por una en la plataforma. Si el archivo trae
una columna «Grupo» (número o nombre), cada persona se acomoda en su grupo y se crea una carga
por grupo.
"""
import re
from collections import OrderedDict

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .rss_common import normalize_text
from .rss_parsers import parse_excel, parse_pdf

MAX_OFFICE_ROWS = 3000


class RssImportBatchOffice(models.Model):
    _inherit = 'rss.import.batch'

    office_load = fields.Boolean(
        'Carga de la plataforma / asesor', readonly=True,
        help='Cargada por la administración o un asesor: no se cobra en el portal y las personas '
             'quedan en preinscripción.')
    advisor_id = fields.Many2one('rss.advisor', string='Asesor', ondelete='set null', readonly=True)

    @api.model
    def _match_group(self, value, groups):
        value = (value or '').strip()
        if not value:
            return self.env['rss.scout.group']
        digits = re.findall(r'\d+', value)
        if digits:
            by_number = groups.filtered(lambda g: g.number == int(digits[0]))
            if len(by_number) == 1:
                return by_number
        key = normalize_text(value)
        by_name = groups.filtered(lambda g: key and (key in normalize_text(g.name) or normalize_text(g.name) in key))
        return by_name if len(by_name) == 1 else self.env['rss.scout.group']

    @api.model
    def create_office_batches(self, groups, filename, data, default_group=None, advisor=None):
        """Lee el archivo y crea una carga por grupo para revisar. Devuelve (cargas, filas sin grupo)."""
        if not data:
            raise UserError(_('Elige un archivo Excel (.xlsx) o PDF.'))
        source = self._detect_source(filename, data)
        rows, error = (parse_pdf if source == 'pdf' else parse_excel)(data)
        if error:
            raise UserError(error)
        if not rows:
            raise UserError(_('No encontré personas en el archivo.'))
        if len(rows) > MAX_OFFICE_ROWS:
            raise UserError(_('El archivo tiene %(n)s personas; el máximo es %(max)s. Divídelo en partes.',
                              n=len(rows), max=MAX_OFFICE_ROWS))
        buckets, unmatched = OrderedDict(), []
        if default_group:
            buckets[default_group] = rows
        else:
            for row in rows:
                group = self._match_group(row.get('group'), groups)
                if group:
                    buckets.setdefault(group, []).append(row)
                else:
                    unmatched.append(row)
            if not buckets:
                raise UserError(_('Elige el grupo, o agrega al archivo una columna «Grupo» con el número o nombre '
                                  'del grupo de cada persona.'))
        attachment = self.env['ir.attachment'].sudo().create({'name': filename or 'lista', 'raw': data,
                                                              'res_model': self._name})
        batches = self.browse()
        for group, group_rows in buckets.items():
            batch = self.create({
                'group_id': group.id,
                'source': source,
                'filename': filename,
                'attachment_id': attachment.id,
                'privacy_accepted': True,
                'office_load': True,
                'advisor_id': advisor.id if advisor else False,
                'line_ids': [fields.Command.create(self.env['rss.import.line']._vals_from_row(r, i))
                             for i, r in enumerate(group_rows, start=1)],
            })
            batch.action_validate()
            batch.message_post(body=_('Lista cargada desde %(where)s por %(who)s: %(n)s personas.',
                                      where=_('el portal del asesor') if advisor else _('la plataforma'),
                                      who=advisor.name if advisor else self.env.user.name, n=len(group_rows)))
            batches |= batch
        attachment.res_id = batches[:1].id
        return batches, unmatched

    def action_save_draft(self):
        """Guarda en preinscripción a las personas válidas (sin cobro)."""
        today = fields.Date.context_today(self)
        Insured = self.env['rss.insured'].sudo()
        total = 0
        for batch in self:
            if not batch.office_load:
                raise UserError(_('Esta carga es de un grupo: se guarda al confirmarse su pago.'))
            if batch.state != 'review':
                continue
            batch.action_validate()
            lines = batch._ready_lines()
            # Plataforma -> «Plataforma»; asesor -> «Portal · Excel/PDF»
            source = {'pdf': 'pdf', 'excel': 'excel'}.get(batch.source, 'portal') if batch.advisor_id else 'backend'
            created = Insured.browse()
            for line in lines:
                created |= Insured.with_context(mail_create_nolog=True).create(
                    batch._line_insured_vals(line, source, today))
                line.insured_id = created[-1]
            batch.write({'state': 'done', 'created_count': len(created)})
            batch.message_post(body=_('%s personas guardadas en preinscripción.', len(created)))
            total += len(created)
        return total
