# -*- coding: utf-8 -*-
from odoo import fields, models

DEFAULT_NOTICE = """
<h3>Aviso de privacidad</h3>
<p><b>[Nombre del responsable]</b>, con domicilio en <b>[domicilio]</b>, es responsable del tratamiento de
los datos personales que se capturan en Red Seguro Scout.</p>
<p><b>Datos que recabamos:</b> de los asegurados, nombre, apellidos, fecha de nacimiento, edad, sexo y grupo
scout; del padre, madre o tutor, nombre completo, teléfono de emergencia y correo electrónico.</p>
<p><b>Finalidades:</b> dar de alta y administrar el seguro de accidentes personales de los integrantes de los
grupos scouts, enviar las relaciones de asegurados a la aseguradora, gestionar pagos y siniestros, y
contactar al padre, madre o tutor en caso de emergencia.</p>
<p><b>Transferencias:</b> los datos se comparten con la aseguradora y su agente o asesor únicamente para la
emisión de pólizas, certificados y la atención de siniestros.</p>
<p><b>Datos de menores de edad:</b> son proporcionados por el representante del grupo con el consentimiento de
su padre, madre o tutor.</p>
<p><b>Derechos ARCO:</b> para acceder, rectificar, cancelar u oponerse al uso de sus datos, escriba a
<b>[correo de contacto]</b>.</p>
<p>Cualquier cambio a este aviso se publicará en esta misma página.</p>
"""


class ResCompany(models.Model):
    _inherit = 'res.company'

    rss_privacy_notice = fields.Html(
        'Aviso de privacidad (Red Seguro Scout)', default=DEFAULT_NOTICE, sanitize=True,
        help='Se muestra siempre en los portales del grupo y del asesor, y debe aceptarse antes de '
             'capturar asegurados.')
