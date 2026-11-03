# -*- coding: utf-8 -*-
"""Alta de usuarios de portal (sin licencia) para asesores y representantes de grupo."""
from odoo import _, fields
from odoo.exceptions import UserError


def grant_portal_user(env, partner, company):
    """Crea o reactiva el usuario de portal del contacto y le envía la invitación.

    Devuelve el usuario. Rechaza contactos que ya son usuarios internos.
    """
    partner = partner.sudo()
    if not partner.email:
        raise UserError(_('%s necesita un correo para recibir su acceso.', partner.name))
    user = partner.with_context(active_test=False).user_ids[:1]
    if user and not user.share:
        raise UserError(_('%s ya es usuario interno; el portal es para usuarios externos.', partner.name))
    if not user:
        same_login = env['res.users'].sudo().with_context(active_test=False).search(
            [('login', '=ilike', partner.email.strip())], limit=1)
        if same_login:
            raise UserError(_('El correo %(email)s ya lo usa el usuario %(user)s.',
                              email=partner.email, user=same_login.name))
        wizard = env['portal.wizard'].sudo().with_context(
            active_model='res.partner', active_ids=partner.ids).create({})
        wizard.user_ids.action_grant_access()
        user = partner.with_context(active_test=False).user_ids[:1]
    elif not user.active:
        user.active = True
    if user:
        sync_portal_login(env, partner)
    if company and company not in user.company_ids:
        user.write({'company_ids': [fields.Command.link(company.id)]})
    return user


def sync_portal_login(env, partner):
    """Hace que el usuario de portal del contacto entre con su correo actual.

    Si el correo cambió después de dar el acceso, el usuario seguía entrando con el
    anterior (p. ej. example@example.com). Solo toca usuarios de portal.
    """
    partner = partner.sudo()
    email = (partner.email or '').strip().lower()
    user = partner.with_context(active_test=False).user_ids[:1]
    if not email or not user or not user.share or (user.login or '').lower() == email:
        return user
    taken = env['res.users'].sudo().with_context(active_test=False).search(
        [('login', '=ilike', email), ('id', '!=', user.id)], limit=1)
    if taken:
        raise UserError(_('El correo %(email)s ya lo usa el usuario %(user)s; usa otro correo.',
                          email=email, user=taken.name))
    user.write({'login': email})
    return user
