# -*- coding: utf-8 -*-
"""Biblioteca de recursos para dirigentes: manuales, PDF, videos, formatos y ligas de apoyo."""
import mimetypes
import re

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

YOUTUBE_RE = re.compile(r'(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/)|youtu\.be/)([\w-]{11})')
VIMEO_RE = re.compile(r'vimeo\.com/(?:video/)?(\d+)')
DRIVE_RE = re.compile(r'drive\.google\.com/file/d/([\w-]+)')

KINDS = [
    ('pdf', 'PDF'),
    ('video', 'Video'),
    ('document', 'Documento'),
    ('presentation', 'Presentación'),
    ('sheet', 'Hoja de cálculo'),
    ('image', 'Imagen'),
    ('audio', 'Audio'),
    ('link', 'Liga'),
    ('other', 'Archivo'),
]
KIND_ICONS = {
    'pdf': 'fa-file-pdf-o', 'video': 'fa-play-circle', 'document': 'fa-file-word-o',
    'presentation': 'fa-file-powerpoint-o', 'sheet': 'fa-file-excel-o', 'image': 'fa-file-image-o',
    'audio': 'fa-file-audio-o', 'link': 'fa-link', 'other': 'fa-file-o',
}
EXTENSIONS = {
    'pdf': ('pdf',),
    'video': ('mp4', 'mov', 'm4v', 'webm', 'avi', 'mkv'),
    'document': ('doc', 'docx', 'odt', 'rtf', 'txt'),
    'presentation': ('ppt', 'pptx', 'odp', 'key'),
    'sheet': ('xls', 'xlsx', 'ods', 'csv'),
    'image': ('png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'),
    'audio': ('mp3', 'm4a', 'wav', 'ogg'),
}


class ScoutResourceCategory(models.Model):
    _name = 'scout.resource.category'
    _description = 'Categoría de recursos para dirigentes'
    _order = 'sequence, name'

    name = fields.Char(string='Categoría', required=True, translate=True)
    sequence = fields.Integer(default=10)
    icon = fields.Char(string='Ícono', default='fa-folder-open',
                       help='Ícono de Font Awesome, por ejemplo fa-book, fa-video-camera, fa-music, fa-gamepad.')
    description = fields.Char(string='Descripción')
    resource_ids = fields.One2many('scout.resource', 'category_id', string='Recursos')
    resource_count = fields.Integer(compute='_compute_resource_count', string='Cantidad')
    active = fields.Boolean(default=True)

    def _compute_resource_count(self):
        data = dict(self.env['scout.resource']._read_group(
            [('category_id', 'in', self.ids)], ['category_id'], ['__count']))
        for category in self:
            category.resource_count = data.get(category, 0)


class ScoutResource(models.Model):
    _name = 'scout.resource'
    _description = 'Recurso para dirigentes'
    _inherit = ['mail.thread']
    _order = 'pinned desc, sequence, id desc'

    name = fields.Char(string='Título', required=True, tracking=True)
    description = fields.Text(string='Descripción', help='Para qué sirve y cuándo usarlo.')
    category_id = fields.Many2one('scout.resource.category', string='Categoría', tracking=True,
                                  default=lambda self: self.env['scout.resource.category'].search([], limit=1))
    file = fields.Binary(string='Archivo', attachment=True,
                         help='Sube un PDF, documento, presentación, imagen, audio o video corto.')
    file_name = fields.Char(string='Nombre del archivo')
    url = fields.Char(string='Liga', help='Video de YouTube o Vimeo, carpeta de Drive o cualquier página de apoyo.')
    kind = fields.Selection(KINDS, string='Tipo', compute='_compute_kind', store=True)
    section_ids = fields.Many2many('scout.section', 'scout_resource_section_rel', 'resource_id', 'section_id',
                                  string='Secciones', domain=[('is_leadership', '=', False)],
                                  help='Solo estas secciones lo ven (sus jefes y, si es del aula, sus scouts).')
    all_sections = fields.Boolean(string='Para todas las secciones',
                                  help='Material general del grupo: lo ven todas las secciones.')
    for_scouts = fields.Boolean(string='Visible para los scouts (aula virtual)',
                                help='Además de los jefes, lo ven los scouts y sus familias en el aula virtual del portal.')
    stage_id = fields.Many2one('scout.progression.stage', string='Adelanto de la ruta', index=True,
                               domain="[('stage_type', 'in', ('promise', 'stage', 'link', 'terminal', 'specialty'))]",
                               help='En el aula virtual se abre hasta que el scout llega a este adelanto '
                                    '(vacío: se ve desde el inicio).')
    view_ids = fields.One2many('scout.resource.view', 'resource_id', string='Vistas')
    view_count = fields.Integer(string='Scouts que lo vieron', compute='_compute_view_count')
    pinned = fields.Boolean(string='Destacado', help='Aparece primero en el portal de dirigentes.')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Grupo', default=lambda self: self.env.company)
    video_embed_url = fields.Char(compute='_compute_embed', string='Video incrustado')
    thumbnail_url = fields.Char(compute='_compute_embed', string='Miniatura')

    def _compute_view_count(self):
        data = dict(self.env['scout.resource.view'].sudo()._read_group(
            [('resource_id', 'in', self.ids)], ['resource_id'], ['__count']))
        for resource in self:
            resource.view_count = data.get(resource, 0)

    @api.constrains('all_sections', 'section_ids')
    def _check_audience(self):
        for resource in self:
            if not resource.all_sections and not resource.section_ids:
                raise ValidationError(_('Elige la sección de "%s" (o marca "Para todas las secciones").', resource.name))

    @api.constrains('file', 'url')
    def _check_content(self):
        for resource in self:
            if not resource.file and not resource.url:
                raise ValidationError(_('Sube un archivo o escribe una liga para "%s".', resource.name))

    @api.depends('file_name', 'url')
    def _compute_kind(self):
        for resource in self:
            kind = False
            if resource.file_name:
                ext = (resource.file_name.rsplit('.', 1)[-1] if '.' in resource.file_name else '').lower()
                kind = next((k for k, exts in EXTENSIONS.items() if ext in exts), 'other')
            elif resource.url:
                url = resource.url.strip().lower()
                if YOUTUBE_RE.search(resource.url) or VIMEO_RE.search(resource.url):
                    kind = 'video'
                elif url.split('?')[0].endswith('.pdf'):
                    kind = 'pdf'
                else:
                    kind = 'link'
            resource.kind = kind or 'other'

    @api.depends('url')
    def _compute_embed(self):
        for resource in self:
            embed = thumb = False
            url = (resource.url or '').strip()
            youtube = YOUTUBE_RE.search(url)
            vimeo = VIMEO_RE.search(url)
            if youtube:
                embed = 'https://www.youtube-nocookie.com/embed/%s' % youtube.group(1)
                thumb = 'https://i.ytimg.com/vi/%s/hqdefault.jpg' % youtube.group(1)
            elif vimeo:
                embed = 'https://player.vimeo.com/video/%s' % vimeo.group(1)
            resource.video_embed_url = embed
            resource.thumbnail_url = thumb

    @api.onchange('file_name')
    def _onchange_file_name(self):
        if self.file_name and not self.name:
            self.name = self.file_name.rsplit('.', 1)[0].replace('_', ' ').replace('-', ' ').strip()

    def _scout_icon(self):
        self.ensure_one()
        return KIND_ICONS.get(self.kind, 'fa-file-o')

    def _scout_kind_label(self):
        self.ensure_one()
        return dict(KINDS).get(self.kind, '')

    def _scout_mimetype(self):
        self.ensure_one()
        return mimetypes.guess_type(self.file_name or '')[0] or 'application/octet-stream'

    def _scout_inline(self):
        """Archivos que el navegador puede mostrar sin descargarlos."""
        self.ensure_one()
        return bool(self.file) and self.kind in ('pdf', 'image', 'video', 'audio')

    @api.model
    def _scout_for_section(self, section, scouts=False):
        """Recursos de la sección (los de todas las secciones incluidos). scouts=True: solo los del aula virtual."""
        domain = [
            '|', ('all_sections', '=', True), ('section_ids', 'in', section.ids),
            '|', ('company_id', '=', False), ('company_id', '=', section.company_id.id or self.env.company.id),
        ]
        if scouts:
            domain.append(('for_scouts', '=', True))
        return self.sudo().search(domain)

    # ------------------------------------------------------------------ aula virtual (scouts)
    @api.model
    def _scout_classroom(self, member):
        """Unidades del aula virtual del scout: una por adelanto de su ruta, abiertas hasta el que le toca."""
        member = member.sudo()
        section = member.scout_section_id
        Stage = self.env['scout.progression.stage'].sudo()
        if not section:
            return {'units': [], 'total': 0, 'seen': 0, 'pct': 0, 'current': Stage}
        portal = self.env['scout.leader.portal'].sudo()
        path = portal.path_stages(section)
        resources = self._scout_for_section(section, scouts=True)
        progress = self.env['scout.member.progress'].sudo().search([('member_id', '=', member.id)])
        achieved = progress.filtered(lambda p: p.state == 'achieved').stage_id
        seen = set(self.env['scout.resource.view'].sudo().search(
            [('member_id', '=', member.id), ('resource_id', 'in', resources.ids)]).resource_id.ids)
        current_index = next((i for i, s in enumerate(path) if s not in achieved), len(path))

        def items(recs):
            return [{'resource': r, 'seen': r.id in seen} for r in recs.sorted(lambda r: (r.sequence, r.id))]

        units = []
        intro = resources.filtered(lambda r: not r.stage_id)
        if intro:
            units.append({'key': 'intro', 'name': _('Bienvenida'), 'stage': Stage, 'state': 'open',
                          'items': items(intro), 'icon': 'fa-flag'})
        for index, stage in enumerate(path):
            state = 'done' if stage in achieved else ('current' if index == current_index else
                                                     ('open' if index < current_index else 'locked'))
            units.append({'key': stage.id, 'name': portal.stage_label(stage, section), 'stage': stage, 'state': state,
                          'items': items(resources.filtered(lambda r, s=stage: r.stage_id == s)),
                          'icon': 'fa-star', 'locked_by': path[index - 1] if state == 'locked' and index else Stage})
        extra = resources.filtered(lambda r: r.stage_id and r.stage_id not in path)
        if extra:
            units.append({'key': 'extra', 'name': _('Especialidades y retos'), 'stage': Stage, 'state': 'open',
                          'items': items(extra), 'icon': 'fa-trophy'})
        unlocked = [i for u in units if u['state'] != 'locked' for i in u['items']]
        total, done = len(unlocked), len([i for i in unlocked if i['seen']])
        return {'units': units, 'total': total, 'seen': done, 'pct': round(done * 100.0 / total) if total else 0,
                'current': path[current_index] if current_index < len(path) else Stage,
                'path_done': len(achieved & path), 'path_total': len(path)}

    def _scout_unlocked_for(self, member):
        """¿El scout ya puede abrir este recurso?"""
        self.ensure_one()
        data = self._scout_classroom(member)
        return any(i['resource'] == self for u in data['units'] if u['state'] != 'locked' for i in u['items'])

    def _scout_mark_seen(self, member):
        self.ensure_one()
        View = self.env['scout.resource.view'].sudo()
        if not View.search_count([('resource_id', '=', self.id), ('member_id', '=', member.id)]):
            View.create({'resource_id': self.id, 'member_id': member.id})


class ScoutResourceView(models.Model):
    _name = 'scout.resource.view'
    _description = 'Recurso visto por un scout'
    _order = 'create_date desc'

    resource_id = fields.Many2one('scout.resource', string='Recurso', required=True, ondelete='cascade', index=True)
    member_id = fields.Many2one('res.partner', string='Scout', required=True, ondelete='cascade', index=True)

    _resource_member_uniq = models.Constraint('UNIQUE (resource_id, member_id)', 'Ya estaba marcado como visto.')
