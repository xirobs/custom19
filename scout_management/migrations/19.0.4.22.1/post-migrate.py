# -*- coding: utf-8 -*-
"""La ruta de enlace es solo para miembros de sección: se quitan las rutas abiertas de quien tiene cargo de
jefe, subjefe, scouter o dirigente."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['scout.route']._cron_refresh()
