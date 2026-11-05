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


class RssGroupDashboard(RssGroupPortal):

    @http.route(['/mi-grupo/tablero'], type='http', auth='user', website=True, sitemap=False)
    def group_dashboard(self, **kw):
        member, denied = self._guard()
        if denied:
            return denied
        data = request.env['rss.dashboard'].sudo().get_data(member.group_id.ids)
        return self._render('red_seguro_scout.group_dashboard', member, 'dashboard', dash=data,
                            dash_json=json.dumps(data), money=_money, xlsx_url='/mi-grupo/tablero.xlsx',
                            filter_groups=False, grupo=False, action_url='/mi-grupo/tablero')

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
    def advisor_dashboard(self, grupo=None, **kw):
        advisor, denied = self._guard()
        if denied:
            return denied
        groups, chosen = self._dash_groups(advisor, grupo)
        data = request.env['rss.dashboard'].sudo().get_data(chosen.ids or [0])
        return self._render('red_seguro_scout.advisor_dashboard', advisor, 'dashboard', dash=data,
                            dash_json=json.dumps(data), money=_money,
                            xlsx_url='/seguro/tablero.xlsx%s' % ('?grupo=%s' % grupo if grupo else ''),
                            filter_groups=groups if len(groups) > 1 else False, grupo=grupo,
                            action_url='/seguro/tablero')

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
