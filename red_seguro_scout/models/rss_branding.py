# -*- coding: utf-8 -*-
"""Identidad visual: Red Seguro Scout + la personalidad de cada grupo.

Cuando un grupo (u organización) adquiere el módulo, sus documentos salen con
SU logo, SUS colores y SU lema; Red Seguro Scout queda como sello discreto.

Orden de prioridad para un documento:
    1. Identidad del grupo (si tiene logo o colores capturados).
    2. Identidad de la organización (res.company: logo, color primario/secundario).
    3. Identidad de Red Seguro Scout (azul marino, oro y rojo del emblema).
"""
import base64
import re

from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.misc import file_open

RSS_NAVY = '#0A2A5E'
RSS_GOLD = '#D9A93A'
RSS_RED = '#B00625'
HEX_RE = re.compile(r'^#(?:[0-9a-fA-F]{3}){1,2}$')


def _norm_hex(value):
    value = (value or '').strip()
    if not HEX_RE.match(value):
        return False
    if len(value) == 4:
        value = '#' + ''.join(c * 2 for c in value[1:])
    return value.upper()


def _luminance(hex_color):
    hex_color = _norm_hex(hex_color) or RSS_NAVY
    rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _text_on(hex_color):
    """Blanco o tinta según el contraste del fondo."""
    return '#FFFFFF' if _luminance(hex_color) < 0.45 else '#14213D'


def _tint(hex_color, factor=0.88):
    """Versión muy clara del color (para fondos de tarjetas y filas)."""
    hex_color = _norm_hex(hex_color) or RSS_NAVY
    rgb = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    rgb = [round(c + (255 - c) * factor) for c in rgb]
    return '#%02X%02X%02X' % tuple(rgb)


class RssBrandingMixin(models.AbstractModel):
    _name = 'rss.branding.mixin'
    _description = 'Identidad visual Red Seguro Scout'

    @api.model
    def _rss_emblem_b64(self):
        with file_open('red_seguro_scout/static/src/img/emblema_red_seguro_scout.png', 'rb') as f:
            return base64.b64encode(f.read()).decode()

    @api.model
    def _rss_report_css(self, brand):
        return rss_report_css(brand)

    @api.model
    def _rss_brand_from(self, group=None, company=None):
        """Devuelve el diccionario de identidad para un documento."""
        group = group or self.env['rss.scout.group']
        company = company or (group.company_id if group else self.env.company)
        logo = primary = secondary = False
        name = company.name
        motto = False
        if group and (group.logo or group.primary_color):
            logo = group.logo
            primary = _norm_hex(group.primary_color)
            secondary = _norm_hex(group.secondary_color)
            name = group.name
            motto = group.motto
        if not logo and company and not company.uses_default_logo:
            logo = company.logo
        primary = primary or _norm_hex(company.primary_color) or RSS_NAVY
        secondary = secondary or _norm_hex(company.secondary_color) or RSS_GOLD
        own = primary != RSS_NAVY or bool(logo)
        if isinstance(logo, bytes):
            logo = logo.decode()
        return {
            'name': name,
            'motto': motto,
            'logo': logo or False,
            'primary': primary,
            'secondary': secondary,
            'on_primary': _text_on(primary),
            'on_secondary': _text_on(secondary),
            'tint': _tint(primary, 0.90),
            'tint_strong': _tint(primary, 0.75),
            'own': own,
            'emblem': self._rss_emblem_b64(),
        }


class RssScoutGroupBranding(models.Model):
    _inherit = 'rss.scout.group'

    logo = fields.Image('Logo / emblema del grupo', max_width=512, max_height=512)
    primary_color = fields.Char(
        'Color principal', help='Color principal del grupo (pañoleta), formato #RRGGBB.')
    secondary_color = fields.Char('Color secundario', help='Color de acento, formato #RRGGBB.')
    motto = fields.Char('Lema', help='Aparece en recibos y documentos del grupo.')
    neckerchief = fields.Char('Pañoleta', help='Descripción de la pañoleta, p. ej. "azul marino, '
                                               'naranja y blanco con grecas negras".')
    brand_style = fields.Char(compute='_compute_brand_style')
    identity_banner = fields.Html(compute='_compute_identity_banner', sanitize=False)

    @api.depends('name', 'motto', 'logo', 'primary_color', 'secondary_color')
    def _compute_identity_banner(self):
        for group in self:
            logo = ''
            if group.logo and group.id:
                logo = ('<img src="/web/image/rss.scout.group/%s/logo/128x128" alt="Logo"/>'
                        % group.id)
            motto = ('<div class="o_rss_identity_motto">«%s»</div>' % escape(group.motto)
                     if group.motto else '')
            group.identity_banner = Markup(
                '<div class="o_rss_group_identity" style="%s">%s<div>'
                '<div class="o_rss_identity_name">%s</div>%s</div>'
                '<div class="o_rss_identity_seal">Red Seguro Scout<br/><i>Rumbo Seguro</i></div>'
                '</div>') % (Markup(group.brand_style), Markup(logo), group.name or '',
                             Markup(motto))

    @api.constrains('primary_color', 'secondary_color')
    def _check_colors(self):
        for group in self:
            for value in (group.primary_color, group.secondary_color):
                if value and not _norm_hex(value):
                    raise ValidationError(_('"%s" no es un color válido; usa el formato #RRGGBB.',
                                            value))

    @api.depends('primary_color', 'secondary_color')
    def _compute_brand_style(self):
        for group in self:
            primary = _norm_hex(group.primary_color) or RSS_NAVY
            secondary = _norm_hex(group.secondary_color) or RSS_GOLD
            group.brand_style = ('--rss-group-primary: %s; --rss-group-secondary: %s; '
                                 '--rss-group-on-primary: %s; --rss-group-tint: %s;'
                                 % (primary, secondary, _text_on(primary), _tint(primary, 0.9)))


def rss_report_css(brand):
    """CSS de los reportes QWeb con los colores de la identidad activa."""
    from markupsafe import Markup
    css = """
.rs-doc { font-family: Helvetica Neue, Arial, sans-serif; color: #1A2233; font-size: 10.5px; }
.rs-band { background: %(primary)s; color: %(on_primary)s; padding: 10px 14px;
           border-radius: 6px; border-bottom: 4px solid %(secondary)s; }
.rs-band table { width: 100%%; }
.rs-band td { vertical-align: middle; }
.rs-logo { height: 54px; max-width: 120px; margin-right: 12px; }
.rs-brand { font-size: 17px; font-weight: bold; letter-spacing: .3px; }
.rs-motto { font-style: italic; opacity: .9; font-size: 10.5px; }
.rs-title { font-size: 15px; font-weight: bold; text-align: right; }
.rs-sub { opacity: .88; text-align: right; font-size: 10.5px; }
.rs-kpis { width: 100%%; margin: 10px 0 8px 0; border-collapse: separate; border-spacing: 6px 0; }
.rs-kpi { background: %(tint)s; border-left: 4px solid %(secondary)s; padding: 6px 10px; }
.rs-kpi .v { font-size: 16px; font-weight: bold; color: %(primary)s; }
.rs-kpi .l { font-size: 9px; text-transform: uppercase; color: #4A5568; letter-spacing: .4px; }
table.rs-table { width: 100%%; border-collapse: collapse; }
table.rs-table th { background: %(primary)s; color: %(on_primary)s; font-size: 9.5px; padding: 5px 4px;
                    text-align: left; border: 1px solid %(primary)s; }
table.rs-table td { padding: 4px; border-bottom: 1px solid %(tint_strong)s; font-size: 9.5px; }
table.rs-table tr.rs-zebra td { background: %(tint)s; }
table.rs-table tr.rs-group td { background: %(tint_strong)s; color: %(primary)s; font-weight: bold;
                                font-size: 10.5px; padding: 5px 6px; }
table.rs-table tr.rs-subtotal td { font-weight: bold; color: %(primary)s; border-top: 2px solid %(secondary)s; }
.rs-c { text-align: center; }
.rs-r { text-align: right; }
.rs-tag { display: inline-block; padding: 1px 6px; border-radius: 8px; font-size: 8.5px; font-weight: bold; }
.rs-tag-new { background: #D7F0DF; color: #1B5E3B; }
.rs-tag-drop { background: #FBE3D6; color: #9A3B10; }
.rs-foot { margin-top: 14px; font-size: 9px; color: #4A5568; border-top: 1px solid %(tint_strong)s; padding-top: 6px; }
.rs-foot table { width: 100%%; }
.rs-seal { text-align: right; white-space: nowrap; color: #0A2A5E; font-weight: bold; }
.rs-seal img { height: 22px; vertical-align: middle; margin-left: 4px; }
.rs-warn { background: #FFF4D6; border: 1px solid #E8C866; color: #6B5300; padding: 6px 10px; margin-bottom: 8px; border-radius: 4px; }
.rs-h4 { color: %(primary)s; margin: 16px 0 6px 0; font-size: 12px; }
.rs-receipt-box { border: 2px solid %(primary)s; border-radius: 6px; margin-top: 12px; width: 100%%; }
.rs-receipt-box td { padding: 7px 12px; font-size: 11.5px; vertical-align: top; }
.rs-receipt-box .k { color: #4A5568; width: 30%%; }
.rs-amount { font-size: 22px; font-weight: bold; color: %(primary)s; }
.rs-sign { width: 100%%; }
.rs-sign td { padding-top: 46px; text-align: center; font-size: 10px; color: #4A5568; }
.rs-sign span { display: inline-block; border-top: 1px solid #1A2233; width: 70%%; padding-top: 4px; }
.rs-stamp { display: inline-block; border: 2px solid %(secondary)s; color: %(primary)s; padding: 4px 10px;
            border-radius: 4px; font-weight: bold; font-size: 11px; }
""" % brand
    return Markup('<style>%s</style>' % css)
