# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .scout_common import MONTHS, join_names as _join_names_common, quarter_bounds, quarter_label, short_name

ITEM_TYPES = [
    ('status', 'Estado actual'),
    ('transition', 'Ruta de enlace'),
    ('previous', 'Avances del trimestre anterior'),
    ('objective', 'Objetivos del trimestre'),
    ('activity', 'Actividades del trimestre'),
    ('need', 'Necesidades y gestión'),
    ('key', 'Objetivos clave'),
    ('progress', 'Avances del trimestre'),
    ('rescue', 'Rescate / seguimiento de bajas'),
    ('next', 'Próximos pasos'),
]


_join_names = _join_names_common


class ScoutSectionReport(models.Model):
    _name = 'scout.section.report'
    _description = 'Informe trimestral de sección'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_from desc, section_id'

    name = fields.Char(compute='_compute_name', store=True, string='Informe')
    section_id = fields.Many2one('scout.section', string='Sección', required=True, tracking=True)
    date_from = fields.Date(string='Desde', required=True,
                            default=lambda self: quarter_bounds(fields.Date.context_today(self))[0])
    date_to = fields.Date(string='Hasta', required=True,
                          default=lambda self: quarter_bounds(fields.Date.context_today(self))[1])
    period_label = fields.Char(compute='_compute_name', store=True, string='Periodo')
    event_ids = fields.Many2many('calendar.event', compute='_compute_event_ids',
                                 string='Actividades del trimestre (calendario)')
    period_type = fields.Selection([('monthly', 'Mensual'), ('quarter', 'Trimestral'), ('annual', 'Anual')],
                                   string='Tipo de informe',
                                   default='quarter', required=True, tracking=True)
    presented_to = fields.Char(string='Presentado en', default='Consejo de Grupo')
    state = fields.Selection([('draft', 'Borrador'), ('presented', 'Presentado')], default='draft',
                             required=True, tracking=True, string='Estado')
    closing_phrase = fields.Char(string='Frase de cierre',
                                 help='Ej. ¡Seguimos construyendo juntos una madriguera donde cada castor crece!')
    situation = fields.Text(string='Situación actual (resumen)',
                            help='Párrafo libre, ej. "La Manada cuenta con 18 Gacelas registradas..."')

    # Indicadores (se recalculan con "Actualizar datos")
    stats_date_from = fields.Date(string='Asistencia evaluada desde', readonly=True)
    stats_date_to = fields.Date(string='Asistencia evaluada hasta', readonly=True)
    threshold_constant = fields.Integer(string='% para "constante"', default=75)
    threshold_risk = fields.Integer(string='% para "en riesgo"', default=40,
                                    help='Por debajo de este porcentaje, el miembro probablemente no regrese.')
    registered_count = fields.Integer(string='Registrados', readonly=True)
    paid_count = fields.Integer(string='Con registro pagado', readonly=True)
    constant_count = fields.Integer(string='Constantes', readonly=True)
    intermittent_count = fields.Integer(string='Intermitentes', readonly=True)
    risk_count = fields.Integer(string='Probablemente no regresen', readonly=True)
    promise_count = fields.Integer(string='Con Promesa', readonly=True)
    leader_count = fields.Integer(string='Dirigentes', readonly=True)
    transition_count = fields.Integer(string='En ruta de enlace', readonly=True)
    avg_attendance = fields.Float(string='Asistencia promedio (%)', readonly=True)

    item_ids = fields.One2many('scout.section.report.item', 'report_id', string='Contenido')
    member_line_ids = fields.One2many('scout.section.report.member', 'report_id', string='Miembros')
    company_id = fields.Many2one(related='section_id.company_id', store=True, string='Grupo')

    _section_period_uniq = models.Constraint(
        'UNIQUE (section_id, date_from, period_type)', 'Ya existe un informe de esa sección para el periodo.')

    @api.depends('section_id', 'date_from', 'date_to', 'period_type')
    def _compute_name(self):
        for report in self:
            label = quarter_label(report.date_from, report.date_to) if report.date_from and report.date_to else ''
            if report.period_type == 'monthly' and report.date_from:
                label = '%s %s' % (MONTHS[report.date_from.month - 1].capitalize(), report.date_from.year)
            report.period_label = label
            kind = {'annual': _('Informe anual'), 'monthly': _('Informe mensual')}.get(
                report.period_type, _('Informe trimestral'))
            report.name = _('%(kind)s %(unit)s %(period)s', kind=kind,
                            unit=report.section_id.display_name or '', period=label)

    @api.constrains('date_from', 'date_to', 'threshold_constant', 'threshold_risk')
    def _check_values(self):
        for report in self:
            if report.date_to < report.date_from:
                raise ValidationError(_('La fecha final debe ser posterior a la inicial.'))
            if not 0 <= report.threshold_risk <= report.threshold_constant <= 100:
                raise ValidationError(_('Los porcentajes deben cumplir 0 ≤ riesgo ≤ constante ≤ 100.'))

    def _compute_event_ids(self):
        for report in self:
            report.event_ids = report._get_events() if report.section_id and report.date_from else False

    def _get_events(self):
        """Actividades del calendario de la sección (o de todo el grupo) dentro del periodo."""
        self.ensure_one()
        return self.env['calendar.event'].sudo().search([
            ('is_scout_event', '=', True), ('scout_date', '>=', self.date_from), ('scout_date', '<=', self.date_to),
            '|', ('scout_section_ids', 'in', self.section_id.ids), ('scout_scope', '=', 'group'),
        ], order='start')

    # ------------------------------------------------------------------
    @staticmethod
    def _short_name(member):
        return short_name(member)

    def _classify(self, pct, counted):
        if not counted:
            return 'no_data'
        if pct >= self.threshold_constant:
            return 'constant'
        if pct >= self.threshold_risk:
            return 'intermittent'
        return 'risk'

    def _attendance_window(self):
        """Ventana de asistencia: el trimestre del informe, pero al menos los últimos 3 meses
        (así un informe a mitad de trimestre no se basa en una o dos sesiones)."""
        self.ensure_one()
        end = min(self.date_to, fields.Date.context_today(self))
        if self.period_type == 'monthly':
            return self.date_from, max(end, self.date_from)
        start = min(self.date_from, end - relativedelta(months=3))
        return start, end

    def _progress_summary(self, member):
        progress = member.sudo().scout_progress_ids
        achieved = progress.filtered(lambda p: p.state == 'achieved' and p.stage_type != 'promise')
        ongoing = progress.filtered(lambda p: p.state == 'in_progress')
        parts = []
        if member.sudo().scout_promise_date:
            parts.append(_('Promesa ✓'))
        if achieved:
            parts.append(_('Aprobó %s', _join_names(achieved.sorted('date_achieved').stage_id.mapped('name'))))
        if ongoing:
            parts.append(_('En curso: %s', _join_names(ongoing.stage_id.mapped('name'))))
        return '. '.join(parts) or _('Sin avances registrados')

    def _auto_items(self, members, lines_by_member):
        """Textos sugeridos a partir de los datos; se pueden editar o borrar."""
        self.ensure_one()
        items = []
        prev_from = self.date_from - relativedelta(months=1 if self.period_type == 'monthly' else 3)
        prev_to = self.date_from - relativedelta(days=1)

        # Ruta de enlace
        for member in members.filtered('scout_transition_date').sorted('scout_transition_date'):
            nxt = member.scout_section_id.next_section_id.display_name
            month = MONTHS[member.scout_transition_date.month - 1]
            items.append(('transition', _('%(name)s: ruta de enlace desde %(month)s%(to)s.',
                                          name=self._short_name(member), month=month,
                                          to=_(' hacia %s', nxt) if nxt else '')))
        # Avances del trimestre anterior y del trimestre actual, agrupados por adelanto
        Progress = self.env['scout.member.progress'].sudo()
        periods = [('progress', self.date_from, self.date_to)]
        if self.period_type != 'annual':
            periods.insert(0, ('previous', prev_from, prev_to))
        for item_type, d_from, d_to in periods:
            achieved = Progress.search([('member_id', 'in', members.ids), ('state', '=', 'achieved'),
                                        ('date_achieved', '>=', d_from), ('date_achieved', '<=', d_to)])
            for stage in achieved.stage_id.sorted(lambda s: (s.stage_type != 'promise', s.sequence)):
                who = achieved.filtered(lambda p: p.stage_id == stage).member_id
                verb = _('hicieron') if len(who) > 1 else _('hizo')
                items.append((item_type, _('%(n)s %(verb)s %(stage)s: %(names)s.', n=len(who), verb=verb,
                                           stage=stage.name,
                                           names=_join_names([self._short_name(m) for m in who]))))
        # Actividades del calendario: las juntas cuentan si ya tienen tema, programa o lista de asistencia
        seen = set()
        today = fields.Date.context_today(self)
        for event in self._get_events().filtered(lambda e: e.scout_activity_type not in ('none', 'council', 'training')):
            if event.scout_activity_type == 'regular' and not (
                    event.scout_theme or event.scout_plan_line_ids or event.scout_done
                    or (event.scout_attendance_line_ids and event.scout_date <= today)):
                continue
            day = event.scout_date.strftime('%d/%m') if event.scout_date else ''
            if event.scout_activity_type == 'regular':
                text = '%s %s' % (day, event.scout_theme or event.name)
                if event.scout_plan_line_ids:
                    text += _(' (%s actividades: %s)', len(event.scout_plan_line_ids),
                              ', '.join(event.scout_plan_line_ids[:4].mapped('name'))
                              + ('…' if len(event.scout_plan_line_ids) > 4 else ''))
            else:
                text = '%s %s' % (day, event.name) + (f' ({event.scout_theme})' if event.scout_theme else '')
            if event.scout_done or event.scout_attendance_closed:
                text += _(' ✓')
            if text not in seen:
                seen.add(text)
                items.append(('activity', text))
        # Objetivos: los de cada junta / actividad del periodo
        seen_obj = set()
        for event in self._get_events().filtered('scout_objective'):
            for objective in [o.strip(' -•') for o in event.scout_objective.splitlines() if o.strip(' -•')]:
                if objective.lower() not in seen_obj:
                    seen_obj.add(objective.lower())
                    items.append(('objective', objective))
        # Rescate
        risk = [m for m in members if lines_by_member[m.id]['class'] == 'risk']
        if risk:
            items.append(('rescue', _('Contactar a las familias de: %s.',
                                      _join_names([self._short_name(m) for m in risk]))))
        return items

    def action_refresh(self):
        Member = self.env['res.partner'].sudo()
        for report in self:
            if report.state == 'presented':
                raise UserError(_('El informe ya fue presentado; regrésalo a borrador para actualizarlo.'))
            members = Member.search([('is_scout', '=', True), ('scout_section_id', '=', report.section_id.id),
                                     ('scout_state', 'in', ('trial', 'active'))])
            youth = members.filtered(lambda m: m.scout_type == 'youth')
            date_from, date_to = report._attendance_window()
            stats = youth._scout_attendance_stats(date_from, date_to, include_open=True)
            cycle = self.env['scout.cycle']._get_current(report.company_id)

            lines_by_member, member_cmds = {}, [fields.Command.clear()]
            for member in youth:
                att, cnt = stats.get(member.id, (0, 0))
                pct = att / cnt * 100.0 if cnt else 0.0
                klass = report._classify(pct, cnt)
                lines_by_member[member.id] = {'class': klass}
                member_cmds.append(fields.Command.create({
                    'member_id': member.id,
                    'patrol_id': member.scout_patrol_id.id,
                    'patrol_role': member.scout_patrol_role,
                    'attendance_pct': pct,
                    'attendance_class': klass,
                    'has_promise': bool(member.scout_promise_date),
                    'in_transition': bool(member.scout_transition_date),
                    'progress_summary': report._progress_summary(member),
                }))
            classes = [v['class'] for v in lines_by_member.values()]
            paid = youth.filtered(lambda m: any(r.cycle_id == cycle and r.payment_state in ('paid', 'exempt')
                                                and r.state == 'validated' for r in m.scout_registration_ids))
            pcts = [l[2]['attendance_pct'] for l in member_cmds[1:] if l[2]['attendance_class'] != 'no_data']
            report.write({
                'stats_date_from': date_from,
                'stats_date_to': date_to,
                'registered_count': len(youth),
                'paid_count': len(paid),
                'constant_count': classes.count('constant'),
                'intermittent_count': classes.count('intermittent'),
                'risk_count': classes.count('risk'),
                'promise_count': len(youth.filtered('scout_promise_date')),
                'leader_count': len(members - youth),
                'transition_count': len(youth.filtered('scout_transition_date')),
                'avg_attendance': sum(pcts) / len(pcts) if pcts else 0.0,
                'member_line_ids': member_cmds,
            })
            # Reemplaza solo los textos automáticos; lo capturado a mano se conserva.
            report.item_ids.filtered('is_auto').unlink()
            report.item_ids = [fields.Command.create({'item_type': t, 'name': text, 'is_auto': True,
                                                      'sequence': 100 + i})
                               for i, (t, text) in enumerate(report._auto_items(youth, lines_by_member))]
        return True

    def action_present(self):
        self.write({'state': 'presented'})

    def action_draft(self):
        self.write({'state': 'draft'})

    # Ayudas para el reporte PDF -------------------------------------------
    def _items(self, item_type):
        return self.item_ids.filtered(lambda i: i.item_type == item_type).sorted('sequence')

    def _patrol_blocks(self):
        self.ensure_one()
        blocks = []
        lines = self.member_line_ids
        for patrol in lines.patrol_id.sorted(lambda p: (p.sequence, p.id)):
            plines = lines.filtered(lambda l: l.patrol_id == patrol).sorted(
                lambda l: {'leader': 0, 'sub': 1}.get(l.patrol_role, 2))
            blocks.append({'patrol': patrol, 'lines': plines, 'leader': patrol.follow_leader_id})
        unassigned = lines.filtered(lambda l: not l.patrol_id)
        return blocks, unassigned

    def _role_labels(self):
        """Etiquetas de guía/subguía según la sección (madera, seisenero, guía...)."""
        self.ensure_one()
        return self.section_id.patrol_role_label or _('Guía'), self.section_id.patrol_subrole_label or _('Subguía')


class ScoutSectionReportItem(models.Model):
    _name = 'scout.section.report.item'
    _description = 'Punto del informe trimestral'
    _order = 'item_type, sequence, id'

    report_id = fields.Many2one('scout.section.report', required=True, ondelete='cascade', index=True)
    item_type = fields.Selection(ITEM_TYPES, string='Bloque', required=True)
    sequence = fields.Integer(default=10)
    name = fields.Text(string='Texto', required=True)
    is_auto = fields.Boolean(string='Automático', readonly=True,
                             help='Generado con "Actualizar datos"; se reemplaza al volver a actualizar.')

    def write(self, vals):
        # Si alguien edita un texto automático, se vuelve manual y ya no se reemplaza.
        if 'name' in vals and 'is_auto' not in vals:
            vals['is_auto'] = False
        return super().write(vals)


class ScoutSectionReportMember(models.Model):
    _name = 'scout.section.report.member'
    _description = 'Miembro en el informe trimestral'
    _order = 'patrol_id, id'

    report_id = fields.Many2one('scout.section.report', required=True, ondelete='cascade', index=True)
    member_id = fields.Many2one('res.partner', string='Miembro', required=True, ondelete='cascade')
    scout_name = fields.Char(related='member_id.scout_name')
    patrol_id = fields.Many2one('scout.patrol', string='Equipo')
    patrol_role = fields.Selection([('leader', 'Guía'), ('sub', 'Subguía'), ('member', 'Integrante')],
                                   string='Rol')
    attendance_pct = fields.Float(string='% Asistencia')
    attendance_class = fields.Selection([
        ('constant', 'Constante'), ('intermittent', 'Intermitente'),
        ('risk', 'Probablemente no regrese'), ('no_data', 'Sin listas'),
    ], string='Clasificación')
    has_promise = fields.Boolean(string='Promesa')
    in_transition = fields.Boolean(string='Ruta de enlace')
    progress_summary = fields.Char(string='Avances en el recorrido')
    note = fields.Char(string='Nota')
