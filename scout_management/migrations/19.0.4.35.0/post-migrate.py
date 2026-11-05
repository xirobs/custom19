# -*- coding: utf-8 -*-
"""Tropa: ruta de adelantos secuencial (Tercera, Segunda, Primera, Cordón Scout, Correa de Manigua, Insignia
terminal y Piloto Scout), especialidades desde Segunda clase e imagen de la secuencia."""
import base64

from odoo import SUPERUSER_ID, api
from odoo.tools.misc import file_open

SUMMARIES = {
    'stage_tropa_segunda': 'Manual de progresión Scout – Segunda Clase.',
    'stage_tropa_primera': 'Manual de progresión Scout – Primera Clase.',
    'stage_tropa_aves_fenix': 'Lineamientos, requisitos y especialidades de la Insignia Terminal de sección.',
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    ref = lambda xid: env.ref('scout_management.%s' % xid, raise_if_not_found=False)
    for xid, text in SUMMARIES.items():
        stage = ref(xid)
        if stage and not stage.summary:
            stage.summary = text
    with file_open('scout_management/static/img/ruta_tropa.jpg', 'rb') as f:
        image = base64.b64encode(f.read())
    segunda = ref('stage_tropa_segunda')
    tropas = env['scout.section'].search([]).filtered(
        lambda s: segunda and s in segunda.section_ids and not s.is_leadership)
    for section in tropas:
        vals = {'sequential_path': True}
        if not section.specialty_start_stage_id and segunda:
            vals['specialty_start_stage_id'] = segunda.id
        if not section.path_image:
            vals['path_image'] = image
        section.write(vals)
