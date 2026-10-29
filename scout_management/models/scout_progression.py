# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


STAGE_TYPES = [
    ('promise', 'Promesa'),
    ('stage', 'Adelanto / recorrido'),
    ('terminal', 'Insignia terminal'),
    ('link', 'Ruta de enlace'),
    ('specialty', 'Especialidad'),
]
# Tipos que forman el camino de adelantos de la sección (termómetro del cuadro de adelantos).
PATH_TYPES = ('promise', 'stage', 'link', 'terminal')


class ScoutProgressionCategory(models.Model):
    """Grupo de especialidades (Deportes, Ecología, Correas…) para no mostrar una lista enorme."""
    _name = 'scout.progression.category'
    _description = 'Categoría de especialidades'
    _order = 'sequence, id'

    name = fields.Char(string='Categoría', required=True, translate=True)
    sequence = fields.Integer(default=10)
    color = fields.Char(string='Color', default='#0f3049', help='Color hexadecimal de la insignia del grupo.')
    stage_ids = fields.One2many('scout.progression.stage', 'category_id', string='Especialidades')
    required_count = fields.Integer(string='Requeridas',
                                    help='Cuántas de esta categoría se piden (ej. 2 especialidades técnicas en Tropa).')
    active = fields.Boolean(default=True)


class ScoutProgressionStage(models.Model):
    _name = 'scout.progression.stage'
    _description = 'Adelanto, recorrido o especialidad'
    _order = 'type_order, sequence, id'

    name = fields.Char(string='Nombre', required=True, translate=True)
    sequence = fields.Integer(default=10)
    stage_type = fields.Selection(STAGE_TYPES, string='Tipo', required=True, default='stage')
    type_order = fields.Integer(compute='_compute_type_order', store=True,
                                help='Orden del tipo: Promesa, adelantos, insignia terminal, ruta de enlace, especialidades.')
    category_id = fields.Many2one('scout.progression.category', string='Categoría', index=True,
                                  help='Para especialidades: grupo al que pertenece (Deportes, Ecología, Correas…).')
    section_ids = fields.Many2many('scout.section', string='Secciones',
                                   help='Vacío = aplica a todas las secciones.')
    description = fields.Text(string='Requisitos / descripción')
    image_128 = fields.Image(string='Insignia', max_width=128, max_height=128)
    level = fields.Selection([('1', 'Amarillo'), ('2', 'Verde'), ('3', 'Rojo')], string='Nivel',
                             help='Especialidades básicas por niveles (Tropa): cada categoría tiene amarillo, verde y rojo.')
    active = fields.Boolean(default=True)

    # ------------------------------------------------------------------ categorías
    CATEGORY_RULES = (
        ('stage_cat_tecnicas', ('tecnica:',)),
        ('stage_cat_rover', ('especialidad rover',)),
        ('stage_cat_correas', ('correa',)),
        ('stage_cat_deportes', ('(deportes)', 'insignia deportes', 'especialidad: deportes')),
        ('stage_cat_expresion', ('(expresion y comunicacion)', 'insignia expresion', 'arte y expresion')),
        ('stage_cat_seguridad', ('(seguridad y rescate)', 'insignia seguridad', 'primeros auxilios y salud')),
        ('stage_cat_tecnologia', ('(tecnologia y ciencia)', 'insignia tecnologia', 'ciencia y tecnologia')),
        ('stage_cat_ecologia', ('(ecologia)', 'insignia ecologia', 'naturaleza y servicio')),
        ('stage_cat_humanidades', ('(humanidades)', 'insignia humanidades', 'espiritualidad')),
    )

    @api.model
    def _scout_assign_categories(self):
        """Acomoda en su categoría las especialidades que aún no tienen (por su nombre)."""
        import unicodedata
        def norm(text):
            return unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()
        refs = {xid: self.env.ref(f'scout_management.{xid}', raise_if_not_found=False)
                for xid, _keys in self.CATEGORY_RULES}
        other = self.env.ref('scout_management.stage_cat_otras', raise_if_not_found=False)
        stages = self.with_context(active_test=False).search(
            [('stage_type', '=', 'specialty'), ('category_id', '=', False)])
        for stage in stages:
            name = norm(stage.name)
            category = next((refs[xid] for xid, keys in self.CATEGORY_RULES
                             if refs[xid] and any(k in name for k in keys)), other)
            if category:
                stage.category_id = category
        return stages

    # ------------------------------------------------------------------ especialidades de Tropa
    TROPA_BASIC = [  # (xmlid de la categoría, clave, nombre)
        ('stage_cat_expresion', 'expresion', 'Expresión y comunicación'),
        ('stage_cat_deportes', 'deportes', 'Deportes'),
        ('stage_cat_tecnologia', 'ciencia', 'Ciencia y tecnología'),
        ('stage_cat_humanidades', 'humanidades', 'Humanidades'),
        ('stage_cat_seguridad', 'seguridad', 'Seguridad y rescate'),
        ('stage_cat_ecologia', 'ecologia', 'Ecología'),
    ]
    TROPA_TECHNICAL = [('campismo', 'Campismo'), ('supervivencia', 'Supervivencia'), ('observacion', 'Observación'),
                       ('senalacion', 'Señalación'), ('pionerismo', 'Pionerismo'), ('navegacion', 'Navegación')]
    LEVEL_NAMES = [('1', 'Amarillo'), ('2', 'Verde'), ('3', 'Rojo')]

    @api.model
    def _scout_tropa_specialties_setup(self):
        """Especialidades de Tropa (femenina y masculina): 6 básicas con nivel amarillo, verde y rojo; 6 técnicas
        (se piden 2) y Primeros auxilios o Protección civil (obligatoria a partir de Primera clase).
        Se crea solo lo que falta, así que se puede correr en cada actualización."""
        import unicodedata

        def norm(text):
            return unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()
        ref = lambda xid: self.env.ref(f'scout_management.{xid}', raise_if_not_found=False)  # noqa: E731
        tropas = (ref('section_tropa_atlas') or self.env['scout.section']) | (ref('section_tropa_hydra')
                                                                             or self.env['scout.section'])
        if not tropas:
            return
        Data = self.env['ir.model.data'].sudo()
        Category = self.env['scout.progression.category'].sudo()

        def ensure(xid, model, vals, search=None):
            record = ref(xid)
            if record:
                return record
            record = search() if search else self.env[model].browse()
            if not record:
                record = self.env[model].sudo().create(vals)
            Data.create({'module': 'scout_management', 'name': xid, 'model': model, 'res_id': record.id,
                         'noupdate': True})
            return record
        tech_cat = ref('stage_cat_tecnicas')
        if tech_cat and not tech_cat.required_count:
            tech_cat.write({'name': 'Especialidades técnicas' if tech_cat.name == 'Técnicas de Tropa' else tech_cat.name,
                            'required_count': 2})
        aid_cat = ensure('stage_cat_primeros_auxilios', 'scout.progression.category',
                         {'name': 'Primeros auxilios o Protección civil', 'sequence': 85, 'color': '#d7262f',
                          'required_count': 1})
        tecnologia = ref('stage_cat_tecnologia')
        if tecnologia and tecnologia.name == 'Tecnología y ciencia':
            tecnologia.name = 'Ciencia y tecnología'
        seq = 200
        for cat_xid, key, label in self.TROPA_BASIC:
            category = ref(cat_xid)
            if not category:
                continue
            for level, level_name in self.LEVEL_NAMES:
                seq += 1
                ensure(f'stage_tropa_{key}_{level}', 'scout.progression.stage', {
                    'name': f'{label} · {level_name}', 'stage_type': 'specialty', 'level': level, 'sequence': seq,
                    'category_id': category.id, 'section_ids': [fields.Command.set(tropas.ids)],
                    'description': f'Especialidad básica de {label}, nivel {level_name.lower()}.'})
        if tech_cat:
            existing = self.with_context(active_test=False).search([('category_id', '=', tech_cat.id)])
            for key, label in self.TROPA_TECHNICAL:
                seq += 1
                ensure(f'stage_tropa_tec_{key}', 'scout.progression.stage', {
                    'name': label, 'stage_type': 'specialty', 'sequence': seq, 'category_id': tech_cat.id,
                    'section_ids': [fields.Command.set(tropas.ids)],
                    'description': 'Especialidad técnica (de la lista se piden dos).'},
                    search=lambda key=key: existing.filtered(lambda s: key in norm(s.name)
                                                             and (not s.section_ids or s.section_ids & tropas))[:1])
        ensure('stage_tropa_primeros_auxilios', 'scout.progression.stage', {
            'name': 'Primeros auxilios o Protección civil', 'stage_type': 'specialty', 'sequence': seq + 1,
            'category_id': aid_cat.id, 'section_ids': [fields.Command.set(tropas.ids)],
            'description': 'Especialidad técnica obligatoria e independiente para todos los adelantos a partir de '
                           'Primera clase.'})

    @api.model
    def _scout_v4_migrate(self):
        """Versión 4: orden de Tropa (Promesa, Tercera, Segunda, Primera) y categorías de especialidades."""
        for xid, seq in (('stage_tropa_tercera', 10), ('stage_tropa_segunda', 20), ('stage_tropa_primera', 30)):
            stage = self.env.ref(f'scout_management.{xid}', raise_if_not_found=False)
            if stage:
                stage.write({'sequence': seq, 'stage_type': 'stage'})
        stages = self.with_context(active_test=False).search([])
        stages.modified(['stage_type'])
        self.env.flush_all()
        self._scout_assign_categories()

    @api.model
    def _scout_v44_migrate(self):
        """En Tropa la Promesa y la Tercera clase son lo mismo: se archiva "Tercera clase" y sus registros pasan a Promesa."""
        tercera = self.env.ref('scout_management.stage_tropa_tercera', raise_if_not_found=False)
        promise = self.env.ref('scout_management.stage_promise', raise_if_not_found=False)
        if not tercera or not promise:
            return
        Progress = self.env['scout.member.progress'].sudo()
        for progress in Progress.search([('stage_id', '=', tercera.id)]):
            existing = Progress.search([('member_id', '=', progress.member_id.id), ('stage_id', '=', promise.id)], limit=1)
            if existing:
                if existing.state != 'achieved' and progress.state == 'achieved':
                    existing.write({'state': 'achieved', 'date_achieved': progress.date_achieved})
                progress.unlink()
            else:
                progress.stage_id = promise
        tercera.active = False

    @api.depends('stage_type')
    def _compute_type_order(self):
        order = {key: i for i, (key, _label) in enumerate(STAGE_TYPES)}
        for stage in self:
            stage.type_order = order.get(stage.stage_type, 9)


class ScoutMemberProgress(models.Model):
    _name = 'scout.member.progress'
    _description = 'Avance del miembro'
    _order = 'date_achieved desc, id desc'

    member_id = fields.Many2one('res.partner', string='Miembro', required=True, ondelete='cascade', index=True,
                                domain="[('is_scout', '=', True)]")
    stage_id = fields.Many2one('scout.progression.stage', string='Adelanto', required=True, ondelete='restrict')
    stage_type = fields.Selection(related='stage_id.stage_type', store=True)
    state = fields.Selection([('in_progress', 'En curso'), ('achieved', 'Logrado')], string='Estado',
                             default='in_progress', required=True)
    date_start = fields.Date(string='Inicio', default=fields.Date.context_today)
    date_achieved = fields.Date(string='Fecha del adelanto')
    leader_id = fields.Many2one('res.partner', string='Dirigente que da seguimiento',
                                domain="[('is_scout', '=', True), ('scout_type', '=', 'leader')]")
    note = fields.Char(string='Nota')
    section_id = fields.Many2one(related='member_id.scout_section_id', store=True, string='Sección')

    _member_stage_uniq = models.Constraint(
        'UNIQUE (member_id, stage_id)', 'El miembro ya tiene registrado ese adelanto.')

    @api.constrains('state', 'date_achieved')
    def _check_date(self):
        for progress in self:
            if progress.state == 'achieved' and not progress.date_achieved:
                raise ValidationError(_('Indica la fecha en que se logró el adelanto.'))

    @api.onchange('state')
    def _onchange_state(self):
        if self.state == 'achieved' and not self.date_achieved:
            self.date_achieved = fields.Date.context_today(self)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._scout_sync_transition()
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'state' in vals or 'stage_id' in vals:
            self._scout_sync_transition()
        return res

    def _scout_sync_transition(self):
        """Empezar la ruta de enlace registra en el miembro la fecha de inicio de su ruta."""
        for progress in self.filtered(lambda p: p.stage_id.stage_type == 'link'):
            member = progress.member_id.sudo()
            day = progress.date_start or fields.Date.context_today(progress)
            if not member.scout_transition_date:
                member.with_context(scout_change_approved=True).scout_transition_date = day
            member._scout_ensure_route()
            route = member.scout_route_id
            if route and route.kind == 'transfer' and not route.date_route_real:
                route.sudo().action_start_route(day)

    @api.depends('member_id', 'stage_id')
    def _compute_display_name(self):
        for progress in self:
            progress.display_name = f'{progress.member_id.name or ""} - {progress.stage_id.name or ""}'
