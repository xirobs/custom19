# -*- coding: utf-8 -*-
"""Certificados de personas que aún no están registradas: se dan de alta desde el certificado
(nombre y fecha de nacimiento leídos) en el grupo que se elija, y se les anexa su certificado."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


def split_certificate_name(name):
    """«LOPEZ MALAGON DANIELA PAULETTE» -> (paterno, materno, primer nombre, segundo nombre).
    Los certificados escriben primero los apellidos."""
    words = (name or '').split()
    if len(words) < 2:
        return False, False, (words[0] if words else False), False
    if len(words) == 2:
        return words[0], False, words[1], False
    father, mother, given = words[0], words[1], words[2:]
    return father, mother, given[0], ' '.join(given[1:]) or False


def _title(value):
    return ' '.join(w[:1].upper() + w[1:].lower() for w in value.split()) if value else value


class RssCertificateLineMissing(models.Model):
    _inherit = 'rss.certificate.line'

    new_group_id = fields.Many2one('rss.scout.group', string='Dar de alta en',
                                   help='Si la persona no está registrada, elige su grupo y usa «Dar de alta y anexar».')
    new_sex = fields.Selection([('female', 'Femenino'), ('male', 'Masculino')], string='Sexo (alta)',
                               compute='_compute_new_sex', store=True, readonly=False,
                               help='Sugerido por el nombre; corrígelo si hace falta.')

    @api.depends('name_read', 'sex_read')
    def _compute_new_sex(self):
        for line in self:
            if line.new_sex:
                line.new_sex = line.new_sex
            elif line.sex_read:
                line.new_sex = line.sex_read
            else:
                first = split_certificate_name(line.name_read)[2] or ''
                line.new_sex = 'female' if first[-1:].upper() in ('A', 'Y') else ('male' if first else False)


class RssCertificateBatchMissing(models.Model):
    _inherit = 'rss.certificate.batch'

    def action_create_missing(self):
        """Da de alta a las personas «Sin identificar» que tienen grupo elegido y anexa su certificado."""
        lines = self.line_ids.filtered(lambda l: not l.insured_id and not l.skip and not l.attached and l.new_group_id)
        if not lines:
            raise UserError(_('Elige el grupo en «Dar de alta en» de las personas sin identificar '
                              '(botón «Sin identificar» para elegirlo a varias a la vez).'))
        return lines.action_create_missing_lines()

    def action_open_unmatched(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Certificados sin identificar'),
            'res_model': 'rss.certificate.line', 'view_mode': 'list',
            'views': [(self.env.ref('red_seguro_scout.rss_certificate_line_view_unmatched').id, 'list')],
            'domain': [('batch_id', '=', self.id), ('insured_id', '=', False), ('skip', '=', False),
                       ('attached', '=', False)],
            'context': {'create': False},
        }


class RssCertificateLineCreate(models.Model):
    _inherit = 'rss.certificate.line'

    def action_create_missing_lines(self):
        """Alta desde el certificado de las líneas elegidas (con grupo) y anexo de su certificado."""
        Insured = self.env['rss.insured'].sudo()
        lines = self.filtered(lambda l: not l.insured_id and not l.skip and not l.attached and l.new_group_id)
        if not lines:
            raise UserError(_('Elige el grupo en «Dar de alta en» de las personas que quieres dar de alta.'))
        missing = lines.filtered(lambda l: not l.name_read or not l.birthdate_read or not l.new_sex)
        if missing:
            raise UserError(_('Faltan datos para dar de alta (nombre, fecha de nacimiento o sexo) en las '
                              'páginas %s.', ', '.join(missing.mapped('pages'))))
        for line in lines:
            father, mother, first, second = split_certificate_name(line.name_read)
            insured = Insured.create({
                'group_id': line.new_group_id.id,
                'first_name': _title(first),
                'second_name': _title(second),
                'last_name_father': _title(father),
                'last_name_mother': _title(mother),
                'birthdate': line.birthdate_read,
                'sex': line.new_sex,
                'date_start': line.date_start or False,
            })
            insured.action_activate()
            insured.message_post(body=_('Dado de alta desde el certificado %s (%s).',
                                        line.certificate_number or line.pages, line.batch_id.name))
            line.write({'insured_id': insured.id, 'candidate_ids': [(5,)]})
        for batch in lines.batch_id:
            batch.message_post(body=_('%s personas dadas de alta desde sus certificados.',
                                      len(lines.filtered(lambda l: l.batch_id == batch))))
            batch.action_attach()
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'success', 'sticky': False,
                       'message': _('%s personas dadas de alta y con su certificado anexado.', len(lines)),
                       'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}},
        }
