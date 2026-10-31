# -*- coding: utf-8 -*-
import base64
import json
import logging

from odoo import _, fields, http
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import request
from odoo.tools import consteq

from odoo.addons.portal.controllers.portal import CustomerPortal

_logger = logging.getLogger(__name__)

MAX_PROOF_SIZE = 5 * 1024 * 1024  # 5 MB


def _scout_pay_links(invoices, member=None):
    """{id de cargo: liga para pagar en línea} solo si hay una pasarela activa (Mercado Pago).
    La liga real (con la comisión, si aplica) se arma hasta que la familia da clic en "Pagar"."""
    env = request.env
    if not invoices or not member or \
            not env['payment.provider'].sudo().search_count([('state', 'in', ('enabled', 'test'))]):
        return {}
    return {inv.id: f'/my/scouts/{member.id}/pagar/{inv.id}' for inv in invoices if inv.scout_can_pay_online}


class ScoutPortal(CustomerPortal):

    # ------------------------------------------------------------------ helpers
    def _scout_partner(self):
        return request.env.user.partner_id.commercial_partner_id

    def _scout_members(self):
        """Miembros cuyo tutor es el usuario del portal (o su empresa/contactos hijos)."""
        partner = self._scout_partner()
        return request.env['res.partner'].sudo().search([
            ('is_scout', '=', True), ('scout_state', '!=', 'inactive'),
            '|', '|', ('id', '=', partner.id),
            ('scout_guardian_id', 'child_of', partner.id), ('scout_guardian2_id', 'child_of', partner.id)])

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
            'pay_links': _scout_pay_links(invoices, member),
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
            'photo_ok': kw.get('foto') == 'ok',
            'photo_error': _('No se pudo leer la foto; intenta con otra imagen (JPG o PNG).') if kw.get('foto') == 'error' else None,
            'page_name': 'scout_detail',
        })
        return request.render('scout_management.portal_scout_detail', values)

    @http.route(['/my/scouts/<int:member_id>/pagar/<int:invoice_id>'], type='http', auth='user', website=True)
    def portal_scout_pay(self, member_id, invoice_id, **kw):
        """La familia eligió pagar en línea: se agrega la comisión (si aplica) y se abre Mercado Pago."""
        try:
            member = self._scout_member_check(member_id)
        except (AccessError, MissingError):
            return request.redirect('/my/scouts')
        invoice = member.sudo().scout_invoice_ids.filtered(lambda m: m.id == invoice_id and m.scout_can_pay_online)
        if not invoice:
            return request.redirect(f'/my/scouts/{member.id}')
        return request.redirect(invoice._scout_payment_link(), local=False)

    # ------------------------------------------------------------------ aula virtual y avances de la sección
    @http.route(['/my/scouts/<int:member_id>/aula'], type='http', auth='user', website=True)
    def portal_scout_classroom(self, member_id, **kw):
        try:
            member = self._scout_member_check(member_id)
        except (AccessError, MissingError):
            return request.redirect('/my/scouts')
        values = self._prepare_portal_layout_values()
        values.update({'member': member, 'aula': request.env['scout.resource'].sudo()._scout_classroom(member),
                       'locked': kw.get('bloqueado'), 'page_name': 'scout_classroom'})
        return request.render('scout_management.portal_scout_classroom', values)

    def _scout_classroom_item(self, member, resource_id):
        resource = request.env['scout.resource'].sudo().browse(int(resource_id)).exists()
        if not resource or not resource.for_scouts or not resource._scout_unlocked_for(member):
            return None
        return resource

    @http.route(['/my/scouts/<int:member_id>/aula/<int:resource_id>'], type='http', auth='user', website=True)
    def portal_scout_classroom_resource(self, member_id, resource_id, **kw):
        try:
            member = self._scout_member_check(member_id)
        except (AccessError, MissingError):
            return request.redirect('/my/scouts')
        resource = self._scout_classroom_item(member, resource_id)
        if not resource:
            return request.redirect(f'/my/scouts/{member.id}/aula?bloqueado=1')
        resource._scout_mark_seen(member)
        aula = request.env['scout.resource'].sudo()._scout_classroom(member)
        unit = next((u for u in aula['units'] if any(i['resource'] == resource for i in u['items'])), None)
        flat = [i['resource'] for u in aula['units'] if u['state'] != 'locked' for i in u['items']]
        position = flat.index(resource) if resource in flat else -1
        values = self._prepare_portal_layout_values()
        values.update({
            'member': member, 'resource': resource, 'unit': unit, 'aula': aula,
            'next_resource': flat[position + 1] if 0 <= position < len(flat) - 1 else None,
            'prev_resource': flat[position - 1] if position > 0 else None,
            'file_url': f'/my/scouts/{member.id}/aula/{resource.id}/archivo', 'page_name': 'scout_classroom',
        })
        return request.render('scout_management.portal_scout_classroom_resource', values)

    @http.route(['/my/scouts/<int:member_id>/aula/<int:resource_id>/archivo'], type='http', auth='user')
    def portal_scout_classroom_file(self, member_id, resource_id, descargar=None, **kw):
        try:
            member = self._scout_member_check(member_id)
        except (AccessError, MissingError):
            return request.not_found()
        resource = self._scout_classroom_item(member, resource_id)
        if not resource or not resource.file:
            return request.not_found()
        stream = request.env['ir.binary']._get_stream_from(
            resource, 'file', filename=resource.file_name or resource.name, mimetype=resource._scout_mimetype())
        return stream.get_response(as_attachment=bool(descargar))

    @http.route(['/my/scouts/<int:member_id>/avances'], type='http', auth='user', website=True)
    def portal_scout_section_board(self, member_id, **kw):
        """Tablero de avances de la sección (para motivar): cómo va cada scout y cada equipo."""
        try:
            member = self._scout_member_check(member_id)
        except (AccessError, MissingError):
            return request.redirect('/my/scouts')
        section = member.sudo().scout_section_id
        portal = request.env['scout.leader.portal'].sudo()
        rows = portal.achievements(section, []) if section else []
        rows.sort(key=lambda r: (-r['pct'], -r['specialties'], r['member'].name or ''))
        teams = {}
        for row in rows:
            patrol = row['member'].scout_patrol_id
            if patrol:
                teams.setdefault(patrol, []).append(row['pct'])
        team_rows = sorted(({'patrol': p, 'pct': round(sum(v) / len(v)), 'members': len(v)} for p, v in teams.items()),
                           key=lambda t: -t['pct'])
        values = self._prepare_portal_layout_values()
        values.update({
            'member': member, 'section': section, 'rows': rows, 'team_rows': team_rows,
            'mine': next((r for r in rows if r['member'] == member), None),
            'position': next((i + 1 for i, r in enumerate(rows) if r['member'] == member), 0),
            'portal_svc': portal, 'page_name': 'scout_board',
        })
        return request.render('scout_management.portal_scout_section_board', values)

    @http.route(['/my/scouts/<int:member_id>/foto'], type='http', auth='user', website=True, methods=['POST'])
    def portal_scout_photo(self, member_id, **post):
        """El tutor (o el propio scout con su usuario) sube o cambia la foto del miembro."""
        try:
            member = self._scout_member_check(member_id)
        except (AccessError, MissingError):
            return request.redirect('/my/scouts')
        photo = scout_photo_from_post(post.get('photo_data'))
        if not photo:
            return request.redirect(f'/my/scouts/{member.id}?foto=error')
        member.sudo().with_context(scout_change_approved=True).image_1920 = photo
        member.sudo().message_post(body=_('Foto actualizada desde el portal por %s.', request.env.user.name))
        return request.redirect(f'/my/scouts/{member.id}?foto=ok')

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


def scout_photo_from_post(value):
    """Foto reducida en el celular (data URL de imagen) → base64; None si no viene o no es imagen."""
    if not isinstance(value, str) or not value.startswith('data:image/'):
        return None
    data = value.split(',', 1)[1] if ',' in value else ''
    if not data or len(data) > MAX_PROOF_SIZE * 2:
        return None
    try:
        base64.b64decode(data, validate=True)
    except ValueError:
        return None
    return data.encode()


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
    cycle = env['scout.cycle'].sudo()._get_registration()
    company = (section or member.scout_section_id).company_id or cycle.company_id or env.company
    return {
        'transfer': company._scout_transfer_info(member.name if member else None,
                                                 member.scout_section_id if member else section),
        'member': member, 'cycle': cycle,
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
    if not member:
        # Reinscripción con la liga general: si ya es (o fue) miembro, se actualiza su expediente, no se duplica.
        member = env['res.partner'].sudo()._scout_find_existing(
            post.get('first_name'), post.get('last_name'), post.get('last_name2'), birthdate)
    # La sección no la elige quien llena la ficha: un miembro conserva la suya (los pases los registran los
    # jefes) y uno nuevo va a la que le corresponde por edad y sexo.
    section = member.scout_section_id if member and member.scout_section_id \
        and not member.scout_section_id.is_leadership else Section._scout_suggest(birthdate, gender)
    if not section:
        return None, _('No hay una sección del grupo para esa edad; revisa la fecha de nacimiento o contacta a '
                       'la jefatura.')
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
    id_uploads = []
    for key, field, side in (('ine_front', 'guardian_id_front', 'front'), ('ine_back', 'guardian_id_back', 'back')):
        upload = post.get(key)
        if upload and hasattr(upload, 'read'):
            content = upload.read()
            if len(content) > MAX_PROOF_SIZE * 3:
                return None, _('La foto de la identificación no debe superar 15 MB.')
            if content:
                encoded = base64.b64encode(content)
                vals.update({field: encoded, f'{field}_name': upload.filename})
                id_uploads.append(((encoded, upload.filename), side))
    try:
        vals['guardian_id_type'], check = env['scout.registration'].sudo()._scout_verify_id_uploads(
            post.get('guardian_id_type'), id_uploads)
        if check:
            vals['guardian_id_check'] = check
    except UserError as e:
        return None, str(e.args[0] if e.args else e)
    if not vals.get('guardian_id_front'):
        previous = Registration.search([('member_id', '=', member.id), ('guardian_id_front', '!=', False)],
                                       order='id desc', limit=1) if member else Registration
        if previous:
            vals.update(guardian_id_front=previous.guardian_id_front,
                        guardian_id_front_name=previous.guardian_id_front_name)
        elif not (existing and existing.guardian_id_front):
            return None, _('Adjunta la identificación del padre, madre o tutor (INE, FM3 o pasaporte).')
    if not scout_photo_from_post(post.get('photo_data')) and not (member and member.sudo().image_1920):
        return None, _('Falta la foto del scout (de frente, para su credencial). Tómala o elígela antes de enviar.')
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
        photo = scout_photo_from_post(post.get('photo_data'))
        if photo and registration.member_id:
            registration.member_id.sudo().with_context(scout_change_approved=True).image_1920 = photo
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
                return request.redirect(reg.invoice_id._scout_payment_link(), local=False)
            if post.get('method') == 'cash':
                reg._scout_set_payment_method('cash')
                message = _('Listo: entrega el efectivo a tesorería o a la jefatura en la próxima junta. '
                            'Te darán tu recibo.')
            if post.get('method') == 'transfer':
                proof = post.get('payment_proof')
                content = proof.read() if proof and hasattr(proof, 'read') else b''
                if len(content) > MAX_PROOF_SIZE:
                    message = _('El comprobante no debe superar 5 MB.')
                else:
                    reg._scout_declare_transfer(post.get('payment_reference'), post.get('payment_date'),
                                                post.get('payment_amount'),
                                                base64.b64encode(content) if content else False,
                                                proof.filename if content else False)
                    message = _('¡Gracias! Avisamos a tesorería; al verificar la transferencia tu registro quedará pagado.')
        invoice = reg.invoice_id
        return request.render('scout_management.scout_registration_payment', {
            'registration': reg, 'invoice': invoice, 'message': message, 'access_token': access_token,
            'can_online': bool(invoice and invoice.scout_can_pay_online and request.env['payment.provider'].sudo()
                               .search_count([('state', 'in', ('enabled', 'test'))])),
            'paid': reg.payment_state in ('paid', 'exempt'),
            'transfer': reg.company_id._scout_transfer_info(reg.member_id.name or reg.first_name, reg.section_id),
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


class ScoutIdCheck(http.Controller):

    @http.route(['/scouts/identificacion/validar'], type='jsonrpc', auth='public', website=True)
    def scout_id_validate(self, data=None, doc_type='ine', side='front', **kw):
        """Revisa al momento la foto de la identificación del tutor (INE, FM3 o pasaporte)."""
        count = request.session.get('scout_id_checks', 0)
        if count >= 25:
            return {'level': 'warn', 'message': _('Foto recibida; se revisará al enviar la ficha.')}
        request.session['scout_id_checks'] = count + 1
        Registration = request.env['scout.registration'].sudo()
        content, mimetype = Registration._scout_decode_upload(data or '')
        return Registration._scout_check_id_document(content, mimetype, doc_type, side)

