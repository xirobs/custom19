# -*- coding: utf-8 -*-
"""Administración del grupo desde el portal (/dirigentes/admin): secretaría, tesorería, tienda y aprobaciones
para manejarlas desde el celular sin entrar al backend.

Todo se hace con el usuario de la persona (no sudo), así que valen los mismos permisos y aprobaciones que en el
backend: secretaría (actas, acuerdos), tesorería (caja y bancos, tienda) y jefe de grupo (aprobaciones).
Solo se usa sudo para leer saldos, generar PDF y crear productos/entradas después de revisar el permiso.
"""
import base64
import logging
from datetime import date, datetime, timedelta
from urllib.parse import quote

from markupsafe import Markup

from odoo import _, fields, http
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import request
from odoo.tools import html2plaintext

from .leader_portal import ScoutLeaderPortal

_logger = logging.getLogger(__name__)

BASE = '/dirigentes/admin'
MOVE_TYPES = [('income', 'Ingreso'), ('expense', 'Egreso / gasto'), ('transfer', 'Traspaso')]
ERRORS = (AccessError, MissingError, UserError, ValidationError)


def _date(value, default=None):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date() if value else default
    except ValueError:
        return default


def _float(value, default=0.0):
    try:
        return float(str(value).replace(',', '').replace('$', '').strip() or default)
    except (TypeError, ValueError):
        return default


def _int(value):
    return int(value) if str(value or '').isdigit() else 0


class ScoutAdminPortal(http.Controller):

    # ------------------------------------------------------------------ ayudas
    @staticmethod
    def _access():
        """Qué áreas puede usar la persona (usuario interno con los grupos del módulo)."""
        user = request.env.user
        if user.share:
            return None
        acc = {
            'secretary': user.has_group('scout_management.group_scout_officer'),
            'treasury': user.has_group('scout_management.group_scout_treasurer'),
            'manager': user.has_group('scout_management.group_scout_manager'),
        }
        acc['store'] = acc['treasury']
        acc['any'] = any(acc.values())
        return acc

    def _guard(self, area):
        acc = self._access()
        if not acc or not acc.get(area):
            return None, request.redirect(BASE)
        return acc, None

    @staticmethod
    def _section():
        sections = request.env['scout.leader.portal']._sections()
        return (sections.filtered('is_leadership')[:1] or sections[:1]) or None

    def _render(self, template, **values):
        acc = self._access() or {}
        values.setdefault('acc', acc)
        values.setdefault('admin_base', BASE)
        values.setdefault('fmt_money', lambda v: '$ {:,.2f}'.format(v or 0.0))
        values.setdefault('fmt_date', lambda d: d.strftime('%d/%m/%Y') if d else '')
        return ScoutLeaderPortal()._render(template, self._section(), 'admin', **values)

    def _back(self, url, error=None, ok=None):
        return ScoutLeaderPortal()._back(url, error=error, ok=ok)

    @staticmethod
    def _err(e):
        return str(e.args[0]) if getattr(e, 'args', None) else str(e)

    @staticmethod
    def _pdf(xmlid, records=None, data=None, filename='documento.pdf'):
        pdf, _type = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
            xmlid, records.ids if records else None, data=data)
        return request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'), ('Content-Length', len(pdf)),
            ('Content-Disposition', 'inline; filename="%s"' % quote(filename))])

    @staticmethod
    def _journals():
        return request.env['account.journal'].sudo().search(
            [('type', 'in', ('cash', 'bank')), ('company_id', '=', request.env.company.id)], order='type, sequence, id')

    @staticmethod
    def _balances(journals=None):
        """Saldos con el cálculo único (mismo que la plataforma y el PDF)."""
        return request.env['scout.treasury.ledger'].balances(request.env.company)

    @staticmethod
    def _members():
        return request.env['res.partner'].sudo().search(
            [('is_scout', '=', True), ('scout_state', 'in', ('trial', 'active')),
             ('company_id', 'in', [request.env.company.id, False])], order='name')

    # ------------------------------------------------------------------ inicio
    @http.route([BASE], type='http', auth='user', website=True)
    def admin_home(self, **kw):
        acc = self._access()
        if acc is None or not acc['any']:
            return self._render('scout_management.admin_no_access')
        env = request.env
        today = fields.Date.context_today(env.user)
        stats = {}
        if acc['secretary']:
            Meeting = env['scout.meeting']
            stats['actas_draft'] = Meeting.search_count([('state', '=', 'draft')])
            stats['actas_to_approve'] = Meeting.search_count([('state', '=', 'to_approve')])
            stats['agreements'] = env['scout.meeting.agreement'].search_count(
                [('state', '=', 'pending'), ('meeting_state', '!=', 'cancel')])
        if acc['treasury']:
            stats['balances'] = self._balances()
            stats['total'] = sum(b['balance'] for b in stats['balances'])
            month_st = env['scout.treasury.ledger'].statement(today.replace(day=1), today, env.company)
            stats['income'] = month_st['total_income']
            stats['expense'] = month_st['total_expense']
            Move = env['scout.treasury.move']
            stats['moves_pending'] = Move.search_count([('state', 'in', ('draft', 'to_approve'))])
        if acc['store']:
            sales = env['scout.store.sale'].search([('state', '=', 'done'), ('date', '=', today)])
            stats['sales_today'] = sum(sales.mapped('amount_total'))
            stats['sales_count'] = len(sales)
            stats['low_stock'] = len(env['product.template'].sudo().search(
                [('scout_store_ok', '=', True), ('scout_track_qty', '=', True)]).filtered('scout_low_stock'))
        if acc['manager']:
            stats['approvals'] = sum(len(v['records']) for v in self._approvals())
        return self._render('scout_management.admin_home', stats=stats, today=today,
                            backend_url='/odoo?menu_id=%s' % env.ref('scout_management.menu_scout_root').id)

    # ------------------------------------------------------------------ secretaría: actas
    @http.route([BASE + '/actas'], type='http', auth='user', website=True)
    def admin_meetings(self, estado='abiertas', **kw):
        acc, redirect = self._guard('secretary')
        if redirect:
            return redirect
        domain = {'abiertas': [('state', 'in', ('draft', 'to_approve'))], 'aprobadas': [('state', '=', 'approved')],
                  'todas': []}.get(estado, [])
        meetings = request.env['scout.meeting'].search(domain, limit=60, order='date desc')
        return self._render('scout_management.admin_meetings', meetings=meetings, estado=estado)

    @http.route([BASE + '/actas/<int:meeting_id>'], type='http', auth='user', website=True, methods=['GET'])
    def admin_meeting(self, meeting_id, **kw):
        acc, redirect = self._guard('secretary')
        if redirect:
            return redirect
        meeting = request.env['scout.meeting'].browse(meeting_id).exists()
        if not meeting:
            return request.redirect(BASE + '/actas')
        roster = meeting.sudo()._scout_roster()
        return self._render('scout_management.admin_meeting', meeting=meeting, roster=roster,
                            local=request.env['scout.leader.portal']._local)

    @http.route([BASE + '/actas/<int:meeting_id>'], type='http', auth='user', website=True, methods=['POST'])
    def admin_meeting_save(self, meeting_id, **post):
        acc, redirect = self._guard('secretary')
        if redirect:
            return redirect
        url = '%s/actas/%s' % (BASE, meeting_id)
        meeting = request.env['scout.meeting'].browse(meeting_id).exists()
        if not meeting:
            return request.redirect(BASE + '/actas')
        form = request.httprequest.form
        try:
            vals = {}
            if meeting.state != 'approved':
                if post.get('title'):
                    vals['title'] = post['title'].strip()
                if post.get('date'):
                    vals['date'] = self._utc(post['date'])
                vals['minutes'] = post.get('minutes') or False
                vals['attendee_ids'] = [(6, 0, [int(x) for x in form.getlist('attendee') if x.isdigit()])]
            vals.update({
                'mode': post.get('mode') if post.get('mode') in ('onsite', 'online', 'hybrid') else meeting.mode,
                'location': (post.get('location') or '').strip() or False,
                'quorum': bool(post.get('quorum')),
                'next_meeting': self._utc(post['next_meeting']) if post.get('next_meeting') else False,
            })
            meeting.write(vals)
            # acuerdos existentes
            for agreement in meeting.agreement_ids:
                state = post.get('ag_state_%s' % agreement.id)
                if state in ('pending', 'done', 'cancel') and state != agreement.state:
                    agreement.state = state
            if (post.get('new_agreement') or '').strip():
                meeting.write({'agreement_ids': [(0, 0, {
                    'name': post['new_agreement'].strip(),
                    'responsible_id': _int(post.get('new_responsible')) or False,
                    'due_date': _date(post.get('new_due')) or False})]})
        except ERRORS as e:
            return self._back(url, error=self._err(e))
        return self._back(url, ok=_('Acta guardada.'))

    @staticmethod
    def _utc(value):
        """'2026-10-30T21:00' (hora local) → datetime UTC sin zona."""
        import pytz
        try:
            local = datetime.strptime(value[:16], '%Y-%m-%dT%H:%M')
        except ValueError:
            return False
        tz = pytz.timezone(request.env.user.tz or 'America/Mexico_City')
        return tz.localize(local).astimezone(pytz.utc).replace(tzinfo=None)

    @http.route([BASE + '/actas/<int:meeting_id>/<string:op>'], type='http', auth='user', website=True,
                methods=['POST'])
    def admin_meeting_op(self, meeting_id, op, **post):
        acc, redirect = self._guard('secretary')
        if redirect:
            return redirect
        url = '%s/actas/%s' % (BASE, meeting_id)
        meeting = request.env['scout.meeting'].browse(meeting_id).exists()
        methods = {'enviar': 'action_submit', 'aprobar': 'action_approve', 'regresar': 'action_reject',
                   'reabrir': 'action_reopen', 'cancelar': 'action_cancel', 'borrador': 'action_draft'}
        if not meeting or op not in methods:
            return request.redirect(BASE + '/actas')
        try:
            getattr(meeting, methods[op])()
        except ERRORS as e:
            return self._back(url, error=self._err(e))
        labels = {'enviar': _('Acta enviada a aprobación.'), 'aprobar': _('Acta aprobada.'),
                  'regresar': _('Acta regresada para correcciones.'), 'reabrir': _('Acta reabierta.'),
                  'cancelar': _('Acta cancelada.'), 'borrador': _('Acta en borrador.')}
        return self._back(url, ok=labels[op])

    @http.route([BASE + '/actas/<int:meeting_id>/pdf'], type='http', auth='user', website=True)
    def admin_meeting_pdf(self, meeting_id, **kw):
        acc, redirect = self._guard('secretary')
        if redirect:
            return redirect
        meeting = request.env['scout.meeting'].browse(meeting_id).exists()
        if not meeting:
            return request.redirect(BASE + '/actas')
        return self._pdf('scout_management.action_report_meeting', meeting, filename='Acta %s.pdf' % meeting.name)

    @http.route([BASE + '/acuerdos'], type='http', auth='user', website=True, methods=['GET', 'POST'])
    def admin_agreements(self, ver='pendientes', **post):
        acc, redirect = self._guard('secretary')
        if redirect:
            return redirect
        Agreement = request.env['scout.meeting.agreement']
        if request.httprequest.method == 'POST':
            agreement = Agreement.browse(_int(post.get('agreement_id'))).exists()
            if agreement and post.get('state') in ('pending', 'done', 'cancel'):
                try:
                    agreement.state = post['state']
                except ERRORS as e:
                    return self._back(BASE + '/acuerdos', error=self._err(e))
            return self._back('%s/acuerdos?ver=%s' % (BASE, ver), ok=_('Acuerdo actualizado.'))
        domain = [('meeting_state', '!=', 'cancel')]
        if ver == 'pendientes':
            domain.append(('state', '=', 'pending'))
        agreements = Agreement.search(domain, order='due_date asc, id desc', limit=150)
        return self._render('scout_management.admin_agreements', agreements=agreements, ver=ver,
                            today=fields.Date.context_today(request.env.user))

    # ------------------------------------------------------------------ tesorería
    @http.route([BASE + '/tesoreria'], type='http', auth='user', website=True)
    def admin_treasury(self, ver='', caja='', mes='0', **kw):
        """Caja y bancos: saldos, estado de cuenta del mes y todos los movimientos (de cualquier origen),
        con el mismo cálculo que la plataforma y el PDF del Consejo."""
        acc, redirect = self._guard('treasury')
        if redirect:
            return redirect
        today = fields.Date.context_today(request.env.user)
        from dateutil.relativedelta import relativedelta
        offset = int(mes) if str(mes).lstrip('-').isdigit() else 0
        start = today.replace(day=1) + relativedelta(months=offset)
        end = start + relativedelta(months=1, days=-1)
        journal_ids = [_int(caja)] if _int(caja) else None
        values = request.env['scout.treasury.ledger']._statement_values(start, end, journal_ids=journal_ids)
        movements = values['movements']
        if ver in ('income', 'expense', 'transfer'):
            movements = [m for m in movements if m['kind'] == ver]
        months = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre',
                  'octubre', 'noviembre', 'diciembre']
        values.pop('company', None)
        return self._render('scout_management.admin_treasury', movements_shown=movements, ver=ver, **values,
                            caja=_int(caja), mes=offset, journals=self._journals(),
                            period_label='%s %s' % (months[start.month - 1], start.year), start=start, end=end,
                            move_types=dict(MOVE_TYPES))

    @http.route([BASE + '/tesoreria/arqueo'], type='http', auth='user', website=True, methods=['GET', 'POST'])
    def admin_treasury_count(self, caja='', **post):
        """Arqueo de caja (contar el efectivo) o conciliación con Mercado Pago / banco."""
        acc, redirect = self._guard('treasury')
        if redirect:
            return redirect
        Ledger = request.env['scout.treasury.ledger']
        journals = self._journals()
        journal = journals.filtered(lambda j: j.id == _int(caja))[:1] or journals[:1]
        Count = request.env['scout.treasury.count']
        if request.httprequest.method == 'POST':
            url = '%s/tesoreria/arqueo?caja=%s' % (BASE, journal.id)
            try:
                if post.get('mp_fees'):
                    res = Ledger._scout_mp_backfill()
                    if res['count']:
                        return self._back(url, ok=_('Se registraron las comisiones de %(n)s pago(s) por %(fee)s. '
                                                    'Revisa de nuevo el saldo.', n=res['count'],
                                                    fee='$ {:,.2f}'.format(res['fee'])))
                    if res['errors']:
                        return self._back(url, error=_('Mercado Pago no respondió: %s', res['errors'][0]))
                    return self._back(url, ok=_('No hay comisiones pendientes de traer.'))
                if post.get('adjust'):
                    count = Count.browse(_int(post.get('adjust'))).exists()
                    count.action_adjust()
                    state = count.adjustment_id.state
                    return self._back(url, ok=_('Ajuste registrado.') if state == 'posted' else
                                      _('Ajuste enviado al jefe de grupo para aprobación.'))
                count = Count.create({'journal_id': journal.id, 'counted_amount': _float(post.get('counted')),
                                      'detail': (post.get('detail') or '').strip() or False})
            except ERRORS as e:
                return self._back(url, error=self._err(e))
            msg = _('Cuadra perfecto.') if count.state == 'done' else _(
                'Hay una diferencia de %s. Revisa o registra el ajuste.', '$ {:,.2f}'.format(count.difference))
            return self._back(url, ok=msg)
        history = Count.search([('journal_id', '=', journal.id)], limit=15)
        kind = Ledger._journal_kind(journal) if journal else 'cash'
        return self._render('scout_management.admin_treasury_count', journal=journal, journals=journals,
                            balance=Ledger._balance(journal) if journal else 0.0, history=history, kind=kind)

    @http.route([BASE + '/tesoreria/nuevo'], type='http', auth='user', website=True, methods=['GET', 'POST'])
    def admin_treasury_new(self, tipo='income', **post):
        acc, redirect = self._guard('treasury')
        if redirect:
            return redirect
        tipo = tipo if tipo in ('income', 'expense', 'transfer') else 'income'
        if request.httprequest.method == 'POST':
            form_url = '%s/tesoreria/nuevo?tipo=%s' % (BASE, tipo)
            try:
                vals = {
                    'move_type': tipo, 'date': _date(post.get('date'), fields.Date.context_today(request.env.user)),
                    'journal_id': _int(post.get('journal_id')),
                    'dest_journal_id': _int(post.get('dest_journal_id')) or False if tipo == 'transfer' else False,
                    'category_id': _int(post.get('category_id')) or False if tipo != 'transfer' else False,
                    'description': (post.get('description') or '').strip(),
                    'amount': _float(post.get('amount')),
                    'payment_method': post.get('payment_method') if post.get('payment_method') in (
                        'cash', 'transfer', 'card', 'other') else 'cash',
                    'reference': (post.get('reference') or '').strip() or False,
                    'member_id': _int(post.get('member_id')) or False,
                    'section_id': _int(post.get('section_id')) or False,
                }
                if not vals['description']:
                    raise UserError(_('Escribe el concepto.'))
                if not vals['journal_id']:
                    raise UserError(_('Elige la caja o banco.'))
                upload = request.httprequest.files.get('attachment')
                if upload and upload.filename:
                    data = upload.read()
                    if len(data) > 10 * 1024 * 1024:
                        raise UserError(_('El comprobante pesa más de 10 MB.'))
                    vals.update({'attachment': base64.b64encode(data), 'attachment_name': upload.filename})
                move = request.env['scout.treasury.move'].create(vals)
                if post.get('post'):
                    move.action_post()
            except ERRORS as e:
                return self._back(form_url, error=self._err(e))
            msg = {'posted': _('Movimiento %s registrado.', move.name),
                   'to_approve': _('Movimiento %s enviado al jefe de grupo para aprobación.', move.name)}.get(
                move.state, _('Movimiento %s guardado en borrador.', move.name))
            return self._back('%s/tesoreria/%s' % (BASE, move.id), ok=msg)
        categories = request.env['scout.treasury.category'].search([('category_type', '=', tipo)]) \
            if tipo != 'transfer' else request.env['scout.treasury.category']
        sections = request.env['scout.section'].sudo().search([('company_id', 'in', [request.env.company.id, False])]) \
            if 'company_id' in request.env['scout.section']._fields else request.env['scout.section'].sudo().search([])
        return self._render('scout_management.admin_treasury_form', tipo=tipo, move_types=dict(MOVE_TYPES),
                            journals=self._journals(), categories=categories, members=self._members(),
                            sections=sections, today=fields.Date.context_today(request.env.user))

    @http.route([BASE + '/tesoreria/<int:move_id>'], type='http', auth='user', website=True)
    def admin_treasury_move(self, move_id, **kw):
        acc, redirect = self._guard('treasury')
        if redirect:
            return redirect
        move = request.env['scout.treasury.move'].browse(move_id).exists()
        if not move:
            return request.redirect(BASE + '/tesoreria')
        return self._render('scout_management.admin_treasury_move', move=move, move_types=dict(MOVE_TYPES))

    @http.route([BASE + '/tesoreria/<int:move_id>/<string:op>'], type='http', auth='user', website=True,
                methods=['POST'])
    def admin_treasury_op(self, move_id, op, **post):
        acc, redirect = self._guard('treasury')
        if redirect:
            return redirect
        url = '%s/tesoreria/%s' % (BASE, move_id)
        move = request.env['scout.treasury.move'].browse(move_id).exists()
        methods = {'registrar': 'action_post', 'aprobar': 'action_approve', 'rechazar': 'action_reject',
                   'cancelar': 'action_cancel', 'borrador': 'action_draft'}
        if not move or op not in methods:
            return request.redirect(BASE + '/tesoreria')
        try:
            getattr(move, methods[op])()
        except ERRORS as e:
            return self._back(url, error=self._err(e))
        return self._back(url, ok=_('Listo: %s', dict(move._fields['state'].selection).get(move.state)))

    @http.route([BASE + '/tesoreria/<int:move_id>/comprobante'], type='http', auth='user')
    def admin_treasury_attachment(self, move_id, **kw):
        acc, redirect = self._guard('treasury')
        if redirect:
            return redirect
        move = request.env['scout.treasury.move'].browse(move_id).exists()
        if not move or not move.attachment:
            return request.not_found()
        data = base64.b64decode(move.attachment)
        name = move.attachment_name or 'comprobante'
        mimetype = 'application/pdf' if data[:4] == b'%PDF' else (
            'image/png' if data[:4] == b'\x89PNG' else 'image/jpeg')
        return request.make_response(data, headers=[('Content-Type', mimetype),
                                                    ('Content-Disposition', 'inline; filename="%s"' % quote(name))])

    @http.route([BASE + '/tesoreria/cuentas'], type='http', auth='user', website=True)
    def admin_receivables(self, ver='cobrar', origen='', **kw):
        acc, redirect = self._guard('treasury')
        if redirect:
            return redirect
        types = ('out_invoice',) if ver == 'cobrar' else ('in_invoice',)
        invoices = request.env['account.move'].search([
            ('move_type', 'in', types), ('state', '=', 'posted'),
            ('payment_state', 'in', ('not_paid', 'partial')), ('company_id', '=', request.env.company.id)],
            order='invoice_date_due asc, id', limit=300)
        if ver == 'pagar':
            invoices |= request.env['account.move'].search([
                ('move_type', 'in', types), ('state', '=', 'draft'), ('scout_approval_state', '=', 'to_approve'),
                ('company_id', '=', request.env.company.id)], limit=50)
        from ..models.scout_payment_origin import CHARGE_ORIGINS
        if ver == 'cobrar' and origen in dict(CHARGE_ORIGINS):
            invoices = invoices.filtered(lambda i: (i.scout_charge_origin or i._scout_guess_origin()[0]) == origen)
        total = sum(invoices.mapped('amount_residual'))
        return self._render('scout_management.admin_receivables', invoices=invoices, ver=ver, total=total,
                            origen=origen, origins=CHARGE_ORIGINS,
                            today=fields.Date.context_today(request.env.user))

    @http.route([BASE + '/tesoreria/balance.pdf'], type='http', auth='user', website=True)
    def admin_treasury_pdf(self, desde=None, hasta=None, **kw):
        acc, redirect = self._guard('treasury')
        if redirect:
            return redirect
        today = fields.Date.context_today(request.env.user)
        wizard = request.env['scout.treasury.report.wizard'].sudo().create({
            'date_from': _date(desde, today.replace(day=1)), 'date_to': _date(hasta, today)})
        return self._pdf('scout_management.action_report_treasury', wizard, data={'wizard_id': wizard.id},
                         filename='Balance tesoreria %s a %s.pdf' % (wizard.date_from, wizard.date_to))

    # ------------------------------------------------------------------ cobros en línea (Mercado Pago)
    @staticmethod
    def _mp_ready():
        provider = request.env['payment.provider'].sudo().search(
            [('code', '=', 'mercado_pago'), ('state', 'in', ('enabled', 'test')),
             ('company_id', '=', request.env.company.id)], limit=1)
        return provider

    @http.route([BASE + '/cobro/nuevo'], type='http', auth='user', website=True, methods=['GET', 'POST'])
    def admin_charge_new(self, **post):
        """Cobro con Mercado Pago por cualquier ingreso: registro, cuota, campamento, uniforme, donativo…"""
        acc, redirect = self._guard('treasury')
        if redirect:
            return redirect
        from ..models.scout_payment_origin import CHARGE_ORIGINS
        env = request.env
        if request.httprequest.method == 'POST':
            try:
                member = env['res.partner'].sudo().browse(_int(post.get('member_id'))).exists()
                payer_name = (post.get('payer') or '').strip()
                phone = (post.get('phone') or '').strip()
                if member:
                    payer = member.scout_guardian_id or member
                elif payer_name:
                    payer = env['res.partner'].sudo().search([('name', '=ilike', payer_name)], limit=1) or \
                        env['res.partner'].sudo().create({'name': payer_name, 'company_id': env.company.id})
                else:
                    raise UserError(_('Elige al miembro o escribe el nombre de quien paga.'))
                if phone and not payer.phone:
                    payer.sudo().phone = phone
                origin = post.get('origin') if post.get('origin') in dict(CHARGE_ORIGINS) else 'other'
                category = env['scout.treasury.category'].browse(_int(post.get('category_id'))).exists()
                concept = (post.get('concept') or '').strip() or (category.name if category else '')
                invoice = env['account.move']._scout_new_charge(
                    payer, _float(post.get('amount')), concept, origin=origin, member=member or None,
                    category=category or None, ref=(post.get('ref') or '').strip() or None)
            except ERRORS as e:
                return self._back(BASE + '/cobro/nuevo', error=self._err(e))
            return self._back('%s/cobro/%s' % (BASE, invoice.id), ok=_('Cobro creado: compártelo para que paguen.'))
        return self._render('scout_management.admin_charge_form', origins=CHARGE_ORIGINS, members=self._members(),
                            categories=env['scout.treasury.category'].search([('category_type', '=', 'income')]),
                            mp=self._mp_ready(), preset_member=_int(post.get('miembro')))

    @http.route([BASE + '/cobro/<int:invoice_id>'], type='http', auth='user', website=True)
    def admin_charge(self, invoice_id, **kw):
        acc, redirect = self._guard('treasury')
        if redirect:
            return redirect
        invoice = request.env['account.move'].browse(invoice_id).exists()
        if not invoice or invoice.move_type != 'out_invoice':
            return request.redirect(BASE + '/tesoreria/cuentas')
        link = qr = message = wa = None
        error = None
        if invoice.scout_can_pay_online:
            try:
                from ..models.scout_online_payment import scout_qr_data_uri, scout_whatsapp_phone
                link = invoice.sudo()._scout_payment_link()
                qr = scout_qr_data_uri(link)
                message = invoice.sudo()._scout_payment_message(link)
                phone = scout_whatsapp_phone(invoice.partner_id.phone or invoice.scout_member_id.phone)
                wa = 'https://wa.me/%s?text=%s' % (phone or '', quote(message))
            except ERRORS as e:
                error = self._err(e)
            except Exception as e:  # noqa: BLE001 - pasarela sin configurar
                _logger.exception('No se pudo generar la liga de pago')
                error = _('No se pudo generar la liga de Mercado Pago: %s', e)
        txs = request.env['payment.transaction'].sudo().search(
            [('invoice_ids', 'in', invoice.ids)], order='create_date desc', limit=10)
        return self._render('scout_management.admin_charge', invoice=invoice.sudo(), link=link, qr=qr,
                            message=message, wa=wa, link_error=error, txs=txs, mp=self._mp_ready())

    @http.route([BASE + '/tesoreria/en-linea'], type='http', auth='user', website=True)
    def admin_online_payments(self, estado='pagados', origen='', **kw):
        """Pagos con Mercado Pago: de quién (miembro y cuenta que pagó), de qué (origen y concepto) y cuánto."""
        acc, redirect = self._guard('treasury')
        if redirect:
            return redirect
        from ..models.scout_payment_origin import CHARGE_ORIGINS
        domain = [('provider_code', '=', 'mercado_pago'), ('company_id', '=', request.env.company.id)]
        if estado == 'pagados':
            domain.append(('state', '=', 'done'))
        elif estado == 'pendientes':
            domain.append(('state', 'in', ('draft', 'pending', 'authorized')))
        if origen in dict(CHARGE_ORIGINS):
            domain.append(('scout_charge_origin', '=', origen))
        txs = request.env['payment.transaction'].sudo().search(domain, order='create_date desc', limit=200)
        done = txs.filtered(lambda t: t.state == 'done')
        totals = {'amount': sum(done.mapped('amount')), 'fee': sum(done.mapped('scout_fee_amount')),
                  'net': sum(t.scout_net_amount or t.amount for t in done)}
        states = {'draft': 'Sin pagar', 'pending': 'Pendiente (OXXO / revisión)', 'authorized': 'Autorizado',
                  'done': 'Pagado', 'cancel': 'Cancelado', 'error': 'Rechazado'}
        return self._render('scout_management.admin_online_payments', txs=txs, totals=totals, estado=estado,
                            origen=origen, origins=CHARGE_ORIGINS, states=states, local=request.env['scout.leader.portal']._local)

    # ------------------------------------------------------------------ credenciales
    @http.route([BASE + '/credenciales'], type='http', auth='user', website=True, methods=['GET', 'POST'])
    def admin_cards(self, **post):
        """Datos del reverso de la credencial (firma del jefe, emergencias, red) e impresión por sección."""
        acc, redirect = self._guard('secretary')
        if redirect:
            return redirect
        company = request.env.company.sudo()
        if request.httprequest.method == 'POST':
            if not acc.get('manager'):
                return self._back(BASE + '/credenciales', error=_('Solo el jefe de grupo cambia estos datos.'))
            vals = {
                'scout_card_group_label': (post.get('group_label') or '').strip() or False,
                'scout_card_emergency_phone': (post.get('emergency_phone') or '').strip() or False,
                'scout_card_chief_name': (post.get('chief_name') or '').strip() or False,
                'scout_card_network': (post.get('network') or '').strip() or False,
            }
            sig = post.get('signature') or ''
            sig_file = request.httprequest.files.get('signature_file')
            if sig_file and sig_file.filename:
                data = sig_file.read()
                if len(data) > 15 * 1024 * 1024:
                    return self._back(BASE + '/credenciales', error=_('La imagen de la firma pesa más de 15 MB.'))
                vals['scout_card_chief_signature'] = base64.b64encode(data)
            elif sig.startswith('data:image/png;base64,'):
                vals['scout_card_chief_signature'] = sig.split(',', 1)[1]
            elif post.get('clear_signature'):
                vals['scout_card_chief_signature'] = False
            upload = request.httprequest.files.get('network_logo')
            if upload and upload.filename:
                vals['scout_card_network_logo'] = base64.b64encode(upload.read())
            targets = company | (company._scout_card_companies() & request.env.user.company_ids.sudo())
            targets.write(vals)
            return self._back(BASE + '/credenciales', ok=_('Datos de la credencial guardados (%s).',
                                                            ', '.join(targets.mapped('name'))))
        sections = request.env['scout.leader.portal']._group_sections()
        sample = request.env['res.partner'].sudo().search([
            ('is_scout', '=', True), ('scout_state', 'in', ('trial', 'active')), ('scout_type', '=', 'youth'),
            ('company_id', 'in', [company.id, False])], limit=1)
        Partner = request.env['res.partner'].sudo()
        status = []
        for comp in (company | company._scout_card_companies()):
            members = Partner.search_count([('is_scout', '=', True), ('company_id', '=', comp.id)])
            conf = comp._scout_card_config_company()
            status.append({'company': comp, 'members': members, 'signature': bool(conf.scout_card_chief_signature),
                           'phone': comp.scout_card_emergency_phone or conf.scout_card_emergency_phone or '',
                           'from': conf if conf != comp else False})
        return self._render('scout_management.admin_cards', company=company, sections=sections, sample=sample,
                            chief=company._scout_card_chief(), status=status)

    @http.route([BASE + '/credenciales/<string:what>.pdf'], type='http', auth='user', website=True)
    def admin_cards_pdf(self, what, seccion=None, **kw):
        acc, redirect = self._guard('secretary')
        if redirect:
            return redirect
        Partner = request.env['res.partner'].sudo()
        domain = [('is_scout', '=', True), ('scout_state', 'in', ('trial', 'active')),
                  ('company_id', 'in', [request.env.company.id, False])]
        if what == 'muestra':
            members = Partner.search(domain + [('scout_type', '=', 'youth')], limit=1)
            layout = 'card'
        else:
            section = request.env['scout.section'].sudo().browse(_int(seccion)).exists()
            if not section:
                return request.redirect(BASE + '/credenciales')
            members = Partner.search(domain + ['|', ('scout_section_id', '=', section.id),
                                               ('scout_leading_section_ids', 'in', section.ids)], order='name')
            layout = 'sheet'
        if not members:
            return self._back(BASE + '/credenciales', error=_('No hay miembros para imprimir.'))
        pdf = request.env['scout.card.pdf']._render(members, layout=layout)
        return request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'), ('Content-Length', len(pdf)),
            ('Content-Disposition', 'inline; filename="Credenciales.pdf"')])

    # ------------------------------------------------------------------ tienda
    @staticmethod
    def _store_products():
        return request.env['product.product'].sudo().search(
            [('scout_store_ok', '=', True), ('sale_ok', '=', True),
             ('company_id', 'in', [request.env.company.id, False])], order='name')

    @http.route([BASE + '/tienda'], type='http', auth='user', website=True, methods=['GET'])
    def admin_store(self, **kw):
        acc, redirect = self._guard('store')
        if redirect:
            return redirect
        from ..models.scout_store import SALE_TYPES
        return self._render('scout_management.admin_store', products=self._store_products(),
                            members=self._members(), sale_types=SALE_TYPES, journals=self._journals())

    @http.route([BASE + '/tienda/vender'], type='http', auth='user', website=True, methods=['POST'])
    def admin_store_sell(self, **post):
        acc, redirect = self._guard('store')
        if redirect:
            return redirect
        env = request.env
        try:
            lines = []
            for product in self._store_products():
                qty = _float(post.get('qty_%s' % product.id))
                if qty > 0:
                    lines.append((0, 0, {'product_id': product.id, 'quantity': qty,
                                         'price_unit': product.lst_price}))
            if not lines:
                raise UserError(_('Elige al menos un producto.'))
            member = env['res.partner'].sudo().browse(_int(post.get('member_id'))).exists()
            mode = post.get('payment_mode') if post.get('payment_mode') in ('paid', 'credit', 'online') else 'paid'
            buyer_name = (post.get('buyer') or '').strip()
            if member:
                partner = member.scout_guardian_id or member
            elif buyer_name:
                partner = env['res.partner'].sudo().search([('name', '=ilike', buyer_name)], limit=1) or \
                    env['res.partner'].sudo().create({'name': buyer_name, 'company_id': env.company.id})
            else:
                if mode != 'paid':
                    raise UserError(_('Para dejar a deber o pagar en línea elige al miembro o escribe quién compra.'))
                partner = env['res.partner'].sudo().search([('name', '=', _('Público en general')),
                                                            ('company_id', 'in', [env.company.id, False])], limit=1) \
                    or env['res.partner'].sudo().create({'name': _('Público en general'),
                                                         'company_id': env.company.id})
            method = post.get('payment_method') if post.get('payment_method') in ('cash', 'transfer', 'card') \
                else 'cash'
            journal_id = _int(post.get('journal_id')) if mode == 'paid' else 0
            if mode == 'paid' and not journal_id:
                journals = self._journals()
                wanted = 'cash' if method == 'cash' else 'bank'
                journal_id = (journals.filtered(lambda j: j.type == wanted)[:1] or journals[:1]).id
            sale_type = post.get('sale_type') or 'store'
            sale = env['scout.store.sale'].create({
                'sale_type': sale_type, 'partner_id': partner.id, 'member_id': member.id or False,
                'payment_mode': mode, 'payment_method': method,
                'journal_id': journal_id or False,
                'payment_reference': (post.get('reference') or '').strip() or False,
                'note': (post.get('note') or '').strip() or False, 'line_ids': lines,
            })
            sale.action_confirm()
        except ERRORS as e:
            request.env.cr.rollback()
            return self._back(BASE + '/tienda', error=self._err(e))
        return self._back('%s/tienda/venta/%s' % (BASE, sale.id), ok=_('Venta %s registrada.', sale.name))

    @http.route([BASE + '/tienda/ventas'], type='http', auth='user', website=True)
    def admin_store_sales(self, desde=None, hasta=None, **kw):
        acc, redirect = self._guard('store')
        if redirect:
            return redirect
        today = fields.Date.context_today(request.env.user)
        date_from, date_to = _date(desde, today), _date(hasta, today)
        sales = request.env['scout.store.sale'].search(
            [('date', '>=', date_from), ('date', '<=', date_to), ('state', '!=', 'draft')],
            order='date desc, id desc', limit=300)
        done = sales.filtered(lambda s: s.state == 'done')
        totals = {'total': sum(done.mapped('amount_total')),
                  'paid': sum(done.filtered(lambda s: s.payment_mode == 'paid').mapped('amount_total')),
                  'credit': sum(done.filtered(lambda s: s.payment_mode != 'paid').mapped('amount_total'))}
        return self._render('scout_management.admin_store_sales', sales=sales, totals=totals,
                            date_from=date_from, date_to=date_to)

    @http.route([BASE + '/tienda/ventas.pdf'], type='http', auth='user', website=True)
    def admin_store_sales_pdf(self, desde=None, hasta=None, **kw):
        acc, redirect = self._guard('store')
        if redirect:
            return redirect
        today = fields.Date.context_today(request.env.user)
        wizard = request.env['scout.daily.sales.wizard'].sudo().create(
            {'date_from': _date(desde, today), 'date_to': _date(hasta, today)})
        return self._pdf('scout_management.action_report_daily_sales', wizard, data={'wizard_id': wizard.id},
                         filename='Parte de ventas %s.pdf' % wizard.date_from)

    @http.route([BASE + '/tienda/venta/<int:sale_id>'], type='http', auth='user', website=True)
    def admin_store_sale(self, sale_id, **kw):
        acc, redirect = self._guard('store')
        if redirect:
            return redirect
        sale = request.env['scout.store.sale'].browse(sale_id).exists()
        if not sale:
            return request.redirect(BASE + '/tienda/ventas')
        return self._render('scout_management.admin_store_sale', sale=sale,
                            receipt_url=sale.sudo()._receipt_public_url())

    @http.route([BASE + '/tienda/venta/<int:sale_id>/<string:op>'], type='http', auth='user', website=True,
                methods=['POST'])
    def admin_store_sale_op(self, sale_id, op, **post):
        acc, redirect = self._guard('store')
        if redirect:
            return redirect
        url = '%s/tienda/venta/%s' % (BASE, sale_id)
        sale = request.env['scout.store.sale'].browse(sale_id).exists()
        if not sale:
            return request.redirect(BASE + '/tienda/ventas')
        try:
            if op == 'cancelar':
                sale.action_cancel()
                return self._back(url, ok=_('Venta cancelada; la mercancía regresó a existencia.'))
            if op == 'whatsapp':
                action = sale.action_send_whatsapp()
                return request.redirect(action['url'], local=False)
        except ERRORS as e:
            return self._back(url, error=self._err(e))
        return request.redirect(url)

    @http.route([BASE + '/tienda/productos'], type='http', auth='user', website=True, methods=['GET', 'POST'])
    def admin_store_products(self, **post):
        acc, redirect = self._guard('store')
        if redirect:
            return redirect
        url = BASE + '/tienda/productos'
        Template = request.env['product.template'].sudo()
        if request.httprequest.method == 'POST':
            action = post.get('action')
            try:
                if action == 'entrada':
                    tmpl = Template.browse(_int(post.get('product_tmpl_id'))).exists()
                    qty = _float(post.get('quantity'))
                    if not tmpl or not qty:
                        raise UserError(_('Elige el producto y la cantidad.'))
                    request.env['scout.store.stock.move'].sudo().create({
                        'product_tmpl_id': tmpl.id, 'move_kind': 'in' if qty > 0 else 'adjust', 'quantity': qty,
                        'unit_cost': _float(post.get('unit_cost'), tmpl.standard_price),
                        'note': (post.get('note') or '').strip() or _('Entrada desde el portal (%s)',
                                                                       request.env.user.name),
                        'company_id': request.env.company.id})
                    return self._back(url, ok=_('Existencia de %(p)s: %(q)g', p=tmpl.name,
                                                q=tmpl.scout_qty_available))
                if action == 'precio':
                    tmpl = Template.browse(_int(post.get('product_tmpl_id'))).exists()
                    if tmpl:
                        vals = {'list_price': _float(post.get('price'), tmpl.list_price)}
                        if post.get('min_qty') not in (None, ''):
                            vals['scout_min_qty'] = _float(post.get('min_qty'))
                        tmpl.write(vals)
                    return self._back(url, ok=_('Producto actualizado.'))
                if action == 'nuevo':
                    name = (post.get('name') or '').strip()
                    if not name:
                        raise UserError(_('Escribe el nombre del producto.'))
                    tmpl = Template.create({
                        'name': name, 'list_price': _float(post.get('price')),
                        'standard_price': _float(post.get('cost')), 'sale_ok': True, 'purchase_ok': False,
                        'type': 'consu', 'scout_store_ok': True,
                        'scout_sale_type': post.get('sale_type') or 'store',
                        'scout_track_qty': bool(post.get('track')), 'scout_min_qty': _float(post.get('min_qty')),
                        'company_id': request.env.company.id,
                    })
                    if _float(post.get('initial')) and tmpl.scout_track_qty:
                        tmpl.scout_initial_qty = _float(post.get('initial'))
                    return self._back(url, ok=_('Producto %s creado.', name))
            except ERRORS as e:
                return self._back(url, error=self._err(e))
        from ..models.scout_store import SALE_TYPES
        products = Template.search([('scout_store_ok', '=', True),
                                    ('company_id', 'in', [request.env.company.id, False])], order='name')
        return self._render('scout_management.admin_store_products', products=products, sale_types=SALE_TYPES)

    # ------------------------------------------------------------------ aprobaciones (jefe de grupo)
    def _approvals(self):
        env = request.env
        return [
            {'key': 'acta', 'title': _('Actas por aprobar'), 'icon': 'fa-file-text-o',
             'records': env['scout.meeting'].search([('state', '=', 'to_approve')]),
             'label': lambda r: '%s · %s' % (r.name, r.title), 'url': lambda r: '%s/actas/%s' % (BASE, r.id),
             'amount': None},
            {'key': 'mov', 'title': _('Egresos y traspasos'), 'icon': 'fa-money',
             'records': env['scout.treasury.move'].search([('state', '=', 'to_approve')]),
             'label': lambda r: '%s · %s' % (r.name, (r.description or '').splitlines()[0][:80] if r.description
                                             else ''),
             'url': lambda r: '%s/tesoreria/%s' % (BASE, r.id), 'amount': lambda r: r.amount},
            {'key': 'bill', 'title': _('Facturas de proveedor'), 'icon': 'fa-file-o',
             'records': env['account.move'].search([('scout_approval_state', '=', 'to_approve')]),
             'label': lambda r: '%s · %s' % (r.partner_id.name or '', r.ref or r.name or ''),
             'url': None, 'amount': lambda r: r.amount_total},
            {'key': 'pay', 'title': _('Pagos'), 'icon': 'fa-credit-card',
             'records': env['account.payment'].search([('scout_approval_state', '=', 'to_approve')]),
             'label': lambda r: '%s · %s' % (r.partner_id.name or '', r.memo or r.name or ''),
             'url': None, 'amount': lambda r: r.amount},
            {'key': 'ficha', 'title': _('Fichas por validar'), 'icon': 'fa-id-card-o',
             'records': env['scout.registration'].search([('state', '=', 'reviewed')]),
             'label': lambda r: '%s · %s' % (r.display_name, r.section_id.name if 'section_id' in r._fields
                                             and r.section_id else ''),
             'url': lambda r: '%s/ficha/%s.pdf' % (BASE, r.id), 'amount': None},
            {'key': 'cambio', 'title': _('Bajas y cambios de sección'), 'icon': 'fa-exchange',
             'records': env['scout.member.change'].search([('state', '=', 'to_approve')]),
             'label': lambda r: '%s · %s' % (r.display_name, (r.reason or '')[:80]), 'url': None, 'amount': None},
        ]

    APPROVAL_MODELS = {
        'acta': ('scout.meeting', 'action_approve', 'action_reject'),
        'mov': ('scout.treasury.move', 'action_approve', 'action_reject'),
        'bill': ('account.move', 'action_scout_approve', 'action_scout_reject'),
        'pay': ('account.payment', 'action_scout_approve', None),
        'ficha': ('scout.registration', 'action_validate', None),
        'cambio': ('scout.member.change', 'action_approve', 'action_reject'),
    }

    @http.route([BASE + '/aprobaciones'], type='http', auth='user', website=True, methods=['GET'])
    def admin_approvals(self, **kw):
        acc, redirect = self._guard('manager')
        if redirect:
            return redirect
        return self._render('scout_management.admin_approvals', groups=self._approvals())

    @http.route([BASE + '/aprobaciones'], type='http', auth='user', website=True, methods=['POST'])
    def admin_approvals_do(self, kind=None, record_id=None, op=None, **post):
        acc, redirect = self._guard('manager')
        if redirect:
            return redirect
        url = BASE + '/aprobaciones'
        spec = self.APPROVAL_MODELS.get(kind)
        if not spec or op not in ('aprobar', 'rechazar'):
            return request.redirect(url)
        model, approve, reject = spec
        record = request.env[model].browse(_int(record_id)).exists()
        if not record:
            return request.redirect(url)
        try:
            if op == 'aprobar':
                getattr(record, approve)()
            elif reject:
                getattr(record, reject)()
            else:
                record.write({'scout_approval_state': 'rejected'})
        except ERRORS as e:
            return self._back(url, error=self._err(e))
        return self._back(url, ok=_('Aprobado.') if op == 'aprobar' else _('Rechazado.'))

    @http.route([BASE + '/ficha/<int:reg_id>.pdf'], type='http', auth='user', website=True)
    def admin_registration_pdf(self, reg_id, **kw):
        acc, redirect = self._guard('secretary')
        if redirect:
            return redirect
        reg = request.env['scout.registration'].browse(reg_id).exists()
        if not reg:
            return request.redirect(BASE)
        return self._pdf('scout_management.action_report_registration', reg, filename='Ficha %s.pdf' % reg.display_name)
