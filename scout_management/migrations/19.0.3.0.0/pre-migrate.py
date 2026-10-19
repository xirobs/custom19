# -*- coding: utf-8 -*-
"""Migración v1 -> v3: los miembros pasan de scout.member a Contactos (res.partner),
la asistencia y el programa pasan al Calendario y la tesorería a Contabilidad.

Antes de cargar los modelos nuevos se respaldan las tablas de la v1 en tablas
scout_v1_* (se conservan en la base como respaldo) y se vacían las columnas
que cambian de destino, para que el ORM pueda crear las nuevas llaves foráneas.
"""
import logging

_logger = logging.getLogger(__name__)


def _table_exists(cr, table):
    cr.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", [table])
    return bool(cr.fetchone())


def _column_exists(cr, table, column):
    cr.execute("SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
               [table, column])
    return bool(cr.fetchone())


def _backup(cr, table):
    backup = f'scout_v1_{table.removeprefix("scout_")}'
    if _table_exists(cr, table) and not _table_exists(cr, backup):
        cr.execute(f'CREATE TABLE "{backup}" AS TABLE "{table}"')
        _logger.info('Scout v1: respaldo %s -> %s', table, backup)


def _drop_fk(cr, table, column):
    cr.execute("""
        SELECT tc.constraint_name FROM information_schema.table_constraints tc
          JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
         WHERE tc.table_name = %s AND kcu.column_name = %s AND tc.constraint_type = 'FOREIGN KEY'
    """, [table, column])
    for (name,) in cr.fetchall():
        cr.execute(f'ALTER TABLE "{table}" DROP CONSTRAINT "{name}"')


def migrate(cr, version):
    if not version or not _table_exists(cr, 'scout_member'):
        return
    _logger.info('Scout: migrando datos de la versión %s', version)
    for table in ('scout_member', 'scout_member_progress', 'scout_registration', 'scout_patrol',
                  'scout_attendance_session', 'scout_attendance_line', 'scout_program', 'scout_program_activity',
                  'scout_treasury_account', 'scout_treasury_move', 'scout_obligation'):
        _backup(cr, table)

    # Columnas que apuntaban a scout.member: se vacían (se reasignan en post-migrate)
    for table, column in (('scout_member_progress', 'member_id'), ('scout_member_progress', 'leader_id'),
                          ('scout_registration', 'member_id'), ('scout_patrol', 'follow_leader_id')):
        if _column_exists(cr, table, column):
            _drop_fk(cr, table, column)
    cr.execute('DELETE FROM scout_member_progress')
    if _column_exists(cr, 'scout_registration', 'member_id'):
        cr.execute('UPDATE scout_registration SET member_id = NULL')
    if _column_exists(cr, 'scout_patrol', 'follow_leader_id'):
        cr.execute('UPDATE scout_patrol SET follow_leader_id = NULL')
    # Asistencia, tesorería e informes: se reconstruyen con la nueva estructura
    for table in ('scout_attendance_line', 'scout_treasury_move', 'scout_section_report_member'):
        if _table_exists(cr, table):
            for column in ('member_id', 'session_id', 'account_id', 'dest_account_id', 'obligation_id'):
                if _column_exists(cr, table, column):
                    _drop_fk(cr, table, column)
            cr.execute(f'DELETE FROM "{table}"')
    if _column_exists(cr, 'scout_registration', 'obligation_id'):
        _drop_fk(cr, 'scout_registration', 'obligation_id')
