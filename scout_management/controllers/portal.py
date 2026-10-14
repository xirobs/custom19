# -*- coding: utf-8 -*-
import base64
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
        cycle = request.env['scout.cycle'].sudo()._get_current()
        if member and cycle and member.scout_registration_ids.filtered(
                lambda r: r.cycle_id == cycle and r.state != 'cancel'):
            return request.redirect(f'/my/scouts/{member.id}?already=1')
        partner = request.env.user.partner_id
        values = self._prepare_portal_layout_values()
        values.update({
            'member': member,
            'cycle': cycle,
            'partner': partner,
            'sections': request.env['scout.section'].sudo().search([('is_leadership', '=', False)]),
            'medical_services': request.env['scout.registration']._fields['medical_service'].selection,
            'blood_types': request.env['scout.registration']._fields['blood_type'].selection,
            'error': error,
            'page_name': 'scout_register',
        })
        return request.render('scout_management.portal_scout_register', values)

    @http.route(['/my/scouts/register/submit'], type='http', auth='user', website=True, methods=['POST'])
    def portal_scout_register_submit(self, **post):
        member = request.env['res.partner']
        if post.get('member_id'):
            try:
                member = self._scout_member_check(post['member_id'])
            except (AccessError, MissingError):
                return request.redirect('/my/scouts')

        required = ['first_name', 'last_name', 'birthdate', 'section_id', 'guardian_name', 'guardian_phone',
                    'guardian_signed_by', 'guardian_signature']
        missing = [f for f in required if not post.get(f)]
        consents = ['consent_participation', 'consent_first_aid', 'consent_data', 'consent_risk', 'consent_update']
        if missing or not all(post.get(c) for c in consents):
            return self.portal_scout_register_form(
                member_id=member.id or None,
                error=_('Completa los campos obligatorios, acepta las autorizaciones y firma la ficha.'))

        section = request.env['scout.section'].sudo().browse(int(post['section_id'])).exists()
        cycle = request.env['scout.cycle'].sudo()._get_current()
        if not section or not cycle:
            return self.portal_scout_register_form(
                member_id=member.id or None, error=_('No hay un ciclo scout abierto; contacta a la jefatura.'))

        def _signature(key):
            data = post.get(key) or ''
            return data.split(',', 1)[1] if data.startswith('data:image') else False

        vals = {
            'cycle_id': cycle.id,
            'member_id': member.id or False,
            'source': 'portal',
            'state': 'submitted',
            'company_id': section.company_id.id,
            'first_name': post.get('first_name', '').strip(),
            'last_name': post.get('last_name', '').strip(),
            'last_name2': post.get('last_name2', '').strip(),
            'birthdate': post.get('birthdate'),
            'section_id': section.id,
            'street': post.get('street'),
            'guardian_id': request.env.user.partner_id.id,
            'guardian_name': post.get('guardian_name'),
            'guardian_relationship': post.get('guardian_relationship'),
            'guardian_phone': post.get('guardian_phone'),
            'guardian_phone_alt': post.get('guardian_phone_alt'),
            'guardian_email': post.get('guardian_email') or request.env.user.email,
            'guardian_street': post.get('guardian_street'),
            'medical_service': post.get('medical_service') or False,
            'medical_policy': post.get('medical_policy'),
            'blood_type': post.get('blood_type') or False,
            'medical_conditions': post.get('medical_conditions'),
            'declared_paid': bool(post.get('declared_paid')),
            'payment_reference': post.get('payment_reference'),
            'payment_date': post.get('payment_date') or False,
            'payment_amount': float(post.get('payment_amount') or 0.0),
            'consent_participation': True, 'consent_first_aid': True, 'consent_data': True,
            'consent_risk': True, 'consent_update': True,
            'consent_image': bool(post.get('consent_image')),
            'guardian_signed_by': post.get('guardian_signed_by'),
            'guardian_signature': _signature('guardian_signature'),
            'member_signed_by': post.get('member_signed_by'),
            'member_signature': _signature('member_signature'),
            'signed_date': fields.Date.context_today(request.env.user),
        }
        proof = post.get('payment_proof')
        if proof and hasattr(proof, 'read'):
            content = proof.read()
            if len(content) > MAX_PROOF_SIZE:
                return self.portal_scout_register_form(member_id=member.id or None,
                                                       error=_('El comprobante no debe superar 5 MB.'))
            vals.update({'payment_proof': base64.b64encode(content), 'payment_proof_name': proof.filename})
        try:
            registration = request.env['scout.registration'].sudo().create(vals)
        except ValidationError as e:
            return self.portal_scout_register_form(member_id=member.id or None, error=str(e))
        registration.message_post(body=_('Ficha enviada desde el portal por %s.', request.env.user.name))
        officers = request.env.ref('scout_management.group_scout_officer').sudo().all_user_ids.filtered(
            lambda u: not u.share and u.id != 1)
        todo = request.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        for user in officers[:2]:
            registration.activity_schedule(activity_type_id=todo.id if todo else False,
                                           summary=_('Revisar ficha enviada desde el portal'), user_id=user.id)
        _logger.info('Ficha scout %s enviada desde el portal por el usuario %s', registration.name, request.env.uid)
        return request.render('scout_management.portal_scout_register_done', {
            **self._prepare_portal_layout_values(), 'registration': registration, 'page_name': 'scout_register'})


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
