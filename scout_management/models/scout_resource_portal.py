# -*- coding: utf-8 -*-
"""Los dirigentes suben material desde el portal (manuales, imágenes, guías, diagramas, ligas de video) y lo
clasifican por tipo de actividad (relevos, técnica, trabajo en equipo, nudos...), para su sección, otras secciones
o todas."""
import base64
import re
import unicodedata

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

MAX_UPLOAD = 60 * 1024 * 1024
URL_RE = re.compile(r'^https?://\S+$', re.I)


class ScoutActivityType(models.Model):
    _name = 'scout.activity.type'
    _description = 'Tipo de actividad (para clasificar material)'
    _order = 'sequence, name'

    name = fields.Char(string='Tipo de actividad', required=True, translate=True)
    sequence = fields.Integer(default=10)
    icon = fields.Char(string='Ícono', default='fa-tag')
    active = fields.Boolean(default=True)

    _name_uniq = models.Constraint('UNIQUE (name)', 'Ese tipo de actividad ya existe.')


class ScoutResourceActivity(models.Model):
    _inherit = 'scout.resource'

    activity_type_ids = fields.Many2many('scout.activity.type', 'scout_resource_activity_type_rel', 'resource_id',
                                         'type_id', string='Tipo de actividad',
                                         help='Relevos, técnica, trabajo en equipo, nudos... para encontrarlo rápido.')
    for_specialties = fields.Boolean(string='Manual de especialidades',
                                     help='Material del Manual de Especialidades (se abre a los scouts desde el nivel '
                                          'en que empiezan las especialidades).')
    uploaded_by_id = fields.Many2one('res.users', string='Subido por', default=lambda self: self.env.user,
                                     readonly=True, copy=False)
    uploaded_section_id = fields.Many2one('scout.section', string='Sección que lo subió', readonly=True, copy=False)

    @staticmethod
    def _scout_norm(text):
        text = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()
        words = [w for w in re.split(r'[^a-z0-9]+', text) if w and w not in ('manual', 'de', 'del', 'la', 'el', 'scout', 'scouts')]
        return ' '.join(w[:-1] if len(w) > 4 and w.endswith('s') else w for w in words)

    def _scout_autoplace(self):
        """Si el material no tiene nivel pero su categoría se llama como un nivel de la ruta ("Tercera Clase",
        "Manual Cordón Scouts", "Manual de Especialidades"...), se ubica solo en ese nivel."""
        portal = self.env['scout.leader.portal'].sudo()
        all_sections = self.env['scout.section'].sudo().search([('is_leadership', '=', False)])
        for res in self.sudo():
            if res.stage_id or res.for_specialties or not res.category_id:
                continue
            cat = self._scout_norm(res.category_id.name)
            if not cat:
                continue
            if 'especialidad' in cat:
                res.with_context(scout_autoplace=True).for_specialties = True
                continue
            sections = all_sections if res.all_sections else res.section_ids
            for section in sections:
                stage = next((st for st in portal.path_stages(section)
                              if cat in {self._scout_norm(portal.path_label(st, section)),
                                         self._scout_norm(portal.stage_label(st, section)),
                                         self._scout_norm(st.name)}), None)
                if stage:
                    res.with_context(scout_autoplace=True).stage_id = stage
                    break

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._scout_autoplace()
        return records

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('scout_autoplace') and {'category_id', 'stage_id', 'for_specialties',
                                                             'section_ids', 'all_sections'} & set(vals):
            self._scout_autoplace()
        return res

    def _scout_can_edit(self, user=None):
        self.ensure_one()
        user = user or self.env.user
        return self.uploaded_by_id == user or user.has_group('scout_management.group_scout_officer') \
            or self.env['scout.leader.portal'].sudo()._is_group_manager()


class ScoutLeaderPortalResources(models.AbstractModel):
    _inherit = 'scout.leader.portal'

    @api.model
    def resource_options(self):
        sections = self.env['scout.section'].sudo().search([('is_leadership', '=', False)])
        return {
            'categories': self.env['scout.resource.category'].sudo().search([]),
            'activity_types': self.env['scout.activity.type'].sudo().search([]),
            'sections': sections,
        }

    @api.model
    def save_resource(self, section, post, files, resource=None):
        """Crea o edita un recurso desde el portal de dirigentes."""
        Resource = self.env['scout.resource'].sudo()
        if resource and not resource._scout_can_edit():
            raise AccessError(_('Solo quien lo subió (o la jefatura de grupo) puede editar este material.'))
        name = (post.get('name') or '').strip()
        if not name:
            raise UserError(_('Escribe el título del material.'))
        # categoría: existente o nueva
        category = self.env['scout.resource.category'].sudo()
        new_cat = (post.get('new_category') or '').strip()
        if new_cat:
            category = category.search([('name', '=ilike', new_cat)], limit=1) or category.create({'name': new_cat})
        elif str(post.get('category_id') or '').isdigit():
            category = category.browse(int(post['category_id'])).exists()
        # tipos de actividad: marcados + nuevos (separados por coma)
        Types = self.env['scout.activity.type'].sudo()
        types = Types.browse([int(x) for x in post.getlist('activity_type_ids') if str(x).isdigit()]
                             if hasattr(post, 'getlist') else []).exists()
        for raw in (post.get('new_types') or '').split(','):
            label = raw.strip()
            if label:
                types |= Types.with_context(active_test=False).search([('name', '=ilike', label)], limit=1) \
                    or Types.create({'name': label[:60]})
        # para quién
        audience = post.get('audience') or 'mine'
        all_sections = audience == 'all'
        if audience == 'some':
            ids = [int(x) for x in (post.getlist('section_ids') if hasattr(post, 'getlist') else []) if str(x).isdigit()]
            sections = self.env['scout.section'].sudo().browse(ids).exists().filtered(lambda s: not s.is_leadership)
            if not sections:
                raise UserError(_('Elige al menos una sección.'))
        elif all_sections:
            sections = self.env['scout.section'].sudo()
        else:
            sections = section if not section.is_leadership else self.env['scout.section'].sudo()
            all_sections = section.is_leadership
        url = (post.get('url') or '').strip()
        if url and not URL_RE.match(url):
            raise UserError(_('La liga debe empezar con http:// o https://'))
        vals = {
            'name': name[:200],
            'description': (post.get('description') or '').strip() or False,
            'category_id': category.id or False,
            'activity_type_ids': [fields.Command.set(types.ids)],
            'all_sections': all_sections,
            'section_ids': [fields.Command.set(sections.ids)],
            'for_scouts': bool(post.get('for_scouts')),
            'url': url or False,
        }
        level = post.get('stage_id') or ''
        vals['stage_id'] = int(level) if str(level).isdigit() else False
        vals['for_specialties'] = level == 'especialidades'
        upload = files.get('file') if files else None
        if upload and upload.filename:
            data = upload.read()
            if len(data) > MAX_UPLOAD:
                raise UserError(_('El archivo pesa más de 60 MB. Para videos largos sube el video a YouTube (oculto) '
                                  'y pega aquí la liga.'))
            vals.update(file=base64.b64encode(data), file_name=upload.filename)
        elif post.get('remove_file') and resource:
            vals.update(file=False, file_name=False)
        if resource:
            resource.write(vals)
            resource.message_post(body=_('Editado desde el portal por %s.', self.env.user.name))
            return resource
        vals.update(uploaded_by_id=self.env.user.id, uploaded_section_id=section.id,
                    company_id=section.company_id.id or self.env.company.id)
        resource = Resource.create(vals)
        resource.message_post(body=_('Subido desde el portal por %(user)s (%(section)s).', user=self.env.user.name,
                                     section=section.display_name))
        return resource

    @api.model
    def delete_resource(self, resource):
        if not resource._scout_can_edit():
            raise AccessError(_('Solo quien lo subió (o la jefatura de grupo) puede borrar este material.'))
        resource.sudo().active = False
