# -*- coding: utf-8 -*-
"""Migración v1 -> v3 (segunda parte): crea los contactos de los miembros y reconstruye
adelantos, fichas, equipos, asistencia y programa a partir de las tablas scout_v1_*."""
import logging
from datetime import datetime, time

import pytz

from odoo import SUPERUSER_ID, api, fields

_logger = logging.getLogger(__name__)

POSITION_BY_PATROL_ROLE = {'leader': 'scout_management.position_guide',
                           'sub': 'scout_management.position_subguide',
                           'member': 'scout_management.position_member'}
POSITION_BY_LEADER_ROLE = {'chief': 'scout_management.position_chief',
                           'assistant': 'scout_management.position_assistant',
                           'candidate': 'scout_management.position_candidate',
                           'group_chief': 'scout_management.position_group_chief',
                           'support': 'scout_management.position_scouter'}
STATE_MAP = {'trial': 'trial', 'active': 'active', 'inactive': 'inactive'}


def _table_exists(cr, table):
    cr.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", [table])
    return bool(cr.fetchone())


def _rows(cr, query, params=None):
    cr.execute(query, params or [])
    cols = [d[0] for d in cr.description]
    return [dict(zip(cols, row)) for row in cr.fetchall()]


def _utc(day, hour, tz):
    local = tz.localize(datetime.combine(day, time(hour, 0)))
    return local.astimezone(pytz.utc).replace(tzinfo=None)


def migrate(cr, version):
    if not version or not _table_exists(cr, 'scout_v1_member'):
        return
    env = api.Environment(cr, SUPERUSER_ID, {'tracking_disable': True, 'mail_create_nolog': True,
                                             'mail_notrack': True, 'scout_change_approved': True,
                                             'scout_no_autoload': True, 'no_mail_to_attendees': True})
    ref = lambda xmlid: env.ref(xmlid, raise_if_not_found=False)  # noqa: E731
    Partner = env['res.partner']
    cols = {r['column_name'] for r in _rows(cr, "SELECT column_name FROM information_schema.columns "
                                                "WHERE table_name = 'scout_v1_member'")}

    # ---------------------------------------------------------------- miembros
    mapping = {}
    for m in _rows(cr, 'SELECT * FROM scout_v1_member ORDER BY id'):
        if m['member_type'] == 'leader':
            pos = ref(POSITION_BY_LEADER_ROLE.get(m.get('leader_role') or '', 'scout_management.position_scouter'))
        else:
            pos = ref(POSITION_BY_PATROL_ROLE.get(m.get('patrol_role') or 'member'))
        vals = {
            'name': m.get('name') or m.get('first_name'),
            'is_scout': True,
            'company_type': 'person',
            'active': m.get('active', True),
            'scout_type': 'leader' if m['member_type'] == 'leader' else 'youth',
            'scout_first_name': m.get('first_name'),
            'scout_last_name': m.get('last_name'),
            'scout_last_name2': m.get('last_name2'),
            'scout_name': m.get('scout_name'),
            'scout_birthdate': m.get('birthdate'),
            'scout_gender': m.get('gender'),
            'scout_shirt_size': m.get('shirt_size'),
            'street': m.get('street'),
            'scout_section_id': m.get('section_id'),
            'scout_patrol_id': m.get('patrol_id'),
            'scout_position_id': pos.id if pos else False,
            'scout_state': STATE_MAP.get(m.get('state'), 'trial'),
            'scout_join_date': m.get('join_date'),
            'scout_leave_date': m.get('leave_date'),
            'scout_transition_date': m.get('transition_date'),
            'scout_notes': m.get('notes'),
            'scout_guardian_id': m.get('guardian_id'),
            'scout_guardian_relationship': m.get('guardian_relationship'),
            'scout_guardian_phone_alt': m.get('guardian_phone_alt'),
            'scout_guardian_same_address': not m.get('guardian_street'),
            'scout_medical_service': m.get('medical_service') if 'medical_service' in cols else False,
            'scout_medical_policy': m.get('medical_policy') if 'medical_policy' in cols else False,
            'scout_blood_type': m.get('blood_type') if 'blood_type' in cols else False,
            'scout_medical_conditions': m.get('medical_conditions') if 'medical_conditions' in cols else False,
            'company_id': False,
        }
        partner = Partner.create({k: v for k, v in vals.items() if v is not None})
        if m.get('guardian_street') and partner.scout_guardian_id:
            partner.scout_guardian_id.street = m['guardian_street']
        # Foto
        att = env['ir.attachment'].search([('res_model', '=', 'scout.member'), ('res_id', '=', m['id']),
                                           ('res_field', '=', 'image_1920')], limit=1)
        if att and att.datas:
            partner.image_1920 = att.datas
        mapping[m['id']] = partner.id
    _logger.info('Scout: %s miembros migrados a Contactos', len(mapping))

    # ---------------------------------------------------------------- adelantos
    if _table_exists(cr, 'scout_v1_member_progress'):
        Progress = env['scout.member.progress']
        for p in _rows(cr, 'SELECT * FROM scout_v1_member_progress'):
            if p['member_id'] not in mapping:
                continue
            Progress.create({'member_id': mapping[p['member_id']], 'stage_id': p['stage_id'], 'state': p['state'],
                             'date_start': p.get('date_start'), 'date_achieved': p.get('date_achieved'),
                             'leader_id': mapping.get(p.get('leader_id')), 'note': p.get('note')})

    # ---------------------------------------------------------------- fichas y equipos
    if _table_exists(cr, 'scout_v1_registration'):
        for r in _rows(cr, 'SELECT id, member_id, payment_state FROM scout_v1_registration'):
            reg = env['scout.registration'].browse(r['id']).exists()
            if not reg:
                continue
            vals = {}
            if r['member_id'] in mapping:
                vals['member_id'] = mapping[r['member_id']]
            if r.get('payment_state') in ('paid', 'partial', 'exempt'):
                vals['manual_payment_state'] = r['payment_state']
            if vals:
                reg.write(vals)
    if _table_exists(cr, 'scout_v1_patrol'):
        for p in _rows(cr, 'SELECT id, follow_leader_id FROM scout_v1_patrol WHERE follow_leader_id IS NOT NULL'):
            patrol = env['scout.patrol'].browse(p['id']).exists()
            if patrol and p['follow_leader_id'] in mapping:
                patrol.follow_leader_id = mapping[p['follow_leader_id']]

    # ---------------------------------------------------------------- asistencia -> calendario
    tz = pytz.timezone('America/Mexico_City')
    Event = env['calendar.event']
    event_by_session = {}
    type_map = {'regular': 'regular', 'camp': 'camp', 'outing': 'outing', 'special': 'special'}
    if _table_exists(cr, 'scout_v1_attendance_session'):
        for s in _rows(cr, 'SELECT * FROM scout_v1_attendance_session ORDER BY date'):
            if s.get('state') == 'cancel':
                continue
            section = env['scout.section'].browse(s['section_id']).exists()
            event = Event.create({
                'name': s.get('topic') or f'Sesión {section.display_name or ""}',
                'is_scout_event': True, 'scout_scope': 'section',
                'scout_section_ids': [fields.Command.set(section.ids)],
                'scout_activity_type': type_map.get(s.get('session_type'), 'regular'),
                'start': _utc(s['date'], 9, tz), 'stop': _utc(s['date'], 13, tz),
                'privacy': 'public', 'partner_ids': [fields.Command.set([])],
            })
            event_by_session[s['id']] = event
        lines = _rows(cr, 'SELECT * FROM scout_v1_attendance_line') \
            if _table_exists(cr, 'scout_v1_attendance_line') else []
        Line = env['scout.attendance.line']
        for line in lines:
            event = event_by_session.get(line['session_id'])
            if event and line['member_id'] in mapping:
                Line.create({'event_id': event.id, 'member_id': mapping[line['member_id']],
                             'status': line['status'], 'note': line.get('note')})
        for s in _rows(cr, "SELECT id FROM scout_v1_attendance_session WHERE state = 'done'"):
            if s['id'] in event_by_session:
                event_by_session[s['id']].write({'scout_attendance_closed': True, 'scout_done': True})
        _logger.info('Scout: %s sesiones migradas al Calendario', len(event_by_session))

    # ---------------------------------------------------------------- programa -> calendario
    if _table_exists(cr, 'scout_v1_program_activity'):
        for a in _rows(cr, 'SELECT * FROM scout_v1_program_activity ORDER BY date'):
            vals = {
                'name': a.get('name') or 'Actividad',
                'scout_theme': a.get('theme'), 'scout_objective': a.get('objective'),
                'scout_recognitions': a.get('recognitions'), 'location': a.get('location'),
                'description': f"<p>Organiza: {a['organizer']}</p>" if a.get('organizer') else False,
            }
            event = event_by_session.get(a.get('session_id'))
            if event:
                event.write({k: v for k, v in vals.items() if v})
                continue
            section = env['scout.section'].browse(a['section_id']).exists() if a.get('section_id') else None
            day_end = a.get('date_end') or a['date']
            vals.update({
                'is_scout_event': True,
                'scout_scope': 'section' if section else 'group',
                'scout_section_ids': [fields.Command.set(section.ids)] if section else False,
                'scout_activity_type': a.get('activity_type') or 'regular',
                'allday': True, 'start': datetime.combine(a['date'], time(12)),
                'stop': datetime.combine(day_end, time(12)),
                'start_date': a['date'], 'stop_date': day_end,
                'privacy': 'public', 'partner_ids': [fields.Command.set([])],
            })
            Event.create(vals)

    _logger.info('Scout: migración terminada. La tesorería de la v1 queda respaldada en las tablas '
                 'scout_v1_treasury_* y scout_v1_obligation (captura saldos iniciales en Contabilidad).')
    env['account.journal'].search([('type', 'in', ('cash', 'bank'))])._scout_setup_simple_treasury()
    from odoo.addons.scout_management.hooks import scout_load_branding
    scout_load_branding(env)
