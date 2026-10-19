# -*- coding: utf-8 -*-
"""Herramientas del miembro: alerta de edad, alerta de registro para nuevos, equipos, cargo y credencial."""
import secrets

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .scout_common import ATTENDED_STATUSES

# A partir de cuántas asistencias un nuevo ingreso ya debe registrarse y pagar.
TRIAL_ATTENDANCE_LIMIT = 2
PATROL_ROLE_POSITIONS = {
    'guide': 'scout_management.position_guide',
    'subguide': 'scout_management.position_subguide',
    'member': 'scout_management.position_member',
}


class ResPartnerScoutTools(models.Model):
    _inherit = 'res.partner'

    scout_age_alert = fields.Selection([
        ('under', 'Menor a la edad de la sección'),
        ('soon', 'Ruta de enlace (alerta amarilla)'),
        ('over', 'Plazo vencido (alerta roja)'),
    ], string='Alerta de edad', compute='_compute_scout_age_alert')
    scout_age_alert_text = fields.Char(string='Aviso de edad', compute='_compute_scout_age_alert')
    scout_trial_attendance = fields.Integer(
        string='Asistencias sin registro', compute='_compute_scout_registration_alert',
        help='Nuevos ingresos (en prueba): asistencias mientras no tenga su registro del ciclo pagado.')
    scout_needs_registration = fields.Boolean(
        string='Debe registrarse', compute='_compute_scout_registration_alert',
        help=f'Lleva {TRIAL_ATTENDANCE_LIMIT} o más asistencias y su registro aún no está pagado.')
    scout_reg_alert_date = fields.Date(string='Aviso de registro enviado', copy=False,
                                       help='Fecha en que se avisó a los jefes y a tesorería que debe registrarse.')
    scout_card_token = fields.Char(string='Clave de la credencial', copy=False, groups='base.group_system')
    scout_form_token = fields.Char(string='Clave de la liga de ficha', copy=False, groups='base.group_system')

    # ------------------------------------------------------------------ edad
    def _compute_scout_age_alert(self):
        """Alerta de la ruta de enlace (roja / amarilla); si no hay, avisa si es menor a la edad de la sección."""
        today = fields.Date.context_today(self)
        for partner in self:
            route = partner.scout_route_id
            alert, text = False, False
            if route and route.alert == 'red':
                alert, text = 'over', route.alert_text
            elif route and route.alert == 'yellow':
                alert, text = 'soon', route.alert_text
            elif partner.is_scout and partner.scout_type == 'youth' and partner.scout_birthdate \
                    and partner.scout_section_id.age_min and not partner.scout_section_id.is_leadership:
                age = relativedelta(today, partner.scout_birthdate).years
                if age < partner.scout_section_id.age_min:
                    alert = 'under'
                    text = _('Tiene %(age)s años; la sección empieza a los %(min)s.', age=age,
                             min=partner.scout_section_id.age_min)
            partner.scout_age_alert = alert
            partner.scout_age_alert_text = text

    # ------------------------------------------------------------------ nuevos ingresos
    def _compute_scout_registration_alert(self):
        youth = self.filtered(lambda p: p.is_scout and p.scout_type == 'youth' and p.id)
        (self - youth).update({'scout_trial_attendance': 0, 'scout_needs_registration': False})
        if not youth:
            return
        pending = youth.filtered(lambda p: p.scout_state == 'trial'
                                 and p.scout_registration_status not in ('paid', 'exempt'))
        (youth - pending).update({'scout_trial_attendance': 0, 'scout_needs_registration': False})
        if not pending:
            return
        cycle = self.env['scout.cycle'].sudo()._get_current()
        domain = [('member_id', 'in', pending.ids), ('status', 'in', ATTENDED_STATUSES)]
        if cycle:
            domain += [('date', '>=', cycle.date_start), ('date', '<=', cycle.date_end)]
        counts = dict(self.env['scout.attendance.line'].sudo()._read_group(domain, ['member_id'], ['__count']))
        for partner in pending:
            count = counts.get(partner, 0)
            partner.scout_trial_attendance = count
            partner.scout_needs_registration = count >= TRIAL_ATTENDANCE_LIMIT

    def _scout_registration_alert_text(self):
        self.ensure_one()
        return _('%(name)s lleva %(count)s asistencias sin registro: ya debe registrarse y pagar su cuota.',
                 name=self.name, count=self.scout_trial_attendance)

    def _scout_notify_registration_due(self):
        """Avisa una sola vez a los jefes de su sección y a tesorería que el nuevo ya debe registrarse."""
        treasurers = self.env.ref('scout_management.group_scout_treasurer').sudo().all_user_ids.filtered(
            lambda u: u.active and not u.share and u.id != 1)
        todo = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        notified = self.browse()
        for partner in self.sudo().filtered(lambda p: p.scout_needs_registration and not p.scout_reg_alert_date):
            text = partner._scout_registration_alert_text()
            leaders = partner.scout_section_id.leader_ids.partner_id
            partner.message_post(
                body=Markup('<b>%s</b><br/>%s') % (_('Nuevo ingreso: registro pendiente'), text),
                partner_ids=(leaders | treasurers.partner_id).ids, subtype_xmlid='mail.mt_comment',
                message_type='comment')
            for user in treasurers[:3]:
                partner.activity_schedule(activity_type_id=todo.id if todo else False,
                                          summary=_('Cobrar registro de nuevo ingreso'), note=text, user_id=user.id)
            partner.scout_reg_alert_date = fields.Date.context_today(partner)
            notified |= partner
        return notified

    # ------------------------------------------------------------------ equipos y cargo
    def _scout_set_patrol(self, patrol):
        """Mueve al miembro de equipo; si era guía o subguía, deja el cargo en el equipo anterior."""
        self.ensure_one()
        if patrol and patrol.section_id != self.scout_section_id:
            raise UserError(_('Ese equipo es de otra sección.'))
        vals = {'scout_patrol_id': patrol.id if patrol else False}
        if self.scout_position_id.kind in ('guide', 'subguide') and patrol != self.scout_patrol_id:
            member_position = self.env.ref(PATROL_ROLE_POSITIONS['member'], raise_if_not_found=False)
            vals['scout_position_id'] = member_position.id if member_position else False
        self.sudo().write(vals)

    def _scout_set_patrol_role(self, kind):
        """Guía, subguía o integrante. Solo hay un guía y un subguía por equipo: el anterior pasa a integrante."""
        self.ensure_one()
        if kind not in PATROL_ROLE_POSITIONS:
            raise UserError(_('Cargo no válido.'))
        if kind != 'member' and not self.scout_patrol_id:
            raise UserError(_('Primero pon a %s en un equipo.', self.name))
        position = self.env.ref(PATROL_ROLE_POSITIONS[kind], raise_if_not_found=False)
        member_position = self.env.ref(PATROL_ROLE_POSITIONS['member'], raise_if_not_found=False)
        if not position:
            raise UserError(_('Falta el cargo en el catálogo de roles.'))
        if kind != 'member':
            previous = self.sudo().search([('scout_patrol_id', '=', self.scout_patrol_id.id), ('id', '!=', self.id),
                                           ('scout_position_id.kind', '=', kind)])
            previous.write({'scout_position_id': member_position.id if member_position else False})
        self.sudo().write({'scout_position_id': position.id})

    # ------------------------------------------------------------------ credencial
    def _scout_card_token(self):
        self.ensure_one()
        partner = self.sudo()
        if not partner.scout_card_token:
            partner.scout_card_token = secrets.token_urlsafe(16)
        return partner.scout_card_token

    def _scout_card_url(self):
        self.ensure_one()
        return f'{self.get_base_url().rstrip("/")}/scout/credencial/{self.id}/{self._scout_card_token()}'

    def _scout_card_qr(self):
        from .scout_online_payment import scout_qr_data_uri
        return scout_qr_data_uri(self._scout_card_url())

    def _scout_card_valid(self):
        """Registro del ciclo pagado o exento (o dirigente activo)."""
        self.ensure_one()
        if self.scout_state == 'inactive':
            return False
        return self.scout_type == 'leader' or self.scout_registration_status in ('paid', 'exempt')

    # ------------------------------------------------------------------ ligas para que la familia llene la ficha
    def _scout_form_url(self):
        """Liga personal (sin contraseña) para que la familia renueve o complete la ficha de este miembro."""
        self.ensure_one()
        partner = self.sudo()
        if not partner.scout_form_token:
            partner.scout_form_token = secrets.token_urlsafe(16)
        return f'{self.get_base_url().rstrip("/")}/scout/ficha/{self.id}/{partner.scout_form_token}'

    @api.model
    def _scout_signup_token(self):
        params = self.env['ir.config_parameter'].sudo()
        token = params.get_param('scout_management.signup_token')
        if not token:
            token = secrets.token_urlsafe(12)
            params.set_param('scout_management.signup_token', token)
        return token

    @api.model
    def _scout_signup_url(self, section=None):
        """Liga general de inscripción para nuevos (la sección se propone por edad y sexo)."""
        url = f'{self.get_base_url().rstrip("/")}/scout/inscripcion/{self._scout_signup_token()}'
        return url + (f'?seccion={section.id}' if section else '')

    def action_scout_form_link(self):
        self.ensure_one()
        return self.env['scout.form.link.wizard'].create({'member_id': self.id})._open()

    def action_scout_print_card(self):
        return self.env.ref('scout_management.action_report_member_card').report_action(self)


class ScoutFormLinkWizard(models.TransientModel):
    _name = 'scout.form.link.wizard'
    _description = 'Liga para que la familia llene la ficha'

    member_id = fields.Many2one('res.partner', string='Miembro', readonly=True)
    section_id = fields.Many2one('scout.section', string='Sección (opcional)',
                                 help='Solo para la liga general de nuevos: deja vacío para que se proponga por edad.')
    link = fields.Char(string='Liga', compute='_compute_link')
    qr_code = fields.Binary(string='QR', compute='_compute_link')
    phone = fields.Char(string='WhatsApp')

    @api.depends('member_id', 'section_id')
    def _compute_link(self):
        import base64
        from .scout_online_payment import scout_qr_png
        for wiz in self:
            link = wiz.member_id._scout_form_url() if wiz.member_id else \
                self.env['res.partner']._scout_signup_url(wiz.section_id or None)
            wiz.link = link
            wiz.qr_code = base64.b64encode(scout_qr_png(link))

    @api.model_create_multi
    def create(self, vals_list):
        wizards = super().create(vals_list)
        for wiz in wizards.filtered('member_id'):
            wiz.phone = wiz.member_id.scout_guardian_id.phone
        return wizards

    def _open(self):
        return {'type': 'ir.actions.act_window', 'name': _('Liga de ficha de registro'), 'res_model': self._name,
                'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    def action_whatsapp(self):
        self.ensure_one()
        from urllib.parse import quote
        from .scout_online_payment import scout_whatsapp_phone
        phone = scout_whatsapp_phone(self.phone)
        if self.member_id:
            text = _('Hola, esta es la liga para llenar (o renovar) la ficha de registro de %(name)s en %(group)s. '
                     'Ahí mismo firmas, adjuntas tu INE y eliges pagar en línea o en efectivo: %(link)s',
                     name=self.member_id.name, group=self.env.company.name, link=self.link)
        else:
            text = _('Hola, esta es la liga para inscribir a tu hijo(a) en %(group)s. Llena la ficha, firma, adjunta tu '
                     'INE y elige pagar en línea o en efectivo: %(link)s', group=self.env.company.name, link=self.link)
        return {'type': 'ir.actions.act_url', 'target': 'new',
                'url': f'https://wa.me/{phone}?text={quote(text)}' if phone else f'https://wa.me/?text={quote(text)}'}
