# -*- coding: utf-8 -*-
"""Actualizar datos de asegurados que ya están en la plataforma con una lista (Excel/PDF):
tutor, teléfono de emergencia, correo, parentesco y CURP. No crea a nadie."""
from odoo import _, fields, models
from odoo.exceptions import UserError

from .rss_common import normalize_text

UPDATE_FIELDS = ('tutor_name', 'tutor_phone', 'tutor_email', 'tutor_relationship', 'curp')


class RssImportBatchUpdate(models.Model):
    _inherit = 'rss.import.batch'

    state = fields.Selection(selection_add=[('updated', 'Actualización aplicada')],
                             ondelete={'updated': 'set default'})
    update_done = fields.Boolean('Usada para actualizar datos', readonly=True, copy=False)
    updated_count = fields.Integer('Asegurados actualizados', readonly=True, copy=False)

    def _update_candidates(self, line):
        """Asegurado registrado que corresponde a la fila: primero exacto (nombre y fecha), luego
        aproximado dentro del grupo de la carga con la misma fecha de nacimiento."""
        if line.duplicate_insured_id:
            return line.duplicate_insured_id, 'exact'
        Insured = self.env['rss.insured'].sudo().with_context(active_test=False)
        domain = [('state', '!=', 'cancel')] if 'cancel' in dict(Insured._fields['state'].selection) else []
        if self.group_id:
            domain.append(('group_id', '=', self.group_id.id))
        if self.company_id:
            domain.append(('company_id', '=', self.company_id.id))
        pool = Insured.search(domain)
        if line.birthdate:
            same_day = pool.filtered(lambda i: i.birthdate == line.birthdate)
        else:
            same_day = Insured
        name = normalize_text(' '.join(p for p in (line.first_name, line.second_name, line.last_name_father,
                                                    line.last_name_mother) if p))
        if not name:
            return Insured, 'none'
        Cert = self.env['rss.certificate.batch']

        def fuzzy(records):
            index = []
            for ins in records:
                words = set()
                for value in (ins.first_name, ins.second_name, ins.last_name_father, ins.last_name_mother):
                    words |= {w for w in normalize_text(value or '').split() if len(w) > 1}
                if words:
                    index.append((ins.id, words))
            return Insured.browse(Cert._fuzzy_match(name, index))

        found = fuzzy(same_day) if same_day else Insured
        if len(found) == 1:
            return found, 'approx'
        # Sin fecha coincidente: solo si el nombre es inconfundible dentro del grupo
        if not found and self.group_id:
            found = fuzzy(pool)
            if len(found) == 1:
                return found, 'name_only'
        return Insured, 'ambiguous' if found else 'none'

    def action_update_existing(self):
        """Pasa a los asegurados ya registrados los datos de contacto de la lista."""
        not_found = []
        for batch in self:
            if batch.state != 'review':
                raise UserError(_('Solo se actualiza con una carga en revisión.'))
            updated = 0
            labels = {'exact': _('coincide nombre y fecha'), 'approx': _('nombre parecido, misma fecha'),
                      'name_only': _('mismo nombre en el grupo; la fecha no coincide, revísala')}
            for line in batch.line_ids.sorted('sequence'):
                insured, how = batch._update_candidates(line)
                if not insured:
                    reason = _('Varias personas posibles') if how == 'ambiguous' else _('No está en la plataforma')
                    line.write({'include': False, 'message': _('No se actualizó: %s.', reason)})
                    not_found.append(line)
                    continue
                vals = {}
                for field in UPDATE_FIELDS:
                    value = line[field]
                    if isinstance(value, str):
                        value = value.strip()
                        if field == 'curp':
                            value = value.upper()
                    if value and value != insured[field]:
                        vals[field] = value
                if line.photo and not insured.photo:
                    vals['photo'] = line.photo
                changes = ', '.join(insured._fields[f].string for f in vals) or _('sin cambios')
                if vals:
                    insured.sudo().write(vals)
                    insured.sudo().message_post(body=_('Datos actualizados con la lista %(file)s: %(what)s.',
                                                       file=batch.filename or batch.display_name, what=changes))
                    updated += 1
                line.write({'include': False, 'insured_id': insured.id,
                            'message': _('Actualizado %(name)s (%(how)s): %(what)s.',
                                         name=insured.name, how=labels[how], what=changes)})
            batch.write({'state': 'updated', 'update_done': True, 'updated_count': updated})
            batch.message_post(body=_('Lista usada para actualizar datos: %(n)s asegurados actualizados, '
                                      '%(m)s sin encontrar.', n=updated, m=len(not_found)))
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'success' if not not_found else 'warning', 'sticky': bool(not_found),
                       'message': _('%(n)s asegurados actualizados. %(m)s filas sin encontrar: revísalas en la carga.',
                                    n=updated, m=len(not_found)),
                       'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}},
        }
