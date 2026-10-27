# -*- coding: utf-8 -*-
"""Página pública de la plataforma, solicitudes de contacto y banners publicitarios en los portales."""
import logging
import random

from markupsafe import Markup, escape

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

PROMO_INTERESTS = [
    ('implementation', 'Implementar la plataforma en mi grupo'),
    ('migration', 'Pasar mis datos (Excel) a la plataforma'),
    ('training', 'Capacitación a jefes y tesorería'),
    ('hosting', 'Hospedaje y respaldos'),
    ('custom', 'Mejoras a la medida'),
    ('demo', 'Solo quiero ver una demostración'),
]
MEMBER_RANGES = [('lt50', 'Menos de 50'), ('50_100', '50 a 100'), ('100_200', '100 a 200'), ('gt200', 'Más de 200')]


class ScoutPromoLead(models.Model):
    _name = 'scout.promo.lead'
    _description = 'Solicitud de información de la plataforma scout'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(string='Nombre', required=True, tracking=True)
    group_name = fields.Char(string='Grupo scout / organización', tracking=True)
    city = fields.Char(string='Ciudad')
    email = fields.Char(string='Correo')
    phone = fields.Char(string='Teléfono / WhatsApp')
    members = fields.Selection(MEMBER_RANGES, string='Miembros aprox.')
    interest_ids = fields.Char(string='Le interesa')
    message = fields.Text(string='Mensaje')
    state = fields.Selection([('new', 'Nueva'), ('contacted', 'Contactada'), ('demo', 'Demostración agendada'),
                              ('won', 'Cliente'), ('lost', 'Descartada')], string='Estado', default='new',
                             tracking=True, required=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    whatsapp_url = fields.Char(compute='_compute_whatsapp_url', string='WhatsApp')

    def _compute_whatsapp_url(self):
        for lead in self:
            digits = ''.join(c for c in (lead.phone or '') if c.isdigit())
            if len(digits) == 10:
                digits = '52' + digits
            lead.whatsapp_url = f'https://wa.me/{digits}' if len(digits) >= 10 else False

    def _scout_notify_owner(self):
        """Avisa por correo a la persona que atiende la plataforma (Datos del grupo → Página de la plataforma)."""
        for lead in self:
            company = lead.company_id.sudo()
            to = (company.scout_promo_email or '').strip()
            interests = dict(PROMO_INTERESTS)
            rows = [
                (_('Nombre'), lead.name), (_('Grupo'), lead.group_name), (_('Ciudad'), lead.city),
                (_('Correo'), lead.email), (_('Teléfono'), lead.phone),
                (_('Miembros'), dict(MEMBER_RANGES).get(lead.members)),
                (_('Le interesa'), ', '.join(interests.get(i, i) for i in (lead.interest_ids or '').split(',') if i)),
                (_('Mensaje'), lead.message),
            ]
            body = Markup('<p>%s</p><table>%s</table>') % (
                _('Nueva solicitud desde la página de la plataforma scout:'),
                Markup('').join(Markup('<tr><td style="padding:2px 12px 2px 0"><b>%s</b></td><td>%s</td></tr>')
                                % (label, value) for label, value in rows if value))
            lead.message_post(body=body, subtype_xmlid='mail.mt_note')
            if not to:
                continue
            try:
                self.env['mail.mail'].sudo().create({
                    'subject': _('Plataforma scout: %(name)s (%(group)s)', name=lead.name,
                                 group=lead.group_name or _('sin grupo')),
                    'body_html': body, 'email_to': to, 'reply_to': lead.email or False,
                    'email_from': company.email_formatted or company.email or to,
                    'auto_delete': True,
                })
            except Exception:  # noqa: BLE001 - la solicitud ya quedó registrada
                _logger.exception('No se pudo preparar el aviso de la solicitud %s', lead.id)

    @api.model
    def _scout_create_from_web(self, post):
        interests = [k for k, _label in PROMO_INTERESTS if post.get(f'interest_{k}')]
        vals = {
            'name': (post.get('name') or '').strip()[:120],
            'group_name': (post.get('group_name') or '').strip()[:160] or False,
            'city': (post.get('city') or '').strip()[:120] or False,
            'email': (post.get('email') or '').strip()[:160] or False,
            'phone': (post.get('phone') or '').strip()[:40] or False,
            'members': post.get('members') if post.get('members') in dict(MEMBER_RANGES) else False,
            'interest_ids': ','.join(interests) or False,
            'message': (post.get('message') or '').strip()[:4000] or False,
        }
        lead = self.sudo().with_context(mail_create_nosubscribe=True).create(vals)
        lead._scout_notify_owner()
        if 'crm.lead' in self.env:
            try:
                with self.env.cr.savepoint():
                    self.env['crm.lead'].sudo().create({
                        'name': _('Plataforma scout: %s', vals['group_name'] or vals['name']),
                        'contact_name': vals['name'], 'partner_name': vals['group_name'],
                        'email_from': vals['email'], 'phone': vals['phone'], 'city': vals['city'],
                        'description': escape(vals['message'] or ''),
                    })
            except Exception:  # noqa: BLE001
                _logger.info('No se creó la oportunidad de CRM', exc_info=True)
        return lead


class ScoutPromoBanner(models.Model):
    _name = 'scout.promo.banner'
    _description = 'Banner publicitario de los portales'
    _order = 'sequence, id'

    name = fields.Char(string='Título', required=True)
    text = fields.Char(string='Texto')
    image = fields.Image(string='Imagen', max_width=1200, max_height=600,
                         help='Opcional. Horizontal, por ejemplo 1200 × 400. Si no hay imagen se muestra con los '
                              'colores del grupo.')
    cta_label = fields.Char(string='Texto del botón', default='Conoce más')
    url = fields.Char(string='Liga', help='Página a la que lleva el botón (puede ser externa: https://...).')
    style = fields.Selection([('navy', 'Azul marino'), ('orange', 'Naranja'), ('green', 'Verde'),
                              ('light', 'Claro')], string='Colores', default='navy', required=True)
    show_leaders = fields.Boolean(string='Portal de jefes', default=True)
    show_families = fields.Boolean(string='Portal de familias')
    date_start = fields.Date(string='Mostrar desde')
    date_end = fields.Date(string='Mostrar hasta')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Empresa', help='Vacío = todas las empresas.')
    sponsor = fields.Char(string='Patrocinador', help='Si es publicidad de un patrocinador, aparece como '
                                                       '"Patrocinado por …".')

    @api.model
    def _scout_pick(self, where='leader'):
        """Un banner vigente para ese portal (se alternan al azar)."""
        today = fields.Date.context_today(self)
        domain = [('show_leaders' if where == 'leader' else 'show_families', '=', True),
                  '|', ('date_start', '=', False), ('date_start', '<=', today),
                  '|', ('date_end', '=', False), ('date_end', '>=', today),
                  '|', ('company_id', '=', False), ('company_id', '=', self.env.company.id)]
        banners = self.sudo().search(domain)
        return random.choice(banners) if banners else self.browse()


class ResCompanyPromo(models.Model):
    _inherit = 'res.company'

    scout_promo_email = fields.Char(string='Correo que recibe las solicitudes',
                                    help='A este correo llega cada solicitud de la página de la plataforma.')
    scout_promo_whatsapp = fields.Char(string='WhatsApp de contacto',
                                       help='Número a 10 dígitos; aparece como botón en la página de la plataforma.')
    scout_promo_brand = fields.Char(string='Quién ofrece la plataforma',
                                    default='Jhuliana Delgado · Consultoría Odoo')
    scout_promo_login = fields.Boolean(string='Entrada con diseño scout', default=True,
                                       help='Da el diseño del grupo a la página de inicio de sesión.')

    scout_app_active = fields.Boolean(string='Se puede instalar como app', default=True,
                                      help='Jefes y familias pueden poner el ícono del grupo en su celular.')
    scout_app_name = fields.Char(string='Nombre de la app', help='Vacío = nombre de la empresa.')
    scout_app_short_name = fields.Char(string='Nombre bajo el ícono', default='Scouts',
                                       help='Máximo 12 letras; es lo que se lee bajo el ícono del celular.')
    scout_app_icon = fields.Image(string='Ícono de la app', max_width=1024, max_height=1024,
                                  help='Cuadrado. Vacío = el logo de la empresa.')

    def _scout_promo_whatsapp_url(self, text=''):
        self.ensure_one()
        digits = ''.join(c for c in (self.sudo().scout_promo_whatsapp or '') if c.isdigit())
        if not digits:
            return False
        if len(digits) == 10:
            digits = '52' + digits
        from urllib.parse import quote
        return f'https://wa.me/{digits}' + (f'?text={quote(text)}' if text else '')
