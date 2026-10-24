# -*- coding: utf-8 -*-
"""Control de rutas de enlace y permanencia.

Cada beneficiario tiene un registro abierto por la sección en la que está: con su fecha de nacimiento y las edades
oficiales de la sección se calculan la fecha de inicio de la ruta de enlace, la fecha oficial de pase (o de partida
en el Clan) y el tiempo que le queda. Un proceso diario revisa los estados, enciende las alertas amarillas y rojas y
avisa a los jefes de la sección actual y de la receptora. Al confirmar el cambio el registro se cierra (historial) y se
abre uno nuevo para la nueva sección. Los cambios nunca ocurren solos por cumplir años.
"""
from dateutil.relativedelta import relativedelta
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

GRACE_MONTHS = 3          # Extensión extraordinaria máxima sobre cada fecha oficial
PASS_NOTICE_DAYS = 30     # Aviso de "se acerca la fecha de pase"
SOON_MONTHS = 3           # "Próximos a comenzar la ruta" / "próximos a su partida"
DEPARTURE_NOTICE_MONTHS = 6

ROUTE_STATES = [
    ('regular', 'Permanencia regular'),
    ('must_start', 'Debe iniciar ruta de enlace'),
    ('in_route', 'En ruta de enlace'),
    ('extension', 'Periodo extraordinario autorizado'),
    ('overdue', 'Ruta o cambio vencido'),
    ('departure_soon', 'Próximo a su partida'),
    ('done', 'Cambio de sección realizado'),
]
ALERTS = [('none', 'Sin alerta'), ('yellow', 'Amarilla'), ('red', 'Roja')]


def span_text(start, end):
    """'2 años 3 meses 5 días' entre dos fechas (años y meses calendario)."""
    if not start or not end:
        return ''
    delta = relativedelta(end, start)
    parts = []
    for value, one, many in ((delta.years, 'año', 'años'), (delta.months, 'mes', 'meses'), (delta.days, 'día', 'días')):
        if value:
            parts.append(f'{value} {one if value == 1 else many}')
    return ' '.join(parts) or '0 días'


def age_text(years, months):
    text = (f'{years} año' if years == 1 else f'{years} años') if years else ''
    if months:
        text += f' {months} mes' if months == 1 else f' {months} meses'
    return text.strip() or '0 meses'


class ScoutSectionRouteConfig(models.Model):
    _inherit = 'scout.section'

    route_age_years = fields.Integer(string='Inicio de ruta: años')
    route_age_months = fields.Integer(string='Inicio de ruta: meses')
    pass_age_years = fields.Integer(string='Pase oficial: años')
    pass_age_months = fields.Integer(string='Pase oficial: meses')
    max_stay_years = fields.Integer(string='Partida: años', help='Clan: edad máxima de permanencia (24 años 6 meses).')
    max_stay_months = fields.Integer(string='Partida: meses')
    next_section_female_id = fields.Many2one('scout.section', string='Sección siguiente (mujeres)',
                                             help='Castores: las niñas pasan a Gacelas y los niños a Lobatos.')
    route_tolerance_days = fields.Integer(
        string='Días para registrar el inicio de ruta', default=30,
        help='Días después de la fecha oficial de inicio de ruta para registrar el inicio antes de la alerta roja. '
             '0 = la alerta roja se enciende al día siguiente de la fecha oficial.')
    route_rule_text = fields.Char(compute='_compute_route_rule_text', string='Regla de la sección')
    gender = fields.Selection([('m', 'Varones'), ('f', 'Mujeres')], string='Sección para',
                              help='Vacío = mixta. Sirve para proponer la sección según la edad y el sexo.')
    promise_label = fields.Char(string='Nombre de la Promesa en la sección',
                                help='Ej. en Tropa: "Promesa (Tercera clase)". Vacío = "Promesa".')

    @api.model
    def _scout_suggest(self, birthdate, gender=None, on_date=None):
        """Sección que le corresponde por edad (y sexo) según las edades oficiales de pase y partida."""
        if not birthdate:
            return self.browse()
        on_date = on_date or fields.Date.context_today(self)
        delta = relativedelta(on_date, birthdate)
        months = delta.years * 12 + delta.months
        candidates = []
        for section in self.search([('is_leadership', '=', False)]):
            _route, pass_, stay = section._route_offsets()
            upper = pass_ or stay
            if not upper or months >= upper:
                continue
            if gender and section.gender and section.gender != gender:
                continue
            candidates.append((upper, section.sequence, section))
        candidates.sort(key=lambda c: (c[0], c[1]))
        return candidates[0][2] if candidates else self.browse()

    @api.constrains('route_age_months', 'pass_age_months', 'max_stay_months')
    def _check_route_months(self):
        for section in self:
            if any(m < 0 or m > 11 for m in (section.route_age_months, section.pass_age_months, section.max_stay_months)):
                raise ValidationError(_('Los meses de las edades oficiales van de 0 a 11.'))

    def _route_offsets(self):
        """(meses de inicio de ruta, meses de pase, meses de partida) desde el nacimiento."""
        self.ensure_one()
        route = self.route_age_years * 12 + self.route_age_months
        pass_ = self.pass_age_years * 12 + self.pass_age_months
        stay = self.max_stay_years * 12 + self.max_stay_months
        return route, pass_, stay

    def _compute_route_rule_text(self):
        for section in self:
            route, pass_, stay = section._route_offsets()
            if pass_:
                section.route_rule_text = _('Ruta a los %(r)s · pase a los %(p)s', r=age_text(*divmod(route, 12)),
                                            p=age_text(*divmod(pass_, 12)))
            elif stay:
                section.route_rule_text = _('Partida a los %s', age_text(*divmod(stay, 12)))
            else:
                section.route_rule_text = False

    @api.model
    def _scout_route_defaults(self):
        """Edades oficiales del Grupo 7 Ethnos (solo donde aún no se configuraron)."""
        ref = lambda x: self.env.ref(f'scout_management.{x}', raise_if_not_found=False)  # noqa: E731
        rules = [
            ('section_castores', (6, 0, 6, 6, 0, 0), 'section_lobatos', 'section_gacelas'),
            ('section_lobatos', (10, 6, 11, 0, 0, 0), 'section_tropa_atlas', None),
            ('section_gacelas', (10, 6, 11, 0, 0, 0), 'section_tropa_hydra', None),
            ('section_tropa_atlas', (16, 6, 17, 0, 0, 0), 'section_clan_rover', None),
            ('section_tropa_hydra', (16, 6, 17, 0, 0, 0), 'section_clan_precursoras', None),
            ('section_clan_rover', (0, 0, 0, 0, 24, 6), None, None),
            ('section_clan_precursoras', (0, 0, 0, 0, 24, 6), None, None),
        ]
        for xid, gender in (('section_lobatos', 'm'), ('section_gacelas', 'f'), ('section_tropa_atlas', 'm'),
                            ('section_tropa_hydra', 'f'), ('section_clan_rover', 'm'),
                            ('section_clan_precursoras', 'f')):
            section = ref(xid)
            if section and not section.gender:
                section.gender = gender
        for xid in ('section_tropa_atlas', 'section_tropa_hydra'):
            section = ref(xid)
            if section and not section.promise_label:
                section.promise_label = 'Promesa (Tercera clase)'
        for xid, ages, nxt, female in rules:
            section = ref(xid)
            if not section or any(section._route_offsets()):
                continue
            ry, rm, py, pm, sy, sm = ages
            vals = {'route_age_years': ry, 'route_age_months': rm, 'pass_age_years': py, 'pass_age_months': pm,
                    'max_stay_years': sy, 'max_stay_months': sm}
            if py:
                vals.update(transition_age=py, transition_notice_months=6)
            if nxt and ref(nxt):
                vals['next_section_id'] = ref(nxt).id
            if female and ref(female):
                vals['next_section_female_id'] = ref(female).id
            section.write(vals)

    def _route_destination(self, gender):
        """Sección a la que pasa según el sexo. Si la sección se divide (Castores → Lobatos / Gacelas) y no se
        sabe el sexo, queda por definir para no mandarla por error a la sección masculina."""
        self.ensure_one()
        if gender == 'f' and self.next_section_female_id:
            return self.next_section_female_id
        if not gender and self.next_section_female_id:
            return self.env['scout.section']
        return self.next_section_id

    def _route_destination_options(self):
        """Secciones a las que puede pasar un beneficiario de esta sección."""
        self.ensure_one()
        return (self.next_section_id | self.next_section_female_id).filtered(lambda s: not s.is_leadership)


class ScoutRoute(models.Model):
    _name = 'scout.route'
    _description = 'Ruta de enlace y permanencia'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'is_open desc, alert_order, date_pass_official, id'
    _rec_name = 'member_id'

    member_id = fields.Many2one('res.partner', string='Beneficiario', required=True, ondelete='cascade', index=True,
                                domain="[('is_scout', '=', True), ('scout_type', '=', 'youth')]")
    member_state = fields.Selection(related='member_id.scout_state', string='Estado del miembro', store=True)
    birthdate = fields.Date(related='member_id.scout_birthdate', string='Fecha de nacimiento')
    section_id = fields.Many2one('scout.section', string='Sección de origen', required=True, index=True)
    destination_id = fields.Many2one('scout.section', string='Sección de destino', tracking=True)
    kind = fields.Selection([('transfer', 'Ruta de enlace y cambio'), ('departure', 'Partida rover / precursora')],
                            string='Movimiento', required=True, default='transfer')
    is_open = fields.Boolean(string='Abierta', default=True, index=True)

    # Fechas oficiales (siempre desde la fecha de nacimiento, en años y meses calendario)
    date_route_official = fields.Date(string='Inicio oficial de ruta', compute='_compute_official', store=True)
    date_pass_official = fields.Date(string='Pase oficial / partida', compute='_compute_official', store=True)
    date_route_max = fields.Date(string='Inicio máximo excepcional', compute='_compute_official', store=True)
    date_pass_max = fields.Date(string='Pase máximo excepcional', compute='_compute_official', store=True)
    # Lo que realmente ocurrió
    date_route_real = fields.Date(string='Inicio real de ruta', tracking=True)
    date_pass_real = fields.Date(string='Cambio real / partida', tracking=True, readonly=True)
    # Excepciones (cada una por separado, máximo 3 meses)
    route_ext_until = fields.Date(string='Inicio de ruta autorizado hasta', tracking=True, readonly=True)
    route_ext_reason = fields.Text(string='Motivo (inicio tardío)', readonly=True)
    route_ext_user_id = fields.Many2one('res.users', string='Autorizó (inicio)', readonly=True)
    route_ext_date = fields.Date(string='Fecha de autorización (inicio)', readonly=True)
    pass_ext_until = fields.Date(string='Pase autorizado hasta', tracking=True, readonly=True)
    pass_ext_reason = fields.Text(string='Motivo (pase tardío)', readonly=True)
    pass_ext_user_id = fields.Many2one('res.users', string='Autorizó (pase)', readonly=True)
    pass_ext_date = fields.Date(string='Fecha de autorización (pase)', readonly=True)
    # Responsables al momento del movimiento
    origin_leaders = fields.Char(string='Jefes de la sección de origen', readonly=True)
    destination_leaders = fields.Char(string='Jefes de la sección receptora', readonly=True)
    note = fields.Text(string='Observaciones')

    # Estado (lo recalcula el proceso diario y cada cambio)
    state = fields.Selection(ROUTE_STATES, string='Estado de ruta', default='regular', readonly=True, index=True,
                             tracking=True)
    alert = fields.Selection(ALERTS, string='Alerta', default='none', readonly=True, index=True)
    alert_order = fields.Integer(readonly=True, default=2)
    alert_text = fields.Char(string='Aviso', readonly=True)
    route_soon = fields.Boolean(string='Próximo a comenzar ruta', readonly=True, index=True)
    notified_state = fields.Char(readonly=True, copy=False)
    notified_pass_soon = fields.Boolean(readonly=True, copy=False)
    status_date = fields.Date(string='Revisado el', readonly=True)

    # Contador (se calcula al mostrar: siempre al día)
    age_label = fields.Char(string='Edad', compute='_compute_counter')
    time_left = fields.Char(string='Tiempo restante', compute='_compute_counter')
    counter_text = fields.Char(string='Permanencia', compute='_compute_counter')
    section_leader_ids = fields.Many2many('res.users', string='Jefe responsable', related='section_id.leader_ids')
    company_id = fields.Many2one(related='section_id.company_id', store=True)

    # ------------------------------------------------------------------ fechas oficiales
    @api.depends('member_id.scout_birthdate', 'section_id.route_age_years', 'section_id.route_age_months',
                 'section_id.pass_age_years', 'section_id.pass_age_months', 'section_id.max_stay_years',
                 'section_id.max_stay_months', 'kind')
    def _compute_official(self):
        for route in self:
            birth = route.member_id.scout_birthdate
            r_months, p_months, s_months = route.section_id._route_offsets() if route.section_id else (0, 0, 0)
            vals = dict(date_route_official=False, date_pass_official=False, date_route_max=False, date_pass_max=False)
            if birth and route.kind == 'transfer' and p_months:
                vals['date_pass_official'] = birth + relativedelta(months=p_months)
                vals['date_pass_max'] = vals['date_pass_official'] + relativedelta(months=GRACE_MONTHS)
                if r_months:
                    vals['date_route_official'] = birth + relativedelta(months=r_months)
                    vals['date_route_max'] = vals['date_route_official'] + relativedelta(months=GRACE_MONTHS)
            elif birth and route.kind == 'departure' and s_months:
                vals['date_pass_official'] = birth + relativedelta(months=s_months)
            route.update(vals)

    def _today(self):
        return fields.Date.context_today(self)

    @api.depends('date_pass_official', 'date_pass_real', 'pass_ext_until', 'state', 'member_id.scout_birthdate')
    def _compute_counter(self):
        today = self._today()
        for route in self:
            birth = route.member_id.scout_birthdate
            age = relativedelta(today, birth) if birth else None
            route.age_label = age_text(age.years, age.months) if age else ''
            target = route.date_pass_official
            route.time_left = route.counter_text = ''
            if route.date_pass_real:
                route.time_left = _('Concluido')
                route.counter_text = _('%(what)s el %(date)s', what=_('Partida') if route.kind == 'departure'
                                       else _('Cambio realizado'), date=route.date_pass_real.strftime('%d/%m/%Y'))
                continue
            if not target:
                route.time_left = '—'
                route.counter_text = _('Falta fecha de nacimiento') if not birth else _('Sin edad oficial configurada')
                continue
            if today < target:
                left = span_text(today, target)
                route.time_left = left + (_(' para partida') if route.kind == 'departure' else '')
                route.counter_text = _('Le quedan %(left)s en %(section)s (hasta el %(date)s)', left=left,
                                       section=route.section_id.name, date=target.strftime('%d/%m/%Y'))
            elif today == target:
                route.time_left = _('Hoy corresponde')
                route.counter_text = _('Hoy corresponde su %s', _('partida') if route.kind == 'departure' else _('cambio de sección'))
            else:
                route.time_left = _('Vencido hace %s', span_text(target, today))
                text = _('El %(what)s correspondía el %(date)s (hace %(ago)s)',
                         what=_('partida') if route.kind == 'departure' else _('cambio'),
                         date=target.strftime('%d/%m/%Y'), ago=span_text(target, today))
                if route.pass_ext_until and today <= route.pass_ext_until:
                    text += _('; quedan %s del plazo extraordinario', span_text(today, route.pass_ext_until))
                route.counter_text = text

    # ------------------------------------------------------------------ estado y alertas
    def _scout_status(self, today):
        """(estado, alerta, aviso, próximo a comenzar) para hoy."""
        self.ensure_one()
        dest = self.destination_id.name or _('la siguiente sección')
        if self.date_pass_real or not self.is_open:
            return 'done', 'none', '', False
        if not self.date_pass_official:
            return 'regular', 'none', '', False
        if self.kind == 'departure':
            if today >= self.date_pass_official:
                return 'overdue', 'red', _('ALERTA: EDAD MÁXIMA DE PERMANENCIA EN CLAN'), False
            if today >= self.date_pass_official - relativedelta(months=DEPARTURE_NOTICE_MONTHS):
                return 'departure_soon', 'yellow', _('Partida el %(date)s (quedan %(left)s)',
                                                     date=self.date_pass_official.strftime('%d/%m/%Y'),
                                                     left=span_text(today, self.date_pass_official)), False
            return 'regular', 'none', '', False
        pass_deadline = self.pass_ext_until or self.date_pass_official
        when = _('cambio a %(dest)s el %(date)s', dest=dest, date=self.date_pass_official.strftime('%d/%m/%Y'))
        # Cambio de sección
        if today > pass_deadline:
            return 'overdue', 'red', _('ALERTA: CAMBIO DE SECCIÓN VENCIDO (%s)', when), False
        # Inicio de ruta
        route_start = self.date_route_official
        if route_start and not self.date_route_real:
            route_deadline = self.route_ext_until or (
                route_start + relativedelta(days=self.section_id.route_tolerance_days or 0))
            if today > route_deadline:
                return 'overdue', 'red', _('ALERTA: INICIO DE RUTA VENCIDO (%s)', when), False
            if self.route_ext_until and today > route_start:
                return 'extension', 'yellow', _(
                    'DEBE INICIAR RUTA DE ENLACE · plazo extraordinario hasta el %(date)s (quedan %(left)s)',
                    date=self.route_ext_until.strftime('%d/%m/%Y'), left=span_text(today, self.route_ext_until)), False
            if today >= route_start:
                return 'must_start', 'yellow', _('DEBE INICIAR RUTA DE ENLACE · %s', when), False
            soon = today >= route_start - relativedelta(months=SOON_MONTHS)
            return 'regular', 'none', '', soon
        if self.pass_ext_until and today > self.date_pass_official:
            return 'extension', 'yellow', _('EN RUTA DE ENLACE · plazo extraordinario de pase hasta el %(date)s '
                                            '(quedan %(left)s)', date=self.pass_ext_until.strftime('%d/%m/%Y'),
                                            left=span_text(today, self.pass_ext_until)), False
        if self.date_route_real:
            return 'in_route', 'yellow', _('EN RUTA DE ENLACE · %(when)s (quedan %(left)s)', when=when,
                                           left=span_text(today, self.date_pass_official)), False
        return 'regular', 'none', '', False

    def _scout_refresh_status(self, notify=True):
        today = self._today()
        for route in self:
            state, alert, text, soon = route._scout_status(today)
            vals = {}
            if (route.state, route.alert, route.alert_text or '', route.route_soon) != (state, alert, text or '', soon):
                vals.update(state=state, alert=alert, alert_text=text or False, route_soon=soon,
                            alert_order={'red': 0, 'yellow': 1}.get(alert, 2))
            if route.status_date != today:
                vals['status_date'] = today
            if vals:
                super(ScoutRoute, route).write(vals)
            if notify and route.is_open:
                route._scout_route_notifications(today)

    # ------------------------------------------------------------------ avisos a los jefes
    def _leader_partners(self, sections):
        return sections.sudo().leader_ids.filtered('active').partner_id

    def _scout_route_notify(self, title, managers=False):
        """Aviso a jefes de la sección actual y de la receptora (y a la jefatura si se pide)."""
        for route in self.sudo():
            member = route.member_id
            today = route._today()
            age = relativedelta(today, member.scout_birthdate) if member.scout_birthdate else None
            rows = [
                (_('Beneficiario'), member.name),
                (_('Fecha de nacimiento'), member.scout_birthdate and '%s (%s)' % (
                    member.scout_birthdate.strftime('%d/%m/%Y'), age_text(age.years, age.months))),
                (_('Sección de origen'), route.section_id.name),
                (_('Sección de destino'), route.destination_id.name or ('—' if route.kind == 'transfer' else _('Partida'))),
                (_('Inicio de ruta'), (route.date_route_real or route.date_route_official) and '%s%s' % (
                    (route.date_route_real or route.date_route_official).strftime('%d/%m/%Y'),
                    '' if route.date_route_real else _(' (oficial)'))),
                (_('Fecha oficial de pase') if route.kind == 'transfer' else _('Fecha de partida'),
                 route.date_pass_official and route.date_pass_official.strftime('%d/%m/%Y')),
                (_('Tiempo restante'), route.time_left),
                (_('Estado'), dict(ROUTE_STATES).get(route.state)),
                (_('Alerta'), route.alert_text),
                (_('Excepción autorizada'), '; '.join(filter(None, [
                    route.route_ext_until and _('inicio hasta %s', route.route_ext_until.strftime('%d/%m/%Y')),
                    route.pass_ext_until and _('pase hasta %s', route.pass_ext_until.strftime('%d/%m/%Y'))]))),
            ]
            body = Markup('<b>%s</b><ul>') % title
            for label, value in rows:
                if value:
                    body += Markup('<li>%s: %s</li>') % (label, value)
            body += Markup('</ul>')
            partners = self._leader_partners(route.section_id | route.destination_id)
            if managers:
                partners |= self.env['scout.approval.mixin']._scout_manager_users().partner_id
            route.message_post(body=body, partner_ids=partners.ids, subtype_xmlid='mail.mt_note')
            member.message_post(body=body, subtype_xmlid='mail.mt_note')

    def _scout_route_notifications(self, today):
        self.ensure_one()
        key = f'{self.state}:{self.alert}'
        if key != (self.notified_state or ''):
            title = {
                'must_start': _('Corresponde iniciar ruta de enlace: %s', self.member_id.name),
                'in_route': _('Inició la ruta de enlace: %s', self.member_id.name),
                'extension': _('Periodo extraordinario autorizado: %s', self.member_id.name),
                'overdue': self.alert_text and '%s: %s' % (self.alert_text.split(' (')[0], self.member_id.name),
                'departure_soon': _('Se acerca la partida de %s', self.member_id.name),
            }.get(self.state)
            if title:
                self._scout_route_notify(title, managers=self.state == 'overdue')
                if self.state == 'overdue':
                    todo = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
                    for user in self.env['scout.approval.mixin']._scout_manager_users()[:3]:
                        self.sudo().activity_schedule(activity_type_id=todo.id if todo else False,
                                                      summary=self.alert_text, user_id=user.id)
            super(ScoutRoute, self).write({'notified_state': key})
        if self.kind == 'transfer' and not self.notified_pass_soon and self.date_pass_official \
                and today <= self.date_pass_official <= today + relativedelta(days=PASS_NOTICE_DAYS):
            self._scout_route_notify(_('Se acerca la fecha oficial de pase de %(name)s (%(date)s)',
                                       name=self.member_id.name, date=self.date_pass_official.strftime('%d/%m/%Y')))
            super(ScoutRoute, self).write({'notified_pass_soon': True})

    # ------------------------------------------------------------------ alta y cambios
    @api.model_create_multi
    def create(self, vals_list):
        routes = super().create(vals_list)
        routes._scout_refresh_status(notify=False)
        return routes

    def write(self, vals):
        protected = {'route_ext_until', 'route_ext_reason', 'pass_ext_until', 'pass_ext_reason', 'date_pass_real',
                     'is_open'} & set(vals)
        if protected and not self.env.context.get('scout_route_system') \
                and not self.env['scout.approval.mixin']._scout_is_manager():
            raise AccessError(_('Las excepciones y la confirmación del cambio las registra la jefatura de grupo.'))
        res = super().write(vals)
        if not self.env.context.get('scout_route_no_refresh'):
            self._scout_refresh_status()
        return res

    # ------------------------------------------------------------------ acciones
    def action_start_route(self, date=None):
        """Registra el inicio efectivo de la ruta de enlace (lo puede hacer el jefe de sección)."""
        for route in self:
            if route.kind != 'transfer' or not route.is_open:
                raise UserError(_('Solo se inicia ruta de enlace en un cambio de sección abierto.'))
            if route.date_route_real:
                continue
            day = fields.Date.to_date(date) if date else route._today()
            if route.date_pass_official and day > (route.pass_ext_until or route.date_pass_official):
                raise UserError(_('La fecha de inicio es posterior a la fecha límite de pase.'))
            route.with_context(scout_route_system=True).write({'date_route_real': day})
            route.member_id.sudo().with_context(scout_change_approved=True).write(
                {'scout_transition_date': route.member_id.scout_transition_date or day})
        return True

    def action_open_extension(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Autorizar periodo extraordinario'),
                'res_model': 'scout.route.extension.wizard', 'view_mode': 'form', 'target': 'new',
                'context': {'default_route_id': self.id,
                            'default_ext_type': 'route' if not self.date_route_real and self.date_route_official
                            else 'pass'}}

    def action_open_confirm(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window',
                'name': _('Registrar partida') if self.kind == 'departure' else _('Confirmar cambio de sección'),
                'res_model': 'scout.route.confirm.wizard', 'view_mode': 'form', 'target': 'new',
                'context': {'default_route_id': self.id, 'default_destination_id': self.destination_id.id}}

    def action_bulk_pass(self):
        """Da el pase de sección (hoy) a los seleccionados, cada uno a su sección de destino."""
        today = self._today()
        done, missing = self.env['scout.route'], []
        for route in self.filtered(lambda r: r.is_open and r.kind == 'transfer'):
            if not route.destination_id:
                missing.append(route.member_id.name)
                continue
            route._confirm(today)
            done |= route
        msg = _('%s beneficiario(s) pasaron de sección.', len(done))
        if missing:
            msg += ' ' + _('Sin destino (indica si es niño o niña): %s', ', '.join(missing))
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Pase de sección'), 'message': msg, 'sticky': bool(missing),
                           'type': 'warning' if missing else 'success',
                           'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    def _authorize_extension(self, ext_type, until, reason):
        self.ensure_one()
        self.env['scout.approval.mixin']._scout_check_manager(_('autorizar periodos extraordinarios'))
        official = self.date_route_official if ext_type == 'route' else self.date_pass_official
        if self.kind != 'transfer' or not official:
            raise UserError(_('Este movimiento no admite periodo extraordinario.'))
        if not reason or not reason.strip():
            raise UserError(_('Escribe el motivo de la excepción.'))
        limit = official + relativedelta(months=GRACE_MONTHS)
        if until <= official or until > limit:
            raise ValidationError(_('La fecha autorizada debe estar entre el %(a)s y el %(b)s (máximo 3 meses).',
                                    a=(official + relativedelta(days=1)).strftime('%d/%m/%Y'),
                                    b=limit.strftime('%d/%m/%Y')))
        prefix = 'route' if ext_type == 'route' else 'pass'
        self.with_context(scout_route_system=True).write({
            f'{prefix}_ext_until': until, f'{prefix}_ext_reason': reason.strip(),
            f'{prefix}_ext_user_id': self.env.uid, f'{prefix}_ext_date': self._today()})
        what = _('inicio de ruta') if ext_type == 'route' else _('pase')
        self._scout_route_notify(_('Periodo extraordinario autorizado (%(what)s) para %(name)s hasta el %(date)s. '
                                   'Motivo: %(reason)s', what=what, name=self.member_id.name,
                                   date=until.strftime('%d/%m/%Y'), reason=reason.strip()))

    def _confirm(self, day, destination=None, note=None):
        """Confirma el cambio (o la partida): cierra este registro y abre el de la nueva sección."""
        self.ensure_one()
        if not self.env.context.get('scout_route_receiver'):
            self.env['scout.approval.mixin']._scout_check_manager(_('confirmar cambios de sección'))
        if not self.is_open:
            raise UserError(_('Este movimiento ya está cerrado.'))
        member = self.member_id.sudo()
        vals = {'date_pass_real': day, 'is_open': False,
                'origin_leaders': ', '.join(self._leader_partners(self.section_id).mapped('name')) or False}
        if note:
            vals['note'] = ((self.note or '') + '\n' + note).strip()
        if self.kind == 'transfer':
            destination = destination or self.destination_id
            if not destination:
                raise UserError(_('Indica la sección de destino.'))
            gender = destination.gender if destination.gender in ('m', 'f') else False
            if gender and member.scout_gender and member.scout_gender != gender \
                    and destination in self.section_id._route_destination_options():
                raise UserError(_('%(name)s está registrado(a) como %(sex)s y %(dest)s es una sección %(dsex)s. '
                                  'Corrige el sexo en su ficha o elige la otra sección.', name=member.name,
                                  sex=dict(member._fields['scout_gender'].selection).get(member.scout_gender),
                                  dest=destination.name, dsex=_('femenina') if gender == 'f' else _('masculina')))
            if gender and not member.scout_gender:
                member.with_context(scout_change_approved=True, scout_route_closing=True).write(
                    {'scout_gender': gender})
            vals.update(destination_id=destination.id,
                        destination_leaders=', '.join(self._leader_partners(destination).mapped('name')) or False)
        self.with_context(scout_route_system=True, scout_route_no_refresh=True).write(vals)
        self._scout_refresh_status(notify=False)
        if self.kind == 'transfer':
            self._scout_route_notify(_('Cambio de sección registrado: %(name)s pasa a %(dest)s el %(date)s',
                                       name=member.name, dest=destination.name, date=day.strftime('%d/%m/%Y')),
                                     managers=True)
            member.with_context(scout_change_approved=True, scout_route_closing=True).write({
                'scout_section_id': destination.id, 'scout_patrol_id': False, 'scout_transition_date': False})
            member._scout_ensure_route()
        else:
            self._scout_route_notify(_('Partida registrada: %(name)s el %(date)s', name=member.name,
                                       date=day.strftime('%d/%m/%Y')), managers=True)
        self.activity_ids.action_feedback(feedback=_('Movimiento registrado'))
        return True

    # ------------------------------------------------------------------ proceso diario
    @api.model
    def _cron_refresh(self):
        members = self.env['res.partner'].sudo().search([
            ('is_scout', '=', True), ('scout_type', '=', 'youth'), ('scout_state', 'in', ('trial', 'active')),
            ('scout_section_id', '!=', False)])
        members._scout_ensure_route()
        self.sudo().search([('is_open', '=', True), ('member_state', 'in', ('trial', 'active'))])._scout_refresh_status()
        return True


class ScoutRouteExtensionWizard(models.TransientModel):
    _name = 'scout.route.extension.wizard'
    _description = 'Autorizar periodo extraordinario'

    route_id = fields.Many2one('scout.route', required=True, readonly=True)
    member_id = fields.Many2one(related='route_id.member_id')
    ext_type = fields.Selection([('route', 'Inicio de ruta de enlace'), ('pass', 'Cambio de sección (pase)')],
                                string='¿Qué se extiende?', required=True)
    official_date = fields.Date(string='Fecha oficial', compute='_compute_limits')
    max_date = fields.Date(string='Máximo permitido', compute='_compute_limits')
    until = fields.Date(string='Autorizado hasta', required=True)
    reason = fields.Text(string='Motivo', required=True)

    @api.depends('route_id', 'ext_type')
    def _compute_limits(self):
        for wiz in self:
            official = wiz.route_id.date_route_official if wiz.ext_type == 'route' else wiz.route_id.date_pass_official
            wiz.official_date = official
            wiz.max_date = official + relativedelta(months=GRACE_MONTHS) if official else False

    @api.onchange('ext_type')
    def _onchange_ext_type(self):
        self.until = self.max_date

    def action_confirm(self):
        self.ensure_one()
        self.route_id._authorize_extension(self.ext_type, self.until, self.reason)
        return {'type': 'ir.actions.act_window_close'}


class ScoutRouteConfirmWizard(models.TransientModel):
    _name = 'scout.route.confirm.wizard'
    _description = 'Confirmar cambio de sección o partida'

    route_id = fields.Many2one('scout.route', required=True, readonly=True)
    kind = fields.Selection(related='route_id.kind')
    member_id = fields.Many2one(related='route_id.member_id')
    date = fields.Date(string='Fecha real', required=True, default=fields.Date.context_today)
    destination_id = fields.Many2one('scout.section', string='Sección de destino')
    note = fields.Text(string='Observaciones')

    def action_confirm(self):
        self.ensure_one()
        self.route_id._confirm(self.date, self.destination_id, self.note)
        return {'type': 'ir.actions.act_window_close'}


class ResPartnerRoute(models.Model):
    _inherit = 'res.partner'

    scout_route_ids = fields.One2many('scout.route', 'member_id', string='Rutas de enlace y permanencia')
    scout_route_id = fields.Many2one('scout.route', string='Ruta actual', compute='_compute_scout_route')
    scout_route_state = fields.Selection(ROUTE_STATES, string='Estado de ruta', compute='_compute_scout_route')
    scout_route_alert = fields.Selection(ALERTS, string='Alerta de ruta', compute='_compute_scout_route')
    scout_time_left = fields.Char(string='Tiempo restante en la sección', compute='_compute_scout_route')
    scout_route_destination_id = fields.Many2one('scout.section', string='Próximo destino',
                                                 compute='_compute_scout_route')

    def _compute_scout_route(self):
        for partner in self:
            route = partner.sudo().scout_route_ids.filtered(
                lambda r: r.is_open and r.section_id == partner.scout_section_id)[:1]
            partner.scout_route_id = route
            partner.scout_route_state = route.state if route else False
            partner.scout_route_alert = route.alert if route else False
            partner.scout_time_left = route.time_left if route else False
            partner.scout_route_destination_id = route.destination_id if route else False

    def _scout_ensure_route(self):
        """Cada beneficiario activo tiene abierto el registro de su sección actual."""
        Route = self.env['scout.route'].sudo()
        for partner in self.sudo().filtered(lambda p: p.is_scout and p.scout_type == 'youth' and p.scout_section_id
                                            and not p.scout_section_id.is_leadership):
            section = partner.scout_section_id
            open_routes = partner.scout_route_ids.filtered('is_open')
            current = open_routes.filtered(lambda r: r.section_id == section)
            # Si cambió de sección por otro camino (solicitud aprobada), se cierra el registro anterior
            for old in open_routes - current:
                old.with_context(scout_route_system=True, scout_route_no_refresh=True).write({
                    'is_open': False, 'date_pass_real': fields.Date.context_today(partner),
                    'destination_id': section.id if old.kind == 'transfer' else old.destination_id.id,
                    'note': ((old.note or '') + '\n' + _('Cambio registrado desde la ficha del miembro.')).strip()})
                old._scout_refresh_status(notify=False)
            _route, _pass, stay = section._route_offsets()
            kind = 'departure' if stay and not _pass else 'transfer'
            destination = section._route_destination(partner.scout_gender) if kind == 'transfer' else False
            if not current:
                Route.create({'member_id': partner.id, 'section_id': section.id, 'kind': kind,
                              'destination_id': destination.id if destination else False,
                              'date_route_real': partner.scout_transition_date or False})
            else:
                vals = {}
                if current.kind != kind:
                    vals['kind'] = kind
                auto = section._route_destination_options()
                if kind == 'transfer' and not current.date_pass_real and current.destination_id != destination \
                        and (not current.destination_id or current.destination_id in auto):
                    vals['destination_id'] = destination.id if destination else False
                if vals:
                    current.with_context(scout_route_system=True).write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        partners._scout_ensure_route()
        return partners

    def write(self, vals):
        res = super().write(vals)
        if {'scout_section_id', 'scout_birthdate', 'scout_gender', 'is_scout', 'scout_type', 'scout_state'} & set(vals) \
                and not self.env.context.get('scout_route_closing'):
            self._scout_ensure_route()
            self.sudo().scout_route_ids.filtered('is_open')._scout_refresh_status()
        return res
