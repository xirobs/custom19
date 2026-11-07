# -*- coding: utf-8 -*-
"""Tablero de KPIs de Red Seguro Scout.

Un solo cálculo para la plataforma (todas las cifras de la empresa), el portal del asesor
(sus grupos) y el portal del grupo (solo su grupo). Devuelve números, series para gráficas
y tablas dinámicas (pivote) listas para pintar.
"""
from collections import OrderedDict, defaultdict
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import _, api, models

from .rss_common import section_for_age, INJURY_SELECTION, SECTION_SELECTION

AGE_BLOCKS = [(0, 6, '0–6'), (7, 10, '7–10'), (11, 14, '11–14'), (15, 17, '15–17'),
              (18, 21, '18–21'), (22, 200, '22 o más')]
MONTHS = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
STATE_LABELS = OrderedDict([('active', 'Asegurados'), ('draft', 'Preinscripción'), ('inactive', 'Baja')])


def _n(value):
    """13.0 -> 13; 33.3 se queda."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _age_block(age):
    for low, high, label in AGE_BLOCKS:
        if low <= (age or 0) <= high:
            return label
    return AGE_BLOCKS[-1][2]


class RssDashboard(models.AbstractModel):
    _name = 'rss.dashboard'
    _description = 'Tablero de KPIs (Red Seguro Scout)'

    @api.model
    def _scope_groups(self, group_ids=None):
        """Grupos que puede ver quien llama. Sin ``group_ids`` = todos los de la empresa."""
        Group = self.env['rss.scout.group'].with_context(active_test=False)
        domain = [('company_id', 'in', self.env.companies.ids)] if 'company_id' in Group._fields else []
        if group_ids:
            domain.append(('id', 'in', list(group_ids)))
        return Group.search(domain)

    @api.model
    def get_data(self, group_ids=None, months=12):
        """KPIs, series y pivotes. La seguridad la aplica quien llama: la plataforma con las
        reglas del usuario; los portales pasan solo los grupos permitidos y usan sudo."""
        groups = self._scope_groups(group_ids)
        today = date.today()
        month_starts = [(today.replace(day=1) - relativedelta(months=n)) for n in range(months - 1, -1, -1)]
        month_keys = [(d.year, d.month) for d in month_starts]
        month_labels = ['%s %s' % (MONTHS[m - 1], str(y)[2:]) for y, m in month_keys]

        Insured = self.env['rss.insured'].with_context(active_test=False)
        insured = Insured.search([('group_id', 'in', groups.ids)])
        active = insured.filtered(lambda i: i.state == 'active')
        sections = dict(SECTION_SELECTION)
        injuries = dict(INJURY_SELECTION)

        # --- KPIs principales
        with_cert = insured.filtered('has_certificate')
        coverage = sum(active.mapped('coverage_total')) if 'coverage_total' in Insured._fields else 0.0
        portal = insured.filtered(lambda i: i.capture_source in ('portal', 'excel', 'pdf'))
        kpis = {
            'insured': len(active),
            'draft': len(insured.filtered(lambda i: i.state == 'draft')),
            'inactive': len(insured.filtered(lambda i: i.state == 'inactive')),
            'total': len(insured),
            'groups': len(groups),
            'groups_with_insured': len(active.group_id),
            'certificates': len(with_cert),
            'certificates_pct': round(100.0 * len(with_cert) / len(insured), 1) if insured else 0.0,
            'without_certificate': len(active) - len(active & with_cert),
            'avg_age': round(sum(active.mapped('age')) / len(active), 1) if active else 0,
            'male': len(active.filtered(lambda i: i.sex == 'male')),
            'female': len(active.filtered(lambda i: i.sex == 'female')),
            'coverage_total': coverage,
            'portal_share': round(100.0 * len(portal) / len(insured), 1) if insured else 0.0,
            'new_this_month': len(insured.filtered(
                lambda i: i.date_start and (i.date_start.year, i.date_start.month) == month_keys[-1])),
        }

        # --- Por grupo (barra apilada: asegurados / preinscripción / baja)
        by_group = OrderedDict((g.id, {'name': g.name, 'short': self._short(g), 'city': g.city or '',
                                       'active': 0, 'draft': 0, 'inactive': 0, 'certificates': 0,
                                       'paid': 0.0, 'claims': 0}) for g in groups.sorted(lambda g: (g.number or 0, g.name)))
        for rec in insured:
            row = by_group.get(rec.group_id.id)
            if row:
                row[rec.state if rec.state in ('active', 'draft', 'inactive') else 'draft'] += 1
                row['certificates'] += 1 if rec.has_certificate else 0

        # --- Distribuciones de los asegurados vigentes (si no hay vigentes, de todos)
        # Distribuciones y tablas dinámicas: todos los registrados (vigentes y preinscripción), sin bajas
        base = insured.filtered(lambda i: i.state in ('active', 'draft'))
        dist_sex = OrderedDict([('Hombres', len(base.filtered(lambda i: i.sex == 'male'))),
                                ('Mujeres', len(base.filtered(lambda i: i.sex == 'female')))])
        dist_section = OrderedDict((label, 0) for _k, label in SECTION_SELECTION)
        dist_age = OrderedDict((label, 0) for _l, _h, label in AGE_BLOCKS)
        dist_city = defaultdict(int)
        pivot_group_section = OrderedDict((r['short'], OrderedDict((lab, 0) for _k, lab in SECTION_SELECTION))
                                          for r in by_group.values())
        pivot_age_sex = OrderedDict((label, {'Hombres': 0, 'Mujeres': 0}) for _l, _h, label in AGE_BLOCKS)
        for rec in base:
            sec = sections.get(rec.section or (section_for_age(rec.age) if rec.birthdate else False))
            if sec:
                dist_section[sec] += 1
                short = by_group.get(rec.group_id.id, {}).get('short')
                if short in pivot_group_section:
                    pivot_group_section[short][sec] += 1
            block = _age_block(rec.age)
            dist_age[block] += 1
            if rec.sex in ('male', 'female'):
                pivot_age_sex[block]['Hombres' if rec.sex == 'male' else 'Mujeres'] += 1
            dist_city[rec.group_id.city or _('Sin ciudad')] += 1

        # --- Altas por mes
        new_by_month = OrderedDict((k, 0) for k in month_keys)
        for rec in insured:
            if rec.date_start:
                key = (rec.date_start.year, rec.date_start.month)
                if key in new_by_month:
                    new_by_month[key] += 1

        # --- Pagos
        Payment = self.env['rss.payment']
        payments = Payment.search([('group_id', 'in', groups.ids), ('state', 'in', ('confirmed', 'remitted'))])
        paid_by_month = OrderedDict((k, 0.0) for k in month_keys)
        for pay in payments:
            if pay.date:
                key = (pay.date.year, pay.date.month)
                if key in paid_by_month:
                    paid_by_month[key] += pay.amount
            row = by_group.get(pay.group_id.id)
            if row:
                row['paid'] += pay.amount
        pending = self.env['rss.import.batch'].search([('group_id', 'in', groups.ids),
                                                       ('state', 'in', ('payment', 'verifying'))])
        kpis.update({
            'paid_total': sum(payments.mapped('amount')),
            'payments': len(payments),
            'pending_payments': len(pending),
            'pending_amount': sum(pending.mapped('amount_total')),
            'groups_without_payment': len([r for r in by_group.values() if not r['paid']]),
        })

        # --- Siniestros
        Claim = self.env['rss.claim']
        claims = Claim.search([('group_id', 'in', groups.ids)])
        claim_states = dict(Claim._fields['state']._description_selection(self.env))
        claims_by_state = OrderedDict((label, 0) for _k, label in Claim._fields['state']._description_selection(self.env))
        claims_by_injury = OrderedDict()
        claims_by_activity = OrderedDict()
        activities = dict(Claim._fields['activity_type']._description_selection(self.env))
        claims_by_month = OrderedDict((k, 0) for k in month_keys)
        for c in claims:
            claims_by_state[claim_states.get(c.state, c.state)] += 1
            inj = injuries.get(c.injury_type, c.injury_type or _('Otro'))
            claims_by_injury[inj] = claims_by_injury.get(inj, 0) + 1
            act = activities.get(c.activity_type, c.activity_type or _('Otra'))
            claims_by_activity[act] = claims_by_activity.get(act, 0) + 1
            if c.date_accident:
                key = (c.date_accident.year, c.date_accident.month)
                if key in claims_by_month:
                    claims_by_month[key] += 1
            row = by_group.get(c.group_id.id)
            if row:
                row['claims'] += 1
        kpis.update({
            'claims': len(claims),
            'claims_open': len(claims.filtered(lambda c: c.state in ('reported', 'in_process', 'approved'))),
            'claimed_total': sum(claims.mapped('amount_claimed')),
            'claims_paid_total': sum(claims.mapped('amount_paid')),
            'claim_rate': round(100.0 * len(claims) / len(active), 2) if active else 0.0,
        })

        group_rows = list(by_group.values())
        kpis = {key: _n(value) for key, value in kpis.items()}
        return {
            'kpis': kpis,
            'months': month_labels,
            'charts': {
                'by_group': {'labels': [r['short'] for r in group_rows],
                             'series': [{'name': label, 'data': [r[key] for r in group_rows]}
                                        for key, label in STATE_LABELS.items()]},
                'sex': self._series(dist_sex),
                'section': self._series(dist_section),
                'age': self._series(dist_age),
                'city': self._series(OrderedDict(sorted(dist_city.items(), key=lambda kv: -kv[1]))),
                'new_by_month': {'labels': month_labels, 'data': list(new_by_month.values())},
                'paid_by_month': {'labels': month_labels, 'data': [round(v, 2) for v in paid_by_month.values()]},
                'paid_by_group': {'labels': [r['short'] for r in group_rows], 'data': [round(r['paid'], 2) for r in group_rows]},
                'claims_by_state': self._series(claims_by_state),
                'claims_by_injury': self._series(claims_by_injury),
                'claims_by_activity': self._series(claims_by_activity),
                'claims_by_month': {'labels': month_labels, 'data': list(claims_by_month.values())},
            },
            'pivots': {
                'group_section': {'rows': list(pivot_group_section.keys()),
                                  'cols': [lab.split(' (')[0] for _k, lab in SECTION_SELECTION],
                                  'values': [list(v.values()) for v in pivot_group_section.values()]},
                'age_sex': {'rows': list(pivot_age_sex.keys()), 'cols': ['Hombres', 'Mujeres'],
                            'values': [[v['Hombres'], v['Mujeres']] for v in pivot_age_sex.values()]},
            },
            'groups': group_rows,
        }

    @staticmethod
    def _short(group):
        return ('G%s %s' % (group.number, (group.name or '').split('–')[0].split(' ', 2)[-1].strip())).strip() \
            if group.number else (group.name or '')[:22]

    @staticmethod
    def _series(ordered):
        return {'labels': list(ordered.keys()), 'data': list(ordered.values())}

    # ------------------------------------------------------------------ Excel
    KPI_LABELS = [
        ('insured', 'Asegurados vigentes'), ('draft', 'En preinscripción'), ('inactive', 'Bajas'),
        ('total', 'Registros totales'), ('groups', 'Grupos'), ('certificates', 'Con certificado'),
        ('certificates_pct', '% con certificado'), ('without_certificate', 'Vigentes sin certificado'),
        ('avg_age', 'Edad promedio'), ('male', 'Hombres'), ('female', 'Mujeres'),
        ('new_this_month', 'Altas este mes'), ('portal_share', '% cargados en portal'),
        ('paid_total', 'Cobrado ($)'), ('payments', 'Pagos'), ('pending_payments', 'Pagos en trámite'),
        ('pending_amount', 'Importe en trámite ($)'), ('groups_without_payment', 'Grupos sin pago'),
        ('claims', 'Siniestros'), ('claims_open', 'Siniestros abiertos'), ('claimed_total', 'Reclamado ($)'),
        ('claims_paid_total', 'Pagado en siniestros ($)'), ('claim_rate', 'Siniestralidad (%)'),
    ]

    @api.model
    def export_xlsx(self, data, title='Tablero Red Seguro Scout'):
        import io
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill

        wine, gold = '6E0B14', 'E9A825'
        head_font = Font(bold=True, color='FFFFFF')
        head_fill = PatternFill('solid', fgColor=wine)
        wb = Workbook()

        def sheet(name, headers, rows, first=False):
            ws = wb.active if first else wb.create_sheet()
            ws.title = name[:31]
            ws.append(headers)
            for cell in ws[1]:
                cell.font, cell.fill = head_font, head_fill
                cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            for row in rows:
                ws.append(list(row))
            for col in ws.columns:
                width = max(len(str(c.value or '')) for c in col) + 2
                ws.column_dimensions[col[0].column_letter].width = min(max(width, 10), 48)
            ws.freeze_panes = 'A2'
            return ws

        k = data['kpis']
        ws = sheet('KPIs', ['Indicador', 'Valor'], [(label, k.get(key, 0)) for key, label in self.KPI_LABELS], first=True)
        ws.insert_rows(1)
        ws['A1'] = title
        ws['A1'].font = Font(bold=True, size=14, color=wine)
        sheet('Por grupo', ['Grupo', 'Ciudad', 'Asegurados', 'Preinscripción', 'Bajas', 'Con certificado', 'Cobrado', 'Siniestros'],
              [(g['name'], g['city'], g['active'], g['draft'], g['inactive'], g['certificates'], g['paid'], g['claims'])
               for g in data['groups']])
        for key, name in (('group_section', 'Grupo x rama'), ('age_sex', 'Edad x sexo')):
            p = data['pivots'][key]
            rows = [[p['rows'][i]] + p['values'][i] + [sum(p['values'][i])] for i in range(len(p['rows']))]
            rows.append(['Total'] + [sum(r[c] for r in p['values']) for c in range(len(p['cols']))]
                        + [sum(sum(r) for r in p['values'])])
            ws = sheet(name, [''] + p['cols'] + ['Total'], rows)
            for cell in ws[ws.max_row]:
                cell.font = Font(bold=True)
        ch = data['charts']
        sheet('Por mes', ['Mes', 'Altas', 'Cobrado', 'Siniestros'],
              zip(data['months'], ch['new_by_month']['data'], ch['paid_by_month']['data'], ch['claims_by_month']['data']))
        dist_rows = []
        for key, label in (('sex', 'Sexo'), ('section', 'Rama'), ('age', 'Edad'), ('city', 'Ciudad'),
                           ('claims_by_state', 'Siniestros por estatus'), ('claims_by_injury', 'Siniestros por lesión'),
                           ('claims_by_activity', 'Siniestros por actividad')):
            for name, value in zip(ch[key]['labels'], ch[key]['data']):
                dist_rows.append((label, name, value))
        sheet('Distribuciones', ['Concepto', 'Valor', 'Cantidad'], dist_rows)
        for ws in wb.worksheets:
            ws.sheet_properties.tabColor = gold
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()
