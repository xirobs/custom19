# -*- coding: utf-8 -*-
"""Ruta de progresión secuencial (Sistema de adelanto de Tropa).

Cada nivel se le abre al scout solo cuando terminó el anterior:
1 Tercera clase -> 2 Segunda clase -> 3 Primera clase -> 4 Cordón Scout -> 5 Correa de Manigua
-> 6 Insignia terminal -> 7 Piloto Scout. El Manual de Especialidades corre en paralelo desde Segunda clase.

Cada nivel puede llevar su manual (PDF) y la sección una imagen con la secuencia, que ven el scout y su familia.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .scout_progression import PATH_TYPES

SEQUENCE_TYPES = tuple(t for t in PATH_TYPES if t != 'link')  # la ruta de enlace depende de la edad, no del nivel


class ScoutSectionProgressionPath(models.Model):
    _inherit = 'scout.section'

    sequential_path = fields.Boolean(
        string='Ruta de adelantos secuencial',
        help='Cada nivel se abre solo cuando el scout terminó el anterior (no se puede registrar en curso ni logrado '
             'un nivel si el anterior no está logrado). La ruta de enlace no se bloquea.')
    specialty_start_stage_id = fields.Many2one(
        'scout.progression.stage', string='Especialidades a partir de',
        help='Las especialidades se abren cuando el scout llega a este nivel (por ejemplo, Segunda clase).')
    path_image = fields.Image(string='Imagen de la ruta de progresión', max_width=1600, max_height=1600,
                              help='Infografía con la secuencia de niveles que ven el scout y su familia.')
    specialty_manual = fields.Binary(string='Manual de especialidades', attachment=True)
    specialty_manual_name = fields.Char()


class ScoutProgressionStageManual(models.Model):
    _inherit = 'scout.progression.stage'

    path_label = fields.Char(string='Nombre en la ruta', translate=True,
                             help='Cómo se llama el nivel en la ruta de progresión, ej. "Manual Cordón Scout".')
    summary = fields.Char(string='Descripción corta', translate=True,
                          help='Lo que se muestra en la ruta, por ejemplo "Manual de progresión Scout – Tercera Clase".')
    manual = fields.Binary(string='Manual (PDF)', attachment=True)
    manual_name = fields.Char()


class ScoutMemberProgressSequence(models.Model):
    _inherit = 'scout.member.progress'

    @api.model
    def _scout_sequence_path(self, section):
        portal = self.env['scout.leader.portal'].sudo()
        return portal.path_stages(section).filtered(lambda s: s.stage_type in SEQUENCE_TYPES)

    @api.model
    def _scout_sequence_block(self, member, stage):
        """Motivo por el que el scout todavía no puede tener este nivel (o False si sí puede)."""
        member = member.sudo()
        section = member.scout_section_id
        if not section or not section.sequential_path or not stage:
            return False
        portal = self.env['scout.leader.portal'].sudo()
        path = self._scout_sequence_path(section)
        achieved = portal.canon(self.sudo().search([('member_id', '=', member.id), ('state', '=', 'achieved')]).stage_id,
                                section)
        stage = portal.canon(stage, section)
        target = stage
        if stage.stage_type == 'specialty':
            target = section.specialty_start_stage_id
            if not target or target not in path:
                return False
        elif stage not in path:
            return False
        index = list(path).index(target)
        if index == 0:
            return False
        previous = path[index - 1]
        if previous in achieved:
            return False
        if stage.stage_type == 'specialty':
            return _('%(name)s todavía no puede hacer especialidades: se abren al llegar a %(level)s (primero debe '
                     'terminar %(prev)s).', name=member.scout_name or member.name,
                     level=portal.stage_label(target, section), prev=portal.stage_label(previous, section))
        return _('%(name)s todavía no puede empezar %(stage)s: primero debe terminar %(prev)s.',
                 name=member.scout_name or member.name, stage=portal.stage_label(stage, section),
                 prev=portal.stage_label(previous, section))

    @api.constrains('state', 'stage_id', 'member_id')
    def _check_sequential_path(self):
        if self.env.context.get('scout_skip_sequence'):
            return
        for progress in self:
            reason = self._scout_sequence_block(progress.member_id, progress.stage_id)
            if reason:
                raise ValidationError(reason)

    def unlink(self):
        """No se puede quitar un nivel si el scout ya avanzó al siguiente."""
        if not self.env.context.get('scout_skip_sequence'):
            for progress in self:
                section = progress.member_id.scout_section_id
                if not section.sequential_path or progress.stage_id.stage_type not in SEQUENCE_TYPES:
                    continue
                path = list(self._scout_sequence_path(section))
                current = self.env['scout.leader.portal'].sudo().canon(progress.stage_id, section)
                if current not in path:
                    continue
                later = path[path.index(current) + 1:]
                raw = self.env['scout.leader.portal'].sudo()._path_raw(section)
                mapping = self.env['scout.leader.portal'].sudo().path_canonical(section)
                later = [st for st in raw if mapping.get(st.id) in later]
                if later and self.sudo().search_count([('member_id', '=', progress.member_id.id),
                                                       ('stage_id', 'in', [s.id for s in later]),
                                                       ('id', 'not in', self.ids)]):
                    label = self.env['scout.leader.portal'].sudo().stage_label(progress.stage_id, section)
                    raise ValidationError(_('No se puede quitar %(stage)s de %(name)s porque ya va en un nivel '
                                            'posterior.', stage=label, name=progress.member_id.name))
        return super().unlink()


class ScoutLeaderPortalPath(models.AbstractModel):
    _inherit = 'scout.leader.portal'

    @api.model
    def _path_raw(self, section):
        return super().path_stages(section)

    @api.model
    def _path_key(self, stage, section):
        return self.env['scout.resource']._scout_norm(self.path_label(stage, section))

    @api.model
    def path_canonical(self, section):
        """{id de etapa: etapa que la representa en la ruta}. Dos etapas que son el mismo nivel (por ejemplo
        "Promesa (Tercera clase)" y "Tercera clase") cuentan como una sola: se muestra una y los avances de
        cualquiera de las dos valen. Se prefiere la de tipo Promesa y la propia de la sección."""
        memo = self.env.__dict__.setdefault('_scout_path_canon', {})
        if section.id in memo:
            return memo[section.id]
        groups = {}
        for stage in self._path_raw(section):
            key = ('link' if stage.stage_type == 'link' else 'path', self._path_key(stage, section))
            groups.setdefault(key, []).append(stage)
        mapping = {}
        for stages in groups.values():
            keep = max(stages, key=lambda st: (st.stage_type == 'promise', bool(st.section_ids), -st.id))
            for st in stages:
                mapping[st.id] = keep
        memo[section.id] = mapping
        return mapping

    @api.model
    def path_stages(self, section):
        raw = self._path_raw(section)
        mapping = self.path_canonical(section)
        return raw.filtered(lambda s: mapping.get(s.id) == s)

    @api.model
    def canon(self, stages, section):
        """Etapas (registros de avance o material) traducidas a la etapa que se muestra en la ruta."""
        mapping = self.path_canonical(section) if section else {}
        result = self.env['scout.progression.stage']
        for stage in stages:
            result |= mapping.get(stage.id, stage)
        return result

    @api.model
    def path_label(self, stage, section):
        """Nombre del nivel en la ruta (como en la infografía): 'Tercera clase', 'Manual Cordón Scout'..."""
        if not stage:
            return ''
        if stage.path_label:
            return stage.path_label
        label = self.stage_label(stage, section)
        if stage.stage_type == 'promise' and '(' in label:  # "Promesa (Tercera clase)" -> "Tercera clase"
            label = label.split('(')[-1].rstrip(')').strip()
            label = label[:1].upper() + label[1:]
        return label

    @api.model
    def stage_summary(self, stage, section):
        if stage.summary:
            return stage.summary
        if stage.stage_type == 'promise' and section.sequential_path:
            label = self.stage_label(stage, section)
            label = label.split('(')[-1].rstrip(')') if '(' in label else label
            return _('Manual de progresión Scout – %s.', label.strip().title())
        return ''

    @api.model
    def progression_path(self, member=None, section=None):
        """Niveles de la ruta del scout en orden, con su estado (logrado / te toca / bloqueado) y su manual."""
        section = (section or member.sudo().scout_section_id).sudo()
        Progress = self.env['scout.member.progress'].sudo()
        path = Progress._scout_sequence_path(section) if section else self.env['scout.progression.stage']
        records = Progress.search([('member_id', '=', member.id)]) if member else Progress
        achieved = self.canon(records.filtered(lambda p: p.state == 'achieved').stage_id, section)
        started = self.canon(records.filtered(lambda p: p.state == 'in_progress').stage_id, section)
        sequential = bool(section and section.sequential_path)
        levels, current_found = [], False
        for index, stage in enumerate(path):
            if not member:
                state = 'open'
            elif stage in achieved:
                state = 'done'
            elif not current_found:
                state, current_found = ('current' if (stage in started or index == 0 or path[index - 1] in achieved
                                                      or not sequential) else 'locked'), True
            else:
                state = 'locked' if sequential else ('current' if stage in started else 'open')
            levels.append({
                'num': index + 1, 'stage': stage, 'label': self.path_label(stage, section),
                'summary': self.stage_summary(stage, section), 'state': state, 'has_manual': bool(stage.manual),
                'started': stage in started,
                'date': records.filtered(lambda p, s=stage: self.canon(p.stage_id, section) == s)[:1].date_achieved,
                'locked_by': self.path_label(path[index - 1], section) if index else '',
            })
        start = section.specialty_start_stage_id if section else False
        specialties = None
        if section and (start or section.specialty_manual or section.sequential_path):
            open_ = True
            if member and sequential and start and start in path:
                idx = list(path).index(start)
                open_ = idx == 0 or path[idx - 1] in achieved
            last = levels[-1]['label'] if levels else ''
            specialties = {'open': open_, 'from': self.path_label(start, section) if start else '', 'to': last,
                           'from_num': (list(path).index(start) + 1) if start and start in path else 1,
                           'to_num': len(levels),
                           'has_manual': bool(section.specialty_manual),
                           'count': len(records.filtered(lambda p: p.stage_id.stage_type == 'specialty'
                                                         and p.state == 'achieved'))}
        # material de cada nivel (diagramas abiertos de primera vista), en el orden de la ruta
        resources = self.env['scout.resource'].sudo()
        if section:
            resources = resources._scout_for_section(section, scouts=bool(member))
        for level in levels:
            items = resources.filtered(lambda r, s=level['stage']: r.stage_id and self.canon(r.stage_id, section) == s
                                       ).sorted(lambda r: (r.sequence, r.id))
            level['diagrams'] = items.filtered(lambda r: r.kind == 'image' and r.file)
            level['materials'] = items - level['diagrams']
        if specialties is not None:
            items = resources.filtered(lambda r: r.stage_id.stage_type == 'specialty' or r.for_specialties).sorted(
                lambda r: (r.stage_id.sequence, r.sequence, r.id))
            specialties['diagrams'] = items.filtered(lambda r: r.kind == 'image' and r.file)
            specialties['materials'] = items - specialties['diagrams']
        return {'section': section, 'levels': levels, 'specialties': specialties, 'sequential': sequential,
                'has_image': bool(section and section.path_image),
                'done': len([l for l in levels if l['state'] == 'done']), 'total': len(levels)}

    @api.model
    def path_manual_allowed(self, member, stage_id):
        """Etapa cuyo manual puede abrir el scout (solo niveles ya abiertos para él), o None."""
        data = self.progression_path(member)
        for level in data['levels']:
            if level['stage'].id == int(stage_id or 0) and level['state'] != 'locked' and level['has_manual']:
                return level['stage']
        return None
