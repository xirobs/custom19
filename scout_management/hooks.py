# -*- coding: utf-8 -*-
import base64
import logging

from odoo.tools.misc import file_open

_logger = logging.getLogger(__name__)


def _load_image(path):
    try:
        with file_open(path, 'rb') as handle:
            return base64.b64encode(handle.read())
    except (FileNotFoundError, OSError):
        return False


def scout_load_branding(env):
    """Carga el logo del grupo y la flor de lis desde static/img si existen y la compañía no los tiene."""
    logo = _load_image('scout_management/static/img/logo_grupo.png')
    emblem = _load_image('scout_management/static/img/flor_de_lis.png')
    default_logo = _load_image('base/static/img/res_company_logo.png')
    for company in env['res.company'].search([]):
        vals = {}
        if logo and (not company.logo or company.logo == default_logo):
            vals['logo'] = logo
        if emblem and not company.scout_emblem:
            vals['scout_emblem'] = emblem
        if vals:
            company.write(vals)
            _logger.info('Scout: imágenes del grupo cargadas en %s', company.name)


def post_init_hook(env):
    env['account.journal'].search([('type', 'in', ('cash', 'bank'))])._scout_setup_simple_treasury()
    scout_load_branding(env)
    env['scout.progression.stage']._scout_assign_categories()
    env['scout.section']._scout_route_defaults()
    env['scout.progression.stage']._scout_v44_migrate()
    env['scout.route']._cron_refresh()
