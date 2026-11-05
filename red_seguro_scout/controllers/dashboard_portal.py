# -*- coding: utf-8 -*-
"""Tablero de KPIs en los portales: el grupo ve solo su grupo; el asesor, sus grupos."""
import json

from odoo import fields, http
from odoo.http import content_disposition, request

from .advisor_portal import RssAdvisorPortal
from .group_portal import RssGroupPortal


def _money(v):
    return '$ {:,.0f}'.format(v or 0.0)


def _xlsx_response(data, filename, title):
    content = request.env['rss.dashboard'].sudo().export_xlsx(data, title=title)
    return request.make_response(content, headers=[
        ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
        ('Content-Disposition', content_disposition(filename))])


def _board_values(audience, group_ids, tablero, base_url, extra_query=''):
    """Pestañas de tableros + el tablero elegido (HTML) o los indicadores con gráficas."""
    boards = request.env['rss.board'].sudo().boards_for(audience)
    chosen = False
    if tablero != 'kpi':
        chosen = boards.filtered(lambda b: str(b.id) == str(tablero)) or boards[:1]
    values = {'boards': boards, 'board': chosen, 'board_base': base_url, 'board_query': extra_query}
    if chosen:
        values['board_html'] = chosen.render_html(group_ids or [0])
    return values


def _board_pdf(board_id, audience, group_ids):
    board = request.env['rss.board'].sudo().boards_for(audience).filtered(lambda b: b.id == int(board_id))
    if not board:
        return request.not_found()
    pdf, _fmt = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
        'red_seguro_scout.action_report_rss_board', board.ids, data={'group_ids': group_ids or [0]})
    return request.make_response(pdf, headers=[
        ('Content-Type', 'application/pdf'),
        ('Content-Disposition', content_disposition('%s.pdf' % board.name))])


class RssGroupDashboard(RssGroupPortal):

    @http.route(['/mi-grupo/tablero'], type='http', auth='user', website=True, sitemap=False)
    def group_dashboard(self, tablero=None, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        values = _board_values('group', member.group_id.ids, tablero, '/mi-grupo/tablero')
        data = request.env['rss.dashboard'].sudo().get_data(member.group_id.ids)
        return self._render('red_seguro_scout.group_dashboard', member, 'dashboard', dash=data,
                            dash_json=json.dumps(data), money=_money, xlsx_url='/mi-grupo/tablero.xlsx',
                            filter_groups=False, grupo=False, action_url='/mi-grupo/tablero', **values)

    @http.route(['/mi-grupo/tablero/<int:board_id>.pdf'], type='http', auth='user', sitemap=False)
    def group_board_pdf(self, board_id, **kw):
        member = request.env['rss.group.member']._for_user(request.env.user)
        if not member:
            return request.not_found()
        return _board_pdf(board_id, 'group', member.group_id.ids)

    @http.route(['/mi-grupo/tablero.xlsx'], type='http', auth='user', sitemap=False)
    def group_dashboard_xlsx(self, **kw):
        member = request.env['rss.group.member']._for_user(request.env.user)
        if not member:
            return request.not_found()
        data = request.env['rss.dashboard'].sudo().get_data(member.group_id.ids)
        return _xlsx_response(data, 'Tablero_%s_%s.xlsx' % ((member.group_id.number or member.group_id.id),
                                                           fields.Date.today()),
                              'Tablero · %s' % member.group_id.name)


class RssAdvisorDashboard(RssAdvisorPortal):

    def _dash_groups(self, advisor, grupo):
        groups = self._groups(advisor)
        if grupo:
            chosen = groups.filtered(lambda g: str(g.id) == str(grupo))
            if chosen:
                return groups, chosen
        return groups, groups

    @http.route(['/seguro/tablero'], type='http', auth='user', website=True, sitemap=False)
    def advisor_dashboard(self, grupo=None, tablero=None, **kw):
        advisor, denied = self._guard()
        if denied:
            return denied
        groups, chosen = self._dash_groups(advisor, grupo)
        values = _board_values('advisor', chosen.ids, tablero, '/seguro/tablero',
                               '&grupo=%s' % grupo if grupo else '')
        data = request.env['rss.dashboard'].sudo().get_data(chosen.ids or [0])
        return self._render('red_seguro_scout.advisor_dashboard', advisor, 'dashboard', dash=data,
                            dash_json=json.dumps(data), money=_money,
                            xlsx_url='/seguro/tablero.xlsx%s' % ('?grupo=%s' % grupo if grupo else ''),
                            filter_groups=groups if len(groups) > 1 else False, grupo=grupo,
                            action_url='/seguro/tablero', **values)

    @http.route(['/seguro/tablero/<int:board_id>.pdf'], type='http', auth='user', sitemap=False)
    def advisor_board_pdf(self, board_id, grupo=None, **kw):
        advisor = request.env['rss.advisor']._for_user(request.env.user)
        if not advisor:
            return request.not_found()
        _groups, chosen = self._dash_groups(advisor, grupo)
        return _board_pdf(board_id, 'advisor', chosen.ids)

    @http.route(['/seguro/tablero.xlsx'], type='http', auth='user', sitemap=False)
    def advisor_dashboard_xlsx(self, grupo=None, **kw):
        advisor = request.env['rss.advisor']._for_user(request.env.user)
        if not advisor:
            return request.not_found()
        _groups, chosen = self._dash_groups(advisor, grupo)
        data = request.env['rss.dashboard'].sudo().get_data(chosen.ids or [0])
        return _xlsx_response(data, 'Tablero_asesor_%s.xlsx' % fields.Date.today(), 'Tablero · %s' % advisor.name)


class RssBackendDashboard(http.Controller):

    @http.route(['/red-seguro-scout/tablero.xlsx'], type='http', auth='user', sitemap=False)
    def backend_dashboard_xlsx(self, grupo=None, **kw):
        if not request.env.user.has_group('red_seguro_scout.group_rss_user'):
            return request.not_found()
        ids = [int(grupo)] if grupo and str(grupo).isdigit() else None
        data = request.env['rss.dashboard'].get_data(ids)
        return _xlsx_response(data, 'Tablero_Red_Seguro_Scout_%s.xlsx' % fields.Date.today(),
                              'Tablero Red Seguro Scout')
