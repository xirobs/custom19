# -*- coding: utf-8 -*-
"""19.0.1.4.0: logo nuevo de Red Seguro Scout en la plataforma de Suscripciones."""
import base64

from odoo import SUPERUSER_ID, api
from odoo.tools.misc import file_open


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    platform = env.ref('red_seguro_scout.bita_platform_red_seguro_scout', raise_if_not_found=False)
    if platform:
        with file_open('red_seguro_scout/static/description/icon.png', 'rb') as f:
            platform.image = base64.b64encode(f.read())
