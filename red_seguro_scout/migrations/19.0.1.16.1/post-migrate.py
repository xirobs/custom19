# -*- coding: utf-8 -*-
"""Plantillas de correo del seguro: se vuelven a cargar una vez para firmar como Red Seguro Scout
(son noupdate y conservarían la firma y el remitente con el nombre de la compañía)."""
from odoo import SUPERUSER_ID, api
from odoo.tools import convert_file

FILES = ('data/mail_template_data.xml', 'data/mail_template_payment_request.xml',
         'data/mail_template_service_charge.xml')


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for path in FILES:
        convert_file(env, 'red_seguro_scout', path, {}, mode='init', noupdate=True, kind='data')
