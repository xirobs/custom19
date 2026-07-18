# -*- coding: utf-8 -*-
"""Import a Microsoft Project ``.xml`` (MSPDI) file into a Gantt project.

Implementation is intentionally compact — the format is well documented and
we only consume the subset MS Project actually emits in current versions.
"""
import base64
import io
import logging
from datetime import datetime, date

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# MSPDI uses these link type codes:
#   0 = FF, 1 = FS, 2 = SF, 3 = SS
_MSP_TYPE = {"0": "FF", "1": "FS", "2": "SF", "3": "SS"}


class GanttImportMSProject(models.TransientModel):
    _name = "mierp.gantt.import.msproject"
    _description = "Import MSPDI XML"

    project_id = fields.Many2one("mierp.gantt.project", required=True)
    file_data = fields.Binary(string="MSPDI .xml", required=True)
    file_name = fields.Char()
    replace_existing = fields.Boolean(
        string="Replace existing tasks",
        default=False,
        help="If checked, all current tasks/dependencies in the project are deleted before import.",
    )

    def _parse_iso(self, txt):
        if not txt:
            return None
        try:
            return datetime.fromisoformat(txt.replace("Z", "+00:00")).date()
        except Exception:
            try:
                return datetime.strptime(txt[:10], "%Y-%m-%d").date()
            except Exception:
                return None

    def action_import(self):
        self.ensure_one()
        try:
            from lxml import etree
        except ImportError:
            raise UserError(_("python lxml is required for MSPDI import."))

        raw = base64.b64decode(self.file_data)
        try:
            root = etree.fromstring(raw)
        except etree.XMLSyntaxError as exc:
            raise UserError(_("Invalid XML: %s") % exc)

        ns = {"m": "http://schemas.microsoft.com/project"}
        # Strip default namespace lookup tolerance.
        def _findtext(el, path):
            n = el.find(path, ns)
            if n is None:
                n = el.find(path.replace("m:", ""))
            return n.text if n is not None else None

        Task = self.env["mierp.gantt.task"]
        Dep = self.env["mierp.gantt.dependency"]

        if self.replace_existing:
            Dep.search([("project_id", "=", self.project_id.id)]).unlink()
            Task.search([("project_id", "=", self.project_id.id)]).unlink()

        uid_to_id = {}
        tasks_el = root.find("m:Tasks", ns)
        if tasks_el is None:
            tasks_el = root.find("Tasks")
        if tasks_el is None:
            raise UserError(_("No <Tasks> section found."))

        task_nodes = tasks_el.findall("m:Task", ns)
        if not task_nodes:
            task_nodes = tasks_el.findall("Task")
        # First pass: create tasks (no parent linkage yet — MSPDI uses
        # OutlineLevel rather than parent UID).
        for task_el in task_nodes:
            uid = _findtext(task_el, "m:UID") or _findtext(task_el, "UID")
            name = _findtext(task_el, "m:Name") or _findtext(task_el, "Name") or "Task"
            start = self._parse_iso(_findtext(task_el, "m:Start") or _findtext(task_el, "Start"))
            finish = self._parse_iso(_findtext(task_el, "m:Finish") or _findtext(task_el, "Finish"))
            pct_raw = _findtext(task_el, "m:PercentComplete") or _findtext(task_el, "PercentComplete")
            milestone_raw = _findtext(task_el, "m:Milestone") or _findtext(task_el, "Milestone")
            if uid in (None, "0"):
                # MSPDI puts a project-level "0" task that we skip.
                continue
            rec = Task.create(
                {
                    "project_id": self.project_id.id,
                    "name": name,
                    "date_start": start or self.project_id.date_start,
                    "date_end": finish or self.project_id.date_start,
                    "progress_pct": float(pct_raw) if pct_raw else 0.0,
                    "is_milestone": milestone_raw == "1",
                    "sequence": int(uid) * 10 if uid.isdigit() else 10,
                }
            )
            uid_to_id[uid] = rec.id

        # Second pass: PredecessorLink → mierp.gantt.dependency.
        created_links = 0
        for task_el in task_nodes:
            uid = _findtext(task_el, "m:UID") or _findtext(task_el, "UID")
            successor_id = uid_to_id.get(uid)
            if not successor_id:
                continue
            link_nodes = task_el.findall("m:PredecessorLink", ns)
            if not link_nodes:
                link_nodes = task_el.findall("PredecessorLink")
            for link_el in link_nodes:
                pred_uid = _findtext(link_el, "m:PredecessorUID") or _findtext(link_el, "PredecessorUID")
                t_code = _findtext(link_el, "m:Type") or _findtext(link_el, "Type") or "1"
                lag_raw = _findtext(link_el, "m:LinkLag") or _findtext(link_el, "LinkLag") or "0"
                pred_id = uid_to_id.get(pred_uid)
                if not pred_id:
                    continue
                Dep.create(
                    {
                        "predecessor_task_id": pred_id,
                        "successor_task_id": successor_id,
                        "type": _MSP_TYPE.get(t_code, "FS"),
                        # MSPDI lag = tenths of minute (10×min → 1 min); for
                        # daily granularity we divide by (60×8×10 = 4800).
                        "lag_days": float(lag_raw) / 4800.0,
                    }
                )
                created_links += 1

        msg = _("Imported %s tasks and %s dependencies.") % (
            len(uid_to_id), created_links
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"type": "success", "message": msg, "sticky": False},
        }
