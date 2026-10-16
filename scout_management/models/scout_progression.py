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
# Tipos que forman el camino de adelantos de la sección (termómetro del cuadro de logros).
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
    date_achieved = fields.Date(string='Fecha de logro')
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
