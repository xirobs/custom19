/** @odoo-module **/
import { addWorkingDays, isoFromDate } from "./working_days";

/** When a task moves, recursively shift all its successors. */
export function autoSchedule(taskId, newStart, newEnd, tasks, deps, calendar) {
    const updates = new Map();
    const taskMap = new Map(tasks.map(t => [t.id, { ...t }]));
    if (!taskMap.has(taskId)) return [];
    const moving = taskMap.get(taskId);
    moving.date_start = isoFromDate(newStart);
    moving.date_end = isoFromDate(newEnd);

    const queue = [taskId];
    const seen = new Set();
    while (queue.length) {
        const id = queue.shift();
        if (seen.has(id)) continue;
        seen.add(id);
        const t = taskMap.get(id);
        if (!t) continue;
        const childLinks = deps.filter(d => d.predecessor === id);
        for (const link of childLinks) {
            const succ = taskMap.get(link.successor);
            if (!succ) continue;
            const dDur = Math.max(1, Math.round(succ.duration_days || 1));
            const lag = link.lag_days || 0;
            const tStart = new Date(t.date_start);
            const tEnd   = new Date(t.date_end);
            let newSuccStart = null;
            if (link.type === "FS")
                newSuccStart = addWorkingDays(tEnd, lag + 1, calendar);
            else if (link.type === "SS")
                newSuccStart = addWorkingDays(tStart, lag, calendar);
            else if (link.type === "FF")
                newSuccStart = addWorkingDays(tEnd, lag - dDur + 1, calendar);
            else if (link.type === "SF")
                newSuccStart = addWorkingDays(tStart, lag - dDur + 1, calendar);
            if (!newSuccStart) continue;
            const succStartIso = isoFromDate(newSuccStart);
            // Only push successors forward.
            if (succStartIso <= succ.date_start) continue;
            const newSuccEnd = addWorkingDays(newSuccStart, dDur - 1, calendar);
            const newSuccEndIso = isoFromDate(newSuccEnd);
            succ.date_start = succStartIso;
            succ.date_end = newSuccEndIso;
            updates.set(succ.id, {
                id: succ.id,
                date_start: succStartIso,
                date_end: newSuccEndIso,
            });
            queue.push(succ.id);
        }
    }
    return Array.from(updates.values());
}
