# -*- coding: utf-8 -*-
"""Página pública de Respaldo Patrimonial (Vive Seguro), acceso con la marca y página de inicio del usuario.

* «/» muestra la página del seguro a los visitantes (sin sesión). Con sesión se comporta como siempre.
  Se apaga con el parámetro de sistema ``insurance_management.public_home`` = False
  (por ejemplo si en la misma base se instala el módulo Sitio web con su propia portada).
* «/vive-seguro»: la página; su formulario crea un prospecto en el CRM de seguros.
* Al iniciar sesión, el equipo de portal entra directo a «/equipo».
"""

import logging
import re

from odoo import _, http
from odoo.http import request
from odoo.addons.web.controllers.home import Home

_logger = logging.getLogger(__name__)

ICP_PUBLIC_HOME = "insurance_management.public_home"
INTERESTS = [
    ("gmm", "Gastos Médicos Mayores", ("médic", "medic", "gastos")),
    ("vida", "Seguro de Vida", ("vida",)),
    ("ahorro", "Ahorro, inversión y retiro", ("vida",)),
    ("educacion", "Educación de mis hijos", ("vida",)),
    ("accidentes", "Accidentes personales", ("accident",)),
    ("autos", "Autos", ("auto",)),
    ("danos", "Hogar o empresa (daños)", ("dano", "daño", "hogar")),
    ("otro", "Otro / no estoy seguro", ()),
]


def _public_home_enabled():
    if not request.db:
        return False
    try:
        value = request.env["ir.config_parameter"].sudo().get_param(ICP_PUBLIC_HOME, "True")
    except Exception:  # noqa: BLE001 - sin base todavía
        return False
    return str(value).lower() not in ("false", "0", "no")


class ViveSeguroHome(Home):

    @http.route("/", type="http", auth="none")
    def index(self, s_action=None, db=None, **kw):
        if not request.session.uid and _public_home_enabled():
            return request.redirect("/vive-seguro")
        return super().index(s_action=s_action, db=db, **kw)

    def _login_redirect(self, uid, redirect=None):
        if not redirect and request.db:
            user = request.env["res.users"].sudo().browse(uid)
            if user.exists() and not user._is_internal() and getattr(user, "ins_portal_access", False):
                return "/equipo"
        return super()._login_redirect(uid, redirect=redirect)


class ViveSeguroSite(http.Controller):

    def _site_values(self, **extra):
        company = request.env["res.company"].sudo().search([], limit=1)
        phone = company.phone or ""
        digits = re.sub(r"\D", "", phone)
        if len(digits) == 10:
            digits = "52" + digits
        values = {
            "company": company,
            "phone": phone,
            "wa_url": ("https://wa.me/%s?text=%s" % (digits, "Hola,%20me%20interesa%20información%20sobre%20un%20seguro"))
            if digits else False,
            "interests": INTERESTS,
            "x_icon": "/insurance_management/static/description/icon.png",
            "sent": False,
            "error": False,
        }
        values.update(extra)
        return values

    @http.route(["/vive-seguro"], type="http", auth="public", sitemap=True)
    def landing(self, enviado=None, **kw):
        return request.render("insurance_management.vs_landing", self._site_values(sent=bool(enviado)))

    @http.route(["/vive-seguro/contacto"], type="http", auth="public", methods=["POST"], csrf=True)
    def contact(self, **post):
        # campo trampa: los robots lo llenan, las personas no lo ven
        if post.get("sitio_web"):
            return request.redirect("/vive-seguro?enviado=1#contacto")
        name = " ".join((post.get("nombre") or "").split())[:120]
        phone = re.sub(r"[^\d+ ]", "", post.get("telefono") or "")[:30].strip()
        email = (post.get("correo") or "").strip()[:120]
        if not name or not (phone or email):
            values = self._site_values(error=_("Escribe tu nombre y un teléfono o correo para poder contactarte."),
                                       form=post)
            return request.render("insurance_management.vs_landing", values)
        key = post.get("interes") or "otro"
        label, needles = next(((lbl, nd) for k, lbl, nd in INTERESTS if k == key), (INTERESTS[-1][1], ()))
        env = request.env(su=True)
        ramo = env["insurance.policy.type"]
        for needle in needles:
            ramo = env["insurance.policy.type"].search([("name", "ilike", needle)], limit=1)
            if ramo:
                break
        tag = env.ref("insurance_management.crm_tag_web", raise_if_not_found=False)
        message = (post.get("mensaje") or "").strip()[:2000]
        vals = {
            "name": _("Sitio web: %(name)s · %(interest)s") % {"name": name, "interest": label},
            "contact_name": name,
            "phone": phone or False,
            "email_from": email or False,
            "type": "opportunity",
            "is_insurance_opportunity": True,
            "description": _("Interés: %(interest)s\nHorario preferido: %(when)s\n\n%(msg)s") % {
                "interest": label, "when": post.get("horario") or "—", "msg": message},
        }
        if ramo:
            vals["insurance_policy_type_id"] = ramo.id
        if tag:
            vals["tag_ids"] = [(4, tag.id)]
        try:
            env["crm.lead"].create(vals)
        except Exception:  # noqa: BLE001 - nunca mostrar un error técnico al visitante
            _logger.exception("Vive Seguro: no se pudo crear el prospecto del sitio web")
            values = self._site_values(error=_("No pudimos registrar tu mensaje. Escríbenos por WhatsApp, por favor."),
                                       form=post)
            return request.render("insurance_management.vs_landing", values)
        return request.redirect("/vive-seguro?enviado=1#contacto")
