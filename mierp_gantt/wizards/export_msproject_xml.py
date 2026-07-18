# -*- coding: utf-8 -*-
"""Server-side MSPDI export. The OWL component also ships a JS implementation
for a no-roundtrip download, but we keep this Python entry point so the
``Action → Export MSP`` server action works regardless of the frontend state.
"""
import base64
import logging
from datetime import datetime, time

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


_MSP_TYPE = {"FF": "0", "FS": "1", "SF": "2", "SS": "3"}


def _iso_dt(d):
    if not d:
        return ""
    if hasattr(d, "isoformat"):
        return datetime.combine(d, time(8, 0)).isoformat()
    return str(d)


class GanttExportMSProject(models.TransientModel):
    _name = "mierp.gantt.export.msproject"
    _description = "Export MSPDI XML"

    project_id = fields.Many2one("mierp.gantt.project", required=True)
    file_name = fields.Char(default="gantt.xml")
    file_data = fields.Binary(string="Generated File")
    state = fields.Selection(
        [("draft", "Draft"), ("done", "Done")], default="draft"
    )

    def _build_xml(self):
        self.ensure_one()
        project = self.project_id
        try:
            from lxml import etree
        except ImportError:
            raise UserError(_("python lxml is required to build MSPDI XML."))

        ns = "http://schemas.microsoft.com/project"
        E = etree.Element
        root = E("Project", nsmap={None: ns})

        def add(parent, tag, text=None):
            el = etree.SubElement(parent, "{%s}%s" % (ns, tag))
            if text is not None:
                el.text = str(text)
            return el

        add(root, "Title", project.name or "")
        add(root, "StartDate", _iso_dt(project.date_start))
        add(root, "FinishDate", _iso_dt(project.date_end))
        add(root, "ScheduleFromStart", "1")

        cals = add(root, "Calendars")
        cal = add(cals, "Calendar")
        add(cal, "UID", "1")
        add(cal, "Name", project.calendar_id.name if project.calendar_id else "Standard")
        add(cal, "IsBaseCalendar", "1")

        tasks = add(root, "Tasks")
        # MSPDI expects a sentinel UID 0 task.
        sentinel = add(tasks, "Task")
        add(sentinel, "UID", "0")
        add(sentinel, "ID", "0")
        add(sentinel, "Name", project.name or "")
        add(sentinel, "Type", "1")
        add(sentinel, "IsNull", "0")
        add(sentinel, "Summary", "1")
        add(sentinel, "Start", _iso_dt(project.date_start))
        add(sentinel, "Finish", _iso_dt(project.date_end))

        ordered = project.task_ids.sorted(key=lambda r: (r.sequence, r.id))
        for idx, t in enumerate(ordered, start=1):
            te = add(tasks, "Task")
            add(te, "UID", str(t.id))
            add(te, "ID", str(idx))
            add(te, "Name", t.name or "")
            add(te, "Type", "1")
            add(te, "IsNull", "0")
            add(te, "Summary", "1" if t.is_summary else "0")
            add(te, "Milestone", "1" if t.is_milestone else "0")
            add(te, "Start", _iso_dt(t.date_start))
            add(te, "Finish", _iso_dt(t.date_end))
            # MSPDI Duration uses ISO-8601 PnDT0H0M0S; we store working-day
            # spans roughly with 8h/day.
            hours = int(round((t.duration_days or 0) * 8))
            add(te, "Duration", f"PT{hours}H0M0S")
            add(te, "PercentComplete", str(int(t.progress_pct or 0)))

            for dep in t.predecessor_ids:
                lk = add(te, "PredecessorLink")
                add(lk, "PredecessorUID", str(dep.predecessor_task_id.id))
                add(lk, "Type", _MSP_TYPE.get(dep.type, "1"))
                add(lk, "LinkLag", str(int(dep.lag_days * 4800)))

        return etree.tostring(
            root, xml_declaration=True, encoding="UTF-8", pretty_print=True
        )

    def action_generate(self):
        self.ensure_one()
        xml = self._build_xml()
        self.write(
            {
                "file_data": base64.b64encode(xml),
                "file_name": "%s.xml" % (self.project_id.name or "gantt").replace(" ", "_"),
                "state": "done",
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "mierp.gantt.export.msproject",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
