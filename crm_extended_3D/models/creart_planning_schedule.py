# -*- coding: utf-8 -*-
"""Horario laboral CreArt y calendarización por ventanas de trabajo."""

from datetime import datetime, time, timedelta

import pytz

# Horario local CreArt (Lun-Sáb; domingo no laborable)
CREART_WORK_SLOTS = (
    (time(5, 0), time(7, 0)),
    (time(10, 0), time(12, 45)),
    (time(16, 0), time(18, 0)),
    (time(19, 0), time(20, 0)),
)
SUNDAY_WEEKDAY = 6


class CreartWorkScheduler:
    """Distribuye horas de trabajo solo dentro de los turnos CreArt."""

    def __init__(self, tz_name="America/Mexico_City"):
        self.tz = pytz.timezone(tz_name or "UTC")

    def _combine(self, day, hour_min):
        return self.tz.localize(datetime.combine(day, hour_min))

    def _localize_utc(self, utc_naive):
        return pytz.UTC.localize(utc_naive).astimezone(self.tz)

    def to_utc_naive(self, local_dt):
        if local_dt.tzinfo is None:
            local_dt = self.tz.localize(local_dt)
        return local_dt.astimezone(pytz.UTC).replace(tzinfo=None)

    def _slot_bounds(self, day, slot):
        start = self._combine(day, slot[0])
        end = self._combine(day, slot[1])
        return start, end

    def _first_slot_of_day(self, day):
        if day.weekday() == SUNDAY_WEEKDAY:
            return None
        return self._slot_bounds(day, CREART_WORK_SLOTS[0])[0]

    def _next_calendar_day(self, day):
        nxt = day + timedelta(days=1)
        while nxt.weekday() == SUNDAY_WEEKDAY:
            nxt += timedelta(days=1)
        return nxt

    def _slot_for_moment(self, local_dt):
        """Devuelve (inicio, fin) del turno que contiene local_dt, o None."""
        if local_dt.weekday() == SUNDAY_WEEKDAY:
            return None
        for slot in CREART_WORK_SLOTS:
            start, end = self._slot_bounds(local_dt.date(), slot)
            if start <= local_dt < end:
                return start, end
        return None

    def _next_slot_start(self, local_dt):
        """Primer instante laborable en o después de local_dt."""
        cursor_day = local_dt.date()
        guard = 0
        while guard < 400:
            guard += 1
            if cursor_day.weekday() == SUNDAY_WEEKDAY:
                cursor_day = self._next_calendar_day(cursor_day)
                continue
            for slot in CREART_WORK_SLOTS:
                start, end = self._slot_bounds(cursor_day, slot)
                if local_dt <= start:
                    return start
                if start <= local_dt < end:
                    return local_dt
            cursor_day = self._next_calendar_day(cursor_day)
            local_dt = self._first_slot_of_day(cursor_day)
        return local_dt

    def advance_to_work_time(self, utc_naive):
        """Mueve un datetime UTC al siguiente instante dentro de turno laboral."""
        local = self._localize_utc(utc_naive)
        if self._slot_for_moment(local):
            return utc_naive
        nxt = self._next_slot_start(local)
        return self.to_utc_naive(nxt)

    def schedule_block(self, start_utc_naive, duration_hours):
        """
        Programa un bloque de duration_hours respetando turnos.
        Retorna (inicio_utc, fin_utc) naive.
        """
        remaining = max(float(duration_hours or 0.0), 0.0) * 3600.0
        if remaining <= 0:
            start = self.advance_to_work_time(start_utc_naive)
            return start, start

        local_start = self._localize_utc(self.advance_to_work_time(start_utc_naive))
        block_start = local_start
        cursor = local_start

        guard = 0
        while remaining > 0.5 and guard < 5000:
            guard += 1
            if cursor.weekday() == SUNDAY_WEEKDAY:
                cursor = self._first_slot_of_day(self._next_calendar_day(cursor.date()))
                continue

            slot = self._slot_for_moment(cursor)
            if not slot:
                cursor = self._next_slot_start(cursor)
                continue

            _, slot_end = slot
            available = (slot_end - cursor).total_seconds()
            if available <= 0:
                cursor = self._next_slot_start(slot_end + timedelta(seconds=1))
                continue

            used = min(remaining, available)
            cursor += timedelta(seconds=used)
            remaining -= used

            if remaining > 0.5 and cursor >= slot_end:
                cursor = self._next_slot_start(slot_end + timedelta(seconds=1))

        return (
            self.to_utc_naive(block_start),
            self.to_utc_naive(cursor),
        )

    @staticmethod
    def work_slots_label():
        return (
            "Lun–Sáb: 05:00–07:00, 10:00–12:45, 16:00–18:00, 19:00–20:00 "
            "(domingo no laborable)"
        )
