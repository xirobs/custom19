# -*- coding: utf-8 -*-
"""Tableros configurables (estilo «Informe de Asegurados – Padrón Oficial de la Red»).

Cada tablero es una lista ordenada de bloques (totales, tablas, barras, hallazgos, ubicación…).
La administración crea tableros y bloques desde la plataforma; se ven en la plataforma, en el
portal del asesor y en el del grupo, y se descargan en PDF. Las gráficas son HTML/CSS (sin
JavaScript) para que el PDF salga igual que la pantalla.
"""
from collections import OrderedDict, defaultdict
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .rss_common import SECTION_SELECTION
from .rss_credential import static_data_uri
from .rss_dashboard import AGE_BLOCKS, _age_block

MONTHS_ES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre',
             'octubre', 'noviembre', 'diciembre']
SECTION_AGES = {'castores': '3 – 5', 'manada': '6 – 10', 'tropa': '11 – 17', 'clan': '18 – 24',
                'dirigente': '25 o más'}
# Nombres por sexo (Gacelas / Lobatos, Tropa Femenil / Masculina…)
SECTION_SEX_LABELS = {
    ('castores', 'female'): 'Castores ♀', ('castores', 'male'): 'Castores ♂',
    ('manada', 'female'): 'Gacelas', ('manada', 'male'): 'Lobatos',
    ('tropa', 'female'): 'Tropa Femenil', ('tropa', 'male'): 'Tropa Masculina',
    ('clan', 'female'): 'Clan ♀', ('clan', 'male'): 'Clan ♂',
    ('dirigente', 'female'): 'Jefatura y Adultos ♀', ('dirigente', 'male'): 'Jefatura y Adultos ♂',
}
# Abreviaturas usuales de los estados de México (para «Sede»)
MX_STATE_ABBR = {
    'Aguascalientes': 'Ags.', 'Baja California': 'B.C.', 'Baja California Sur': 'B.C.S.', 'Campeche': 'Camp.',
    'Chiapas': 'Chis.', 'Chihuahua': 'Chih.', 'Ciudad de México': 'CDMX', 'Coahuila': 'Coah.', 'Colima': 'Col.',
    'Durango': 'Dgo.', 'Estado de México': 'Méx.', 'México': 'Méx.', 'Guanajuato': 'Gto.', 'Guerrero': 'Gro.',
    'Hidalgo': 'Hgo.', 'Jalisco': 'Jal.', 'Michoacán': 'Mich.', 'Morelos': 'Mor.', 'Nayarit': 'Nay.',
    'Nuevo León': 'N.L.', 'Oaxaca': 'Oax.', 'Puebla': 'Pue.', 'Querétaro': 'Qro.', 'Quintana Roo': 'Q. Roo',
    'San Luis Potosí': 'S.L.P.', 'Sinaloa': 'Sin.', 'Sonora': 'Son.', 'Tabasco': 'Tab.', 'Tamaulipas': 'Tamps.',
    'Tlaxcala': 'Tlax.', 'Veracruz': 'Ver.', 'Yucatán': 'Yuc.', 'Zacatecas': 'Zac.',
}

BLOCK_TYPES = [
    ('header', 'Encabezado (logo, título y corte)'),
    ('kpi_padron', 'Totales: asegurados, grupos y estados'),
    ('kpi_operacion', 'Totales: cobrado, pagos en trámite y siniestros'),
    ('kpi_certificados', 'Totales: certificados, edad promedio y sexo'),
    ('table_group', 'Tabla: asegurados por grupo'),
    ('table_section', 'Tabla: asegurados por sección'),
    ('bar_section', 'Barras: distribución por sección'),
    ('bar_group', 'Columnas: distribución por grupo'),
    ('bar_age', 'Barras: distribución por edad'),
    ('table_age_sex', 'Tabla: edad × sexo'),
    ('table_group_section', 'Tabla: grupo × sección'),
    ('payments_group', 'Tabla: pagos por grupo'),
    ('claims_summary', 'Siniestros: resumen por estatus y lesión'),
    ('findings', 'Principales hallazgos (automáticos)'),
    ('locations', 'Ubicación de los grupos'),
    ('text', 'Texto libre'),
]
DEFAULT_TITLES = {
    'table_group': 'Asegurados por Grupo', 'table_section': 'Asegurados por Sección (Red)',
    'bar_section': 'Distribución por Sección', 'bar_group': 'Distribución por Grupo',
    'bar_age': 'Distribución por Edad', 'table_age_sex': 'Edad × Sexo', 'table_group_section': 'Grupo × Sección',
    'payments_group': 'Pagos por Grupo', 'claims_summary': 'Siniestros', 'findings': 'Principales hallazgos',
    'locations': 'Ubicación de los Grupos', 'text': 'Notas',
}


class RssScoutGroupState(models.Model):
    _inherit = 'rss.scout.group'

    state_id = fields.Many2one('res.country.state', string='Estado',
                               domain="[('country_id.code', '=', 'MX')]", tracking=True)
    seat_label = fields.Char('Sede', compute='_compute_seat_label')

    @api.depends('city', 'state_id')
    def _compute_seat_label(self):
        for group in self:
            abbr = MX_STATE_ABBR.get(group.state_id.name, group.state_id.code) if group.state_id else ''
            group.seat_label = ', '.join(x for x in (group.city, abbr) if x)


class RssBoard(models.Model):
    _name = 'rss.board'
    _description = 'Tablero configurable (Red Seguro Scout)'
    _order = 'sequence, id'

    name = fields.Char('Título', required=True, translate=True)
    subtitle = fields.Char('Subtítulo', translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    scope = fields.Selection([
        ('active', 'Solo asegurados vigentes'),
        ('active_draft', 'Vigentes y en preinscripción'),
        ('all', 'Todos los registros (incluye bajas)'),
    ], string='Quiénes cuentan', default='active_draft', required=True)
    show_platform = fields.Boolean('En la plataforma', default=True)
    show_advisor = fields.Boolean('En el portal del asesor', default=True)
    show_group = fields.Boolean('En el portal del grupo', default=True)
    block_ids = fields.One2many('rss.board.block', 'board_id', string='Bloques', copy=True)

    # ------------------------------------------------------------------ datos
    @api.model
    def _board_data(self, groups, scope):
        Insured = self.env['rss.insured'].sudo().with_context(active_test=False)
        states = {'active': ['active'], 'active_draft': ['active', 'draft'],
                  'all': ['active', 'draft', 'inactive']}[scope]
        insured = Insured.search([('group_id', 'in', groups.ids), ('state', 'in', states)])
        total = len(insured)
        pct = (lambda n: round(100.0 * n / total, 1) if total else 0.0)
        groups = groups.sorted(lambda g: (g.number or 999, g.name))
        per_group = OrderedDict((g.id, insured.filtered(lambda i, g=g: i.group_id == g)) for g in groups)
        group_rows = []
        for n, g in enumerate(groups, start=1):
            members = per_group[g.id]
            group_rows.append({
                'n': n, 'id': g.id, 'name': g.name.split('–')[0].split(' - ')[0].strip() if '–' in g.name else g.name,
                'full_name': g.name, 'short': 'G%s' % g.number if g.number else (g.name or '')[:6],
                'seat': g.seat_label or g.city or '', 'count': len(members),
                'male': len(members.filtered(lambda i: i.sex == 'male')),
                'female': len(members.filtered(lambda i: i.sex == 'female')),
                'state': g.state_id.name or '', 'city': g.city or '',
            })
        labels = dict(SECTION_SELECTION)
        sections, sections_sex = [], []
        for key, label in SECTION_SELECTION:
            members = insured.filtered(lambda i, key=key: i.section == key)
            short = label.split(' (')[0]
            sections.append({'key': key, 'label': '%s (%s)' % (short, SECTION_AGES[key]) if key != 'dirigente' else short,
                             'name': short, 'count': len(members), 'pct': pct(len(members))})
            for sex in ('female', 'male'):
                part = members.filtered(lambda i, sex=sex: i.sex == sex)
                name = SECTION_SEX_LABELS[(key, sex)]
                sections_sex.append({'key': '%s_%s' % (key, sex), 'name': name,
                                     'label': '%s (%s %s)' % (name, SECTION_AGES[key], '♀' if sex == 'female' else '♂')
                                     if key in ('manada', 'tropa') else name,
                                     'count': len(part), 'pct': pct(len(part))})
        no_section = insured.filtered(lambda i: not i.section)
        if no_section:
            sections.append({'key': 'none', 'label': _('Sin sección'), 'name': _('Sin sección'),
                             'count': len(no_section), 'pct': pct(len(no_section))})
            sections_sex.append(dict(sections[-1]))
        ages = OrderedDict((label, {'male': 0, 'female': 0, 'total': 0}) for _l, _h, label in AGE_BLOCKS)
        for rec in insured:
            block = ages[_age_block(rec.age)]
            block['total'] += 1
            if rec.sex in ('male', 'female'):
                block[rec.sex] += 1
        group_section = [{'name': r['short'], 'full_name': r['full_name'],
                          'values': [len(per_group[r['id']].filtered(lambda i, k=k: i.section == k))
                                     for k, _l in SECTION_SELECTION]} for r in group_rows]
        # Ubicación: estado -> grupos
        locations = OrderedDict()
        for r in group_rows:
            key = r['state'] or r['city'] or _('Sin ubicación')
            seat = r['seat'] or key
            locations.setdefault(key, {'seat': seat, 'groups': []})['groups'].append(r['short'])
        # Pagos y siniestros
        payments = self.env['rss.payment'].sudo().search([('group_id', 'in', groups.ids),
                                                          ('state', 'in', ('confirmed', 'remitted'))])
        pay_rows = []
        for r in group_rows:
            gp = payments.filtered(lambda p, gid=r['id']: p.group_id.id == gid)
            pay_rows.append({'name': r['full_name'], 'short': r['short'], 'count': len(gp),
                             'amount': sum(gp.mapped('amount')), 'last': max(gp.mapped('date')) if gp else False})
        pending = self.env['rss.import.batch'].sudo().search([('group_id', 'in', groups.ids),
                                                              ('state', 'in', ('payment', 'verifying'))])
        Claim = self.env['rss.claim'].sudo()
        claims = Claim.search([('group_id', 'in', groups.ids)])
        claim_states = dict(Claim._fields['state']._description_selection(self.env))
        injuries = dict(Claim._fields['injury_type']._description_selection(self.env))
        by_state = OrderedDict((label, 0) for _k, label in Claim._fields['state']._description_selection(self.env))
        by_injury = defaultdict(int)
        for c in claims:
            by_state[claim_states.get(c.state, c.state)] += 1
            by_injury[injuries.get(c.injury_type, c.injury_type or _('Otra'))] += 1
        active = insured.filtered(lambda i: i.state == 'active')
        state_names = [k for k in locations]
        data = {
            'total': total, 'active': len(active), 'draft': len(insured.filtered(lambda i: i.state == 'draft')),
            'groups': group_rows, 'group_count': len([r for r in group_rows if r['count']]) or len(group_rows),
            'states': state_names, 'state_count': len([s for s in state_names if s != _('Sin ubicación')]),
            'sections': sections, 'sections_sex': sections_sex, 'ages': ages,
            'group_section': group_section, 'section_cols': [l.split(' (')[0] for _k, l in SECTION_SELECTION],
            'locations': locations,
            'male': len(insured.filtered(lambda i: i.sex == 'male')),
            'female': len(insured.filtered(lambda i: i.sex == 'female')),
            'avg_age': round(sum(insured.mapped('age')) / total, 1) if total else 0,
            'certificates': len(insured.filtered('has_certificate')),
            'cert_pct': pct(len(insured.filtered('has_certificate'))),
            'payments': pay_rows, 'paid_total': sum(payments.mapped('amount')),
            'pending_count': len(pending), 'pending_amount': sum(pending.mapped('amount_total')),
            'claims': len(claims), 'claims_open': len(claims.filtered(lambda c: c.state in ('reported', 'in_process', 'approved'))),
            'claims_paid': sum(claims.mapped('amount_paid')), 'claims_claimed': sum(claims.mapped('amount_claimed')),
            'claims_by_state': by_state, 'claims_by_injury': OrderedDict(sorted(by_injury.items(), key=lambda kv: -kv[1])),
        }
        data['findings'] = self._findings(data)
        return data

    @api.model
    def _findings(self, d):
        out = []
        if not d['total']:
            return [_('Todavía no hay asegurados que contar con este filtro.')]
        out.append(_('%(n)s asegurados en %(g)s grupos.', n=d['total'], g=d['group_count']))
        biggest = max(d['sections'], key=lambda s: s['count'])
        if biggest['count']:
            out.append(_('La sección más grande es %(s)s (%(p)s %%).', s=biggest['name'], p=biggest['pct']))
        rows = sorted([r for r in d['groups'] if r['count']], key=lambda r: -r['count'])
        if rows:
            top = [r for r in rows if r['count'] == rows[0]['count']]
            rest = [r for r in rows if r['count'] < rows[0]['count']][:2]
            text = _('Los grupos con mayor número son %(top)s (%(n)s%(each)s)',
                     top=' y '.join(('%s %s' % (r['short'], r['name'].split(' ', 2)[-1] if r['name'].count(' ') >= 2 else ''))
                                    .strip() for r in top), n=rows[0]['count'], each=_(' cada uno') if len(top) > 1 else '')
            if rest:
                text += _(', seguido de %s', ' y '.join('%s (%s)' % (r['short'], r['count']) for r in rest))
            out.append(text + '.')
        if d['state_count'] > 1:
            parts = ', '.join('%s (%s grupo%s)' % (k, len(v['groups']), 's' if len(v['groups']) != 1 else '')
                              for k, v in d['locations'].items())
            out.append(_('Participación en %(n)s estados: %(parts)s.', n=d['state_count'], parts=parts))
        out.append(_('%(h)s hombres y %(m)s mujeres; edad promedio de %(a)s años.', h=d['male'], m=d['female'], a=d['avg_age']))
        if d['draft']:
            out.append(_('%s personas siguen en preinscripción (falta darlas de alta en el seguro).', d['draft']))
        out.append(_('%(p)s %% tiene su certificado de póliza anexado.', p=d['cert_pct']))
        if d['claims']:
            out.append(_('%(n)s siniestros registrados, %(o)s abiertos.', n=d['claims'], o=d['claims_open']))
        return out

    # ------------------------------------------------------------------ armado
    def _rows(self):
        """Acomoda los bloques en renglones: completo = solo; dos de media página = juntos."""
        self.ensure_one()
        rows, pending = [], None
        for block in self.block_ids.sorted('sequence'):
            if block.width == 'full' or block.block_type in ('header', 'kpi_padron', 'kpi_operacion', 'kpi_certificados'):
                if pending:
                    rows.append([pending])
                    pending = None
                rows.append([block])
            elif pending:
                rows.append([pending, block])
                pending = None
            else:
                pending = block
        if pending:
            rows.append([pending])
        return rows

    def _render_values(self, groups, scope_label=None):
        self.ensure_one()
        today = fields.Date.context_today(self)
        data = self._board_data(groups, self.scope)
        return {
            'board': self, 'd': data, 'rows': self._rows(),
            'cut_label': '%s de %s' % (MONTHS_ES[today.month - 1], today.year),
            'today': today, 'scope_label': scope_label,
            'money': lambda v: '$ {:,.0f}'.format(v or 0.0),
            'max_of': lambda values: max([v for v in values] or [0]) or 1,
            'logo': static_data_uri('static/description/icon.png'),
            'print_mode': False,
        }

    def render_html(self, group_ids=None):
        """HTML del tablero (lo usan la plataforma y los portales)."""
        self.ensure_one()
        groups = self.env['rss.dashboard']._scope_groups(group_ids)
        return self.env['ir.qweb']._render('red_seguro_scout.rss_board_body', self._render_values(groups))

    @api.model
    def boards_for(self, audience):
        field = {'platform': 'show_platform', 'advisor': 'show_advisor', 'group': 'show_group'}[audience]
        return self.sudo().search([(field, '=', True)])

    @api.model
    def platform_boards(self):
        return [{'id': b.id, 'name': b.name} for b in self.boards_for('platform')]

    def action_preview(self):
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'rss_dashboard', 'params': {'board_id': self.id},
                'name': self.name}

    def action_print(self, group_ids=None):
        self.ensure_one()
        data = {'group_ids': group_ids} if group_ids else None
        return self.env.ref('red_seguro_scout.action_report_rss_board').report_action(self, data=data)


class RssBoardBlock(models.Model):
    _name = 'rss.board.block'
    _description = 'Bloque de un tablero'
    _order = 'sequence, id'

    board_id = fields.Many2one('rss.board', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    block_type = fields.Selection(BLOCK_TYPES, string='Bloque', required=True, default='table_group')
    title = fields.Char('Título', translate=True, help='Vacío = título sugerido del bloque.')
    width = fields.Selection([('full', 'Ancho completo'), ('half', 'Media página')], string='Ancho',
                             default='half', required=True)
    split_sex = fields.Boolean('Separar por sexo', help='En secciones: Tropa Femenil / Tropa Masculina, etc.')
    body = fields.Text('Texto', help='Solo para el bloque «Texto libre».')

    def display_title(self):
        self.ensure_one()
        return self.title or DEFAULT_TITLES.get(self.block_type) or dict(BLOCK_TYPES).get(self.block_type)

    @api.onchange('block_type')
    def _onchange_block_type(self):
        if self.block_type in ('header', 'kpi_padron', 'kpi_operacion', 'kpi_certificados'):
            self.width = 'full'
