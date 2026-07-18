/** @odoo-module **/

/** Kahn's algorithm. Returns the topological order or `null` if a cycle is
 *  detected. Edges are read from the dependency list. */
export function topologicalSort(tasks, deps) {
    const inDegree = new Map();
    const adj = new Map();
    for (const t of tasks) { inDegree.set(t.id, 0); adj.set(t.id, []); }
    for (const d of deps) {
        if (!inDegree.has(d.predecessor) || !inDegree.has(d.successor)) continue;
        adj.get(d.predecessor).push(d);
        inDegree.set(d.successor, inDegree.get(d.successor) + 1);
    }
    const queue = [];
    for (const [id, deg] of inDegree.entries()) if (deg === 0) queue.push(id);
    const sorted = [];
    while (queue.length) {
        const id = queue.shift();
        sorted.push(id);
        for (const e of adj.get(id) || []) {
            inDegree.set(e.successor, inDegree.get(e.successor) - 1);
            if (inDegree.get(e.successor) === 0) queue.push(e.successor);
        }
    }
    if (sorted.length !== tasks.length) return null;
    return sorted;
}
