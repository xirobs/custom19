/** @odoo-module **/

/** Generate an MSPDI XML string that MS Project 2019+ can import. */

const MSP_TYPE = { "FF": "0", "FS": "1", "SF": "2", "SS": "3" };

function _isoDateTime(s) {
    if (!s) return "";
    if (s.length === 10) return s + "T08:00:00";
    return s;
}

function _xmlEscape(s) {
    if (s === undefined || s === null) return "";
    return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&apos;");
}

export function exportMSPDI(project, tasks, deps, calendar) {
    const lines = [];
    lines.push('<?xml version="1.0" encoding="UTF-8"?>');
    lines.push('<Project xmlns="http://schemas.microsoft.com/project">');
    lines.push(`  <Title>${_xmlEscape(project.name || "Gantt")}</Title>`);
    if (project.date_start)
        lines.push(`  <StartDate>${_isoDateTime(project.date_start)}</StartDate>`);
    if (project.date_end)
        lines.push(`  <FinishDate>${_isoDateTime(project.date_end)}</FinishDate>`);
    lines.push("  <ScheduleFromStart>1</ScheduleFromStart>");

    lines.push("  <Calendars>");
    lines.push("    <Calendar>");
    lines.push("      <UID>1</UID>");
    lines.push(`      <Name>${_xmlEscape((calendar && calendar.name) || "Standard")}</Name>`);
    lines.push("      <IsBaseCalendar>1</IsBaseCalendar>");
    lines.push("    </Calendar>");
    lines.push("  </Calendars>");

    lines.push("  <Tasks>");
    lines.push("    <Task>");
    lines.push("      <UID>0</UID><ID>0</ID>");
    lines.push(`      <Name>${_xmlEscape(project.name || "Gantt")}</Name>`);
    lines.push("      <Type>1</Type><IsNull>0</IsNull><Summary>1</Summary>");
    if (project.date_start)
        lines.push(`      <Start>${_isoDateTime(project.date_start)}</Start>`);
    if (project.date_end)
        lines.push(`      <Finish>${_isoDateTime(project.date_end)}</Finish>`);
    lines.push("    </Task>");

    let seq = 0;
    for (const t of tasks) {
        seq += 1;
        lines.push("    <Task>");
        lines.push(`      <UID>${t.id}</UID><ID>${seq}</ID>`);
        lines.push(`      <Name>${_xmlEscape(t.name)}</Name>`);
        lines.push("      <Type>1</Type>");
        lines.push("      <IsNull>0</IsNull>");
        lines.push(`      <Summary>${t.is_summary ? "1" : "0"}</Summary>`);
        lines.push(`      <Milestone>${t.is_milestone ? "1" : "0"}</Milestone>`);
        if (t.date_start) lines.push(`      <Start>${_isoDateTime(t.date_start)}</Start>`);
        if (t.date_end)   lines.push(`      <Finish>${_isoDateTime(t.date_end)}</Finish>`);
        const hours = Math.max(0, Math.round((t.duration_days || 0) * 8));
        lines.push(`      <Duration>PT${hours}H0M0S</Duration>`);
        lines.push(`      <PercentComplete>${Math.round(t.progress_pct || 0)}</PercentComplete>`);
        const taskDeps = deps.filter(d => d.successor === t.id);
        for (const d of taskDeps) {
            lines.push("      <PredecessorLink>");
            lines.push(`        <PredecessorUID>${d.predecessor}</PredecessorUID>`);
            lines.push(`        <Type>${MSP_TYPE[d.type] || "1"}</Type>`);
            lines.push(`        <LinkLag>${Math.round((d.lag_days || 0) * 4800)}</LinkLag>`);
            lines.push("      </PredecessorLink>");
        }
        lines.push("    </Task>");
    }
    lines.push("  </Tasks>");
    lines.push("</Project>");
    return lines.join("\n");
}
