# -*- coding: utf-8 -*-
import base64
import json
import logging

from odoo import _, fields, http
from odoo.exceptions import AccessError, MissingError, ValidationError
from odoo.http import request
from odoo.tools import consteq

from odoo.addons.portal.controllers.portal import CustomerPortal

_logger = logging.getLogger(__name__)

MAX_PROOF_SIZE = 5 * 1024 * 1024  # 5 MB


def _scout_pay_links(invoices):
    """{id de cargo: liga de pago} solo si hay una pasarela activa (Mercado Pago)."""
    env = request.env
    if not invoices or not env['payment.provider'].sudo().search_count([('state', 'in', ('enabled', 'test'))]):
        return {}
    links = {}
    for inv in invoices.sudo():
        try:
            links[inv.id] = inv._scout_payment_link()
        except Exception:  # noqa: BLE001
            _logger.exception('No se pudo generar la liga de pago de %s', inv.name)
    return links


class ScoutPortal(CustomerPortal):

    # ------------------------------------------------------------------ helpers
    def _scout_partner(self):
        return request.env.user.partner_id.commercial_partner_id

    def _scout_members(self):
        """Miembros cuyo tutor es el usuario del portal (o su empresa/contactos hijos)."""
        partner = self._scout_partner()
        return request.env['res.partner'].sudo().search([
            ('is_scout', '=', True), ('scout_state', '!=', 'inactive'),
            '|', ('scout_guardian_id', 'child_of', partner.id), ('scout_guardian2_id', 'child_of', partner.id)])

    def _scout_member_check(self, member_id):
        member = request.env['res.partner'].sudo().browse(int(member_id)).exists()
        if not member or member not in self._scout_members():
            raise MissingError(_('Este registro no existe o no tienes acceso.'))
        return member

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'scout_count' in counters:
            values['scout_count'] = len(self._scout_members())
        return values

    # ------------------------------------------------------------------ pages
    @http.route(['/my/scouts'], type='http', auth='user', website=True)
    def portal_my_scouts(self, **kw):
        values = self._prepare_portal_layout_values()
        members = self._scout_members()
        registrations = request.env['scout.registration'].sudo().search([
            ('create_uid', '=', request.env.user.id), ('state', 'in', ('draft', 'submitted'))])
        values.update({'members': members, 'pending_registrations': registrations, 'page_name': 'scouts'})
        return request.render('scout_management.portal_my_scouts', values)

    @http.route(['/my/scouts/<int:member_id>'], type='http', auth='user', website=True)
    def portal_scout_detail(self, member_id, **kw):
        try:
            member = self._scout_member_check(member_id)
        except (AccessError, MissingError):
            return request.redirect('/my/scouts')
        lines = request.env['scout.attendance.line'].sudo().search(
            [('member_id', '=', member.id), ('closed', '=', True)], limit=12)
        today = fields.Date.context_today(member)
        activities = request.env['calendar.event'].sudo().search([
            ('is_scout_event', '=', True), ('scout_date', '>=', today),
            '|', ('scout_section_ids', 'in', member.scout_section_id.ids), ('scout_scope', '=', 'group'),
        ], order='start', limit=10)
        conversation = request.env['scout.family.conversation'].sudo()._get_or_create_for_member(member)
        conversation._portal_ensure_token()
        pid = request.env.user.partner_id.id
        invoices = member.scout_invoice_ids.filtered(
            lambda m: m.state == 'posted' and m.move_type == 'out_invoice' and m.payment_state in ('not_paid', 'partial'))
        values = self._prepare_portal_layout_values()
        values.update({
            'member': member,
            'pay_links': _scout_pay_links(invoices),
            'attendance_lines': lines,
            'activities': activities,
            'invoices': invoices,
            'progress': member.scout_progress_ids.filtered(lambda p: p.state == 'achieved').sorted('date_achieved'),
            'conversation': conversation,
            'object': conversation,
            'token': conversation.access_token,
            'pid': pid,
            'hash': conversation._sign_token(pid),
            'already': kw.get('already'),
            'page_name': 'scout_detail',
        })
        return request.render('scout_management.portal_scout_detail', values)

    @http.route(['/my/scouts/register', '/my/scouts/<int:member_id>/register'], type='http', auth='user',
                website=True, methods=['GET'])
    def portal_scout_register_form(self, member_id=None, error=None, **kw):
        member = request.env['res.partner']
        if member_id:
            try:
                member = self._scout_member_check(member_id)
            except (AccessError, MissingError):
                return request.redirect('/my/scouts')
        cycle = request.env['scout.cycle'].sudo()._get_registration()
        current = member and cycle and member.sudo().scout_registration_ids.filtered(
            lambda r: r.cycle_id == cycle and r.state != 'cancel')[:1]
        if current and current.state in ('reviewed', 'validated'):
            return request.redirect(f'/my/scouts/{member.id}?already=1')
        values = self._prepare_portal_layout_values()
        values.update(scout_register_values(member, request.env.user.partner_id, error))
        values.update({'form_action': '/my/scouts/register/submit', 'layout': 'portal.portal_layout',
                       'page_name': 'scout_register'})
        return request.render('scout_management.portal_scout_register', values)

    @http.route(['/my/scouts/register/submit'], type='http', auth='user', website=True, methods=['POST'])
    def portal_scout_register_submit(self, **post):
        member = request.env['res.partner']
        if post.get('member_id'):
            try:
                member = self._scout_member_check(post['member_id'])
            except (AccessError, MissingError):
                return request.redirect('/my/scouts')
        registration, error = scout_save_registration(post, member, request.env.user.partner_id)
        if error:
            return self.portal_scout_register_form(member_id=member.id or None, error=error)
        registration.message_post(body=_('Ficha enviada desde el portal por %s.', request.env.user.name))
        return request.redirect(registration._scout_payment_path())


# ---------------------------------------------------------------------- ficha de registro (compartida)
def scout_register_values(member, partner=None, error=None, section=None):
    member = member.sudo()
    env = request.env
    sections = env['scout.section'].sudo().search([('is_leadership', '=', False)])
    rules = {}
    for sec in sections:
        _route, pass_, stay = sec._route_offsets()
        rules[sec.id] = {'upper': pass_ or stay, 'gender': sec.gender or ''}
    previous = env['scout.registration'].sudo().search(
        [('member_id', '=', member.id), ('state', '!=', 'cancel')], order='id desc', limit=1) if member else None
    return {
        'member': member, 'cycle': env['scout.cycle'].sudo()._get_registration(),
        'partner': partner or member.scout_guardian_id or env['res.partner'],
        'previous': previous, 'preset_section': section or member.scout_section_id,
        'sections': sections, 'section_rules_json': json.dumps(rules),
        'medical_services': env['scout.registration']._fields['medical_service'].selection,
        'blood_types': env['scout.registration']._fields['blood_type'].selection,
        'error': error,
    }


def scout_save_registration(post, member, guardian=None):
    """Crea o actualiza la ficha del ciclo con lo que llenó la familia. Devuelve (ficha, error)."""
    env = request.env
    member = member.sudo()
    required = ['first_name', 'last_name', 'birthdate', 'gender', 'guardian_name', 'guardian_phone',
                'guardian_signed_by', 'guardian_signature']
    consents = ['consent_participation', 'consent_first_aid', 'consent_image', 'consent_data', 'consent_risk',
                'consent_update']
    if any(not post.get(f) for f in required) or not all(post.get(c) for c in consents):
        return None, _('Completa los campos obligatorios, acepta todas las autorizaciones y firma la ficha.')
    cycle = env['scout.cycle'].sudo()._get_registration()
    if not cycle:
        return None, _('No hay un ciclo scout abierto; contacta a la jefatura.')
    try:
        birthdate = fields.Date.to_date(post.get('birthdate'))
    except ValueError:
        return None, _('La fecha de nacimiento no es válida.')
    gender = post.get('gender') if post.get('gender') in ('m', 'f') else False
    Section = env['scout.section'].sudo()
    section = Section.browse(int(post['section_id'])).exists() if str(post.get('section_id') or '').isdigit() \
        else Section
    if not section:
        section = Section._scout_suggest(birthdate, gender)
    if not section:
        return None, _('No encontramos una sección para esa edad; contacta a la jefatura.')
    if not member:
        # Reinscripción con la liga general: si ya es (o fue) miembro, se actualiza su expediente, no se duplica.
        member = env['res.partner'].sudo()._scout_find_existing(
            post.get('first_name'), post.get('last_name'), post.get('last_name2'), birthdate)
    Registration = env['scout.registration'].sudo()
    existing = Registration.search([('member_id', '=', member.id), ('cycle_id', '=', cycle.id),
                                    ('state', '!=', 'cancel')], limit=1) if member else Registration
    if existing and existing.state in ('reviewed', 'validated'):
        return None, _('%(name)s ya tiene su ficha del ciclo %(cycle)s en revisión o validada. Si falta el pago, '
                       'pide la liga de pago a la jefatura.', name=member.name, cycle=cycle.name)

    def _signature(key):
        data = post.get(key) or ''
        return data.split(',', 1)[1] if data.startswith('data:image') else False

    method = post.get('payment_method') if post.get('payment_method') in ('online', 'cash', 'transfer') else 'online'
    vals = {
        'cycle_id': cycle.id, 'member_id': member.id or False, 'source': 'portal', 'state': 'submitted',
        'company_id': section.company_id.id or cycle.company_id.id,
        'first_name': post.get('first_name', '').strip(), 'last_name': post.get('last_name', '').strip(),
        'last_name2': post.get('last_name2', '').strip(), 'birthdate': birthdate, 'gender': gender,
        'section_id': section.id, 'street': post.get('street'),
        'guardian_id': guardian.id if guardian and not guardian._is_public() else False,
        'guardian_name': post.get('guardian_name'), 'guardian_relationship': post.get('guardian_relationship'),
        'guardian_phone': post.get('guardian_phone'), 'guardian_phone_alt': post.get('guardian_phone_alt'),
        'guardian_email': post.get('guardian_email') or (guardian.email if guardian else False),
        'guardian_street': post.get('guardian_street'),
        'medical_service': post.get('medical_service') or False, 'medical_policy': post.get('medical_policy'),
        'blood_type': post.get('blood_type') or False, 'medical_conditions': post.get('medical_conditions'),
        'payment_method': method, 'declared_paid': method == 'transfer',
        'payment_reference': post.get('payment_reference'), 'payment_date': post.get('payment_date') or False,
        'payment_amount': float(post.get('payment_amount') or 0.0) if method == 'transfer' else 0.0,
        'consent_participation': True, 'consent_first_aid': True, 'consent_data': True, 'consent_risk': True,
        'consent_update': True, 'consent_image': True,
        'guardian_signed_by': post.get('guardian_signed_by'), 'guardian_signature': _signature('guardian_signature'),
        'member_signed_by': post.get('member_signed_by'), 'member_signature': _signature('member_signature'),
        'signed_date': fields.Date.context_today(request.env.user),
    }
    for key, field in (('ine_front', 'guardian_id_front'), ('ine_back', 'guardian_id_back')):
        upload = post.get(key)
        if upload and hasattr(upload, 'read'):
            content = upload.read()
            if len(content) > MAX_PROOF_SIZE * 2:
                return None, _('La INE no debe superar 10 MB.')
            if content:
                vals.update({field: base64.b64encode(content), f'{field}_name': upload.filename})
    if not vals.get('guardian_id_front'):
        previous = Registration.search([('member_id', '=', member.id), ('guardian_id_front', '!=', False)],
                                       order='id desc', limit=1) if member else Registration
        if previous:
            vals.update(guardian_id_front=previous.guardian_id_front,
                        guardian_id_front_name=previous.guardian_id_front_name)
        elif not (existing and existing.guardian_id_front):
            return None, _('Adjunta la INE del padre, madre o tutor (frente).')
    if member:
        vals['kind'] = 'return' if member.scout_state == 'inactive' else 'renewal'
    proof = post.get('payment_proof')
    if proof and hasattr(proof, 'read'):
        content = proof.read()
        if len(content) > MAX_PROOF_SIZE:
            return None, _('El comprobante no debe superar 5 MB.')
        if content:
            vals.update({'payment_proof': base64.b64encode(content), 'payment_proof_name': proof.filename})
    try:
        if existing:
            existing.write(vals)
            registration = existing
        else:
            registration = Registration.create(vals)
        registration._scout_prepare_for_payment()
        registration._scout_set_payment_method(method)
    except ValidationError as e:
        return None, str(e.args[0] if e.args else e)
    officers = env.ref('scout_management.group_scout_officer').sudo().all_user_ids.filtered(
        lambda u: not u.share and u.id != 1)
    todo = env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
    for user in officers[:2]:
        registration.activity_schedule(activity_type_id=todo.id if todo else False,
                                       summary=_('Revisar ficha enviada por la familia'), user_id=user.id)
    _logger.info('Ficha scout %s enviada por la familia (usuario %s)', registration.name, request.env.uid)
    return registration, None


class ScoutPublicRegistration(http.Controller):
    """Ligas sin contraseña para que la familia llene la ficha y pague (en línea o en efectivo)."""

    def _render_form(self, member, action, error=None, section=None):
        values = scout_register_values(member, None, error, section)
        values.update({'form_action': action, 'layout': 'web.frontend_layout', 'public_form': True})
        return request.render('scout_management.portal_scout_register', values)

    @http.route(['/scout/inscripcion/<string:token>'], type='http', auth='public', website=True, methods=['GET'])
    def scout_signup(self, token, seccion=None, **kw):
        if not consteq(token, request.env['res.partner'].sudo()._scout_signup_token()):
            return request.not_found()
        section = request.env['scout.section'].sudo().browse(int(seccion)).exists() if str(seccion or '').isdigit() \
            else None
        return self._render_form(request.env['res.partner'], f'/scout/inscripcion/{token}', section=section)

    @http.route(['/scout/inscripcion/<string:token>'], type='http', auth='public', website=True, methods=['POST'])
    def scout_signup_submit(self, token, **post):
        if not consteq(token, request.env['res.partner'].sudo()._scout_signup_token()):
            return request.not_found()
        registration, error = scout_save_registration(post, request.env['res.partner'])
        if error:
            return self._render_form(request.env['res.partner'], f'/scout/inscripcion/{token}', error)
        return request.redirect(registration._scout_payment_path())

    def _member_from_token(self, member_id, token):
        member = request.env['res.partner'].sudo().browse(member_id).exists()
        if not member or not member.is_scout or not member.scout_form_token \
                or not consteq(token, member.scout_form_token):
            return None
        return member

    @http.route(['/scout/ficha/<int:member_id>/<string:token>'], type='http', auth='public', website=True,
                methods=['GET', 'POST'])
    def scout_member_form(self, member_id, token, **post):
        member = self._member_from_token(member_id, token)
        if not member:
            return request.not_found()
        action = f'/scout/ficha/{member.id}/{token}'
        cycle = request.env['scout.cycle'].sudo()._get_registration()
        current = member.scout_registration_ids.filtered(lambda r: r.cycle_id == cycle and r.state != 'cancel')[:1]
        if request.httprequest.method == 'GET' and current and current.state in ('reviewed', 'validated'):
            return request.redirect(current._scout_payment_path())
        if request.httprequest.method == 'POST':
            registration, error = scout_save_registration(post, member)
            if error:
                return self._render_form(member, action, error)
            return request.redirect(registration._scout_payment_path())
        return self._render_form(member, action)

    def _registration(self, reg_id, access_token):
        reg = request.env['scout.registration'].sudo().browse(reg_id).exists()
        if not reg or not access_token or not reg.access_token or not consteq(access_token, reg.access_token):
            return None
        return reg

    @http.route(['/scout/ficha/pago/<int:reg_id>'], type='http', auth='public', website=True, methods=['GET', 'POST'])
    def scout_registration_payment(self, reg_id, access_token=None, **post):
        reg = self._registration(reg_id, access_token)
        if not reg:
            return request.not_found()
        message = None
        if request.httprequest.method == 'POST':
            if post.get('method') == 'online' and reg.invoice_id.scout_can_pay_online:
                reg._scout_set_payment_method('online')
                return request.redirect(reg.invoice_id._scout_payment_link())
            if post.get('method') == 'cash':
                reg._scout_set_payment_method('cash')
                message = _('Listo: entrega el efectivo a tesorería o a la jefatura en la próxima junta. '
                            'Te darán tu recibo.')
        invoice = reg.invoice_id
        return request.render('scout_management.scout_registration_payment', {
            'registration': reg, 'invoice': invoice, 'message': message, 'access_token': access_token,
            'can_online': bool(invoice and invoice.scout_can_pay_online and request.env['payment.provider'].sudo()
                               .search_count([('state', 'in', ('enabled', 'test'))])),
            'paid': reg.payment_state in ('paid', 'exempt'),
        })


class ScoutReceiptController(http.Controller):

    @http.route(['/scout/recibo/<int:sale_id>'], type='http', auth='public', website=True)
    def scout_receipt_pdf(self, sale_id, access_token=None, **kw):
        """Descarga del recibo en PDF con la liga enviada por correo o WhatsApp."""
        sale = request.env['scout.store.sale'].sudo().browse(sale_id).exists()
        if not sale or not access_token or not sale.access_token or not consteq(access_token, sale.access_token):
            return request.not_found()
        pdf, _type = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
            'scout_management.action_report_store_receipt', sale.ids)
        headers = [('Content-Type', 'application/pdf'), ('Content-Length', len(pdf)),
                   ('Content-Disposition', f'inline; filename="Recibo {sale.name}.pdf"')]
        return request.make_response(pdf, headers=headers)
