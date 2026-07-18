/** @odoo-module **/

/**
 * Working-days arithmetic. The calendar payload comes from the controller
 * and looks like:
 *   { workdays: [0,1,2,3,4], daily_hours: 8.0,
 *     holidays: [{date:"2026-12-25", recurring:true, name:"Navidad"}, ...] }
 *
 * All functions accept Date or ISO 8601 strings and return Date objects.
 */

const ONE_DAY = 86400000;

function _toDate(d) {
    if (!d) return null;
    if (d instanceof Date) return new Date(d.getTime());
    if (typeof d === "string") return new Date(d.slice(0, 10) + "T00:00:00");
    return new Date(d);
}

function _isoDay(d) {
    return new Date(d.getFullYear(), d.getMonth(), d.getDate(), 0, 0, 0, 0);
}

function _holidaySet(calendar) {
    const set = new Set();
    const recurring = [];
    if (!calendar || !calendar.holidays) return { set, recurring };
    for (const h of calendar.holidays) {
        if (!h.date) continue;
        if (h.recurring) recurring.push(h.date);
        else set.add(h.date);
    }
    return { set, recurring };
}

export function isWorkingDay(date, calendar) {
    if (!calendar) return true;
    const d = _toDate(date);
    if (!d) return false;
    const wd = d.getDay() === 0 ? 6 : d.getDay() - 1;
    // Convert JS day (Sun=0..Sat=6) to ISO (Mon=0..Sun=6)
    const workdays = calendar.workdays && calendar.workdays.length
        ? calendar.workdays
        : [0, 1, 2, 3, 4];
    if (!workdays.includes(wd)) return false;
    const { set, recurring } = _holidaySet(calendar);
    const iso = d.toISOString().slice(0, 10);
    if (set.has(iso)) return false;
    const mmdd = iso.slice(5);
    for (const r of recurring) {
        if (r.slice(5) === mmdd) return false;
    }
    return true;
}

export function addWorkingDays(date, n, calendar) {
    const d = _isoDay(_toDate(date));
    if (!d) return null;
    if (!n) return d;
    const dir = n >= 0 ? 1 : -1;
    let remaining = Math.abs(Math.round(n));
    while (remaining > 0) {
        d.setTime(d.getTime() + dir * ONE_DAY);
        if (isWorkingDay(d, calendar)) remaining--;
        if (remaining > 4000) break; // safety
    }
    return d;
}

export function workingDaysBetween(d1, d2, calendar) {
    const a = _isoDay(_toDate(d1));
    const b = _isoDay(_toDate(d2));
    if (!a || !b) return 0;
    if (a.getTime() === b.getTime()) return 0;
    const dir = a < b ? 1 : -1;
    let count = 0;
    const cursor = new Date(a.getTime());
    let safety = 4000;
    while (cursor.getTime() !== b.getTime() && safety-- > 0) {
        cursor.setTime(cursor.getTime() + dir * ONE_DAY);
        if (isWorkingDay(cursor, calendar)) count += dir;
    }
    return count;
}

export function durationWorkingDays(start, end, calendar) {
    const a = _isoDay(_toDate(start));
    const b = _isoDay(_toDate(end));
    if (!a || !b) return 0;
    if (a > b) return 0;
    let count = 0;
    const cursor = new Date(a.getTime());
    let safety = 4000;
    while (cursor.getTime() <= b.getTime() && safety-- > 0) {
        if (isWorkingDay(cursor, calendar)) count++;
        cursor.setTime(cursor.getTime() + ONE_DAY);
    }
    return count;
}

export function isoFromDate(d) {
    if (!d) return null;
    if (typeof d === "string") return d.slice(0, 10);
    return d.toISOString().slice(0, 10);
}
