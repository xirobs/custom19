# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class RssImportWizard(models.TransientModel):
    """Botón «Cargar lista (Excel / PDF)» de la plataforma."""
    _name = 'rss.import.wizard'
    _description = 'Cargar lista de asegurados (Excel o PDF)'

    group_id = fields.Many2one('rss.scout.group', string='Grupo',
                               help='Déjalo vacío si el archivo trae la columna «Grupo» (número o nombre).')
    file = fields.Binary('Archivo', required=True)
    filename = fields.Char()

    def action_load(self):
        self.ensure_one()
        if not self.file:
            raise UserError(_('Elige un archivo.'))
        import base64
        Batch = self.env['rss.import.batch']
        groups = self.env['rss.scout.group'].search([])
        batches, unmatched = Batch.create_office_batches(groups, self.filename, base64.b64decode(self.file),
                                                         default_group=self.group_id or None)
        if unmatched:
            names = ', '.join(' '.join(x for x in (r.get('first_name'), r.get('last_name_father')) if x)
                              for r in unmatched[:15])
            batches[:1].message_post(body=_('%(n)s personas sin grupo reconocido no se cargaron: %(names)s%(more)s',
                                            n=len(unmatched), names=names, more='…' if len(unmatched) > 15 else ''))
        action = {
            'type': 'ir.actions.act_window', 'res_model': 'rss.import.batch', 'name': _('Lista cargada'),
            'context': {'create': False},
        }
        if len(batches) == 1:
            action.update({'view_mode': 'form', 'res_id': batches.id, 'views': [(False, 'form')]})
        else:
            action.update({'view_mode': 'list,form', 'domain': [('id', 'in', batches.ids)],
                           'views': [(False, 'list'), (False, 'form')]})
        return action
