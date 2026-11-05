# -*- coding: utf-8 -*-
"""Nombres de los niveles de Tropa como en la infografía del Sistema de adelanto."""
from odoo import SUPERUSER_ID, api

LABELS = {
    'stage_tropa_segunda': 'Segunda clase',
    'stage_tropa_primera': 'Primera clase',
    'stage_tropa_cordon': 'Manual Cordón Scout',
    'stage_tropa_correa': 'Manual Correa de Manigua',
    'stage_tropa_aves_fenix': 'Manual Insignia Terminal',
    'stage_tropa_piloto': 'Manual Piloto Scout',
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xid, label in LABELS.items():
        stage = env.ref('scout_management.%s' % xid, raise_if_not_found=False)
        if stage and not stage.path_label:
            stage.path_label = label
