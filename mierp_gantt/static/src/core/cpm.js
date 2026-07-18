/** @odoo-module **/
import { addWorkingDays, durationWorkingDays, isoFromDate } from "./working_days";
import { topologicalSort } from "./topo_sort";

/** Critical Path Method — forward + backward pass. */
export function computeCriticalPath(tasks, deps, calendar) {
    if (!tasks.length) {
        return {
            criticalIds: new Set(), projectStart: null, projectEnd: null,
            earlyStarts: new Map(), earlyFinishes: new Map(),
            lateStarts: new Map(), lateFinishes: new Map(),
            totalFloat: new Map(),
        };
    }

    const sorted = topologicalSort(tasks, deps) || tasks.map(t => t.id);
    const taskMap = new Map(tasks.map(t => [t.id, t]));

    const dur = (t) => t.is_milestone ? 0 : Math.max(1, Math.round(t.duration_days || 1));

    const projectStartIso = tasks.reduce((acc, t) =>
        !acc || (t.date_start && t.date_start < acc) ? t.date_start : acc, null);

    const earlyStart = new Map();
    const earlyFinish = new Map();

    for (const id of sorted) {
        const t = taskMap.get(id);
        if (!t) continue;
        const preds = deps.filter(d => d.successor === id);
        let es;
        if (preds.length === 0) {
            es = new Date(t.date_start || projectStartIso);
        } else {
            es = preds.reduce((acc, d) => {
                const p = taskMap.get(d.predecessor);
                if (!p) return acc;
                const pES = earlyStart.get(p.id) || new Date(p.date_start);
                const pEF = earlyFinish.get(p.id) || addWorkingDays(pES, dur(p) - 1, calendar);
                let cand;
                const lag = d.lag_days || 0;
                const dDur = dur(t);
                if (d.type === "FS") cand = addWorkingDays(pEF, lag + 1, calendar);
                else if (d.type === "SS") cand = addWorkingDays(pES, lag, calendar);
                else if (d.type === "FF") cand = addWorkingDays(pEF, lag - dDur + 1, calendar);
                else if (d.type === "SF") cand = addWorkingDays(pES, lag - dDur + 1, calendar);
                if (!acc || (cand && cand > acc)) return cand;
                return acc;
            }, null);
            if (!es) es = new Date(t.date_start);
        }
        earlyStart.set(id, es);
        const ef = t.is_milestone ? new Date(es) : addWorkingDays(es, dur(t) - 1, calendar);
        earlyFinish.set(id, ef);
    }

    const projectEnd = [...earlyFinish.values()].reduce((acc, d) =>
        !acc || d > acc ? d : acc, null) || new Date();
    const projectStart = [...earlyStart.values()].reduce((acc, d) =>
        !acc || d < acc ? d : acc, null) || new Date();

    const lateStart = new Map();
    const lateFinish = new Map();

    for (const id of [...sorted].reverse()) {
        const t = taskMap.get(id);
        if (!t) continue;
        const succs = deps.filter(d => d.predecessor === id);
        let lf;
        if (succs.length === 0) {
            lf = new Date(projectEnd);
        } else {
            lf = succs.reduce((acc, d) => {
                const s = taskMap.get(d.successor);
                if (!s) return acc;
                const sLS = lateStart.get(s.id);
                const sLF = lateFinish.get(s.id);
                if (!sLS || !sLF) return acc;
                const lag = d.lag_days || 0;
                const dDur = dur(t);
                let cand;
                if (d.type === "FS") cand = addWorkingDays(sLS, -(lag + 1), calendar);
                else if (d.type === "SS") cand = addWorkingDays(sLS, dDur - lag - 1, calendar);
                else if (d.type === "FF") cand = addWorkingDays(sLF, -lag, calendar);
                else if (d.type === "SF") cand = addWorkingDays(sLF, dDur - lag - 1, calendar);
                if (!acc || (cand && cand < acc)) return cand;
                return acc;
            }, null);
            if (!lf) lf = new Date(projectEnd);
        }
        lateFinish.set(id, lf);
        const ls = t.is_milestone ? new Date(lf) : addWorkingDays(lf, -(dur(t) - 1), calendar);
        lateStart.set(id, ls);
    }

    const totalFloat = new Map();
    const criticalIds = new Set();
    for (const id of sorted) {
        const es = earlyStart.get(id);
        const ls = lateStart.get(id);
        const f = es && ls ? durationWorkingDays(es, ls, calendar) : 0;
        totalFloat.set(id, f);
        if (f === 0) criticalIds.add(id);
    }

    const isoMap = (m) => {
        const r = new Map();
        for (const [k, v] of m.entries()) r.set(k, isoFromDate(v));
        return r;
    };
    return {
        criticalIds,
        projectStart,
        projectEnd,
        earlyStarts: isoMap(earlyStart),
        earlyFinishes: isoMap(earlyFinish),
        lateStarts: isoMap(lateStart),
        lateFinishes: isoMap(lateFinish),
        totalFloat,
    };
}
