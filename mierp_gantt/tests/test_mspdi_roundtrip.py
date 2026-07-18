# -*- coding: utf-8 -*-
import base64
from lxml import etree

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "mierp_gantt")
class TestMSPDIRoundtrip(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cal = cls.env["mierp.gantt.calendar"].create({"name": "C", "workdays": "0,1,2,3,4"})
        cls.project = cls.env["mierp.gantt.project"].create({
            "name": "Roundtrip Project",
            "calendar_id": cls.cal.id,
            "date_start": "2026-05-11", "date_end": "2026-08-31",
        })
        Task = cls.env["mierp.gantt.task"]
        cls.t1 = Task.create({
            "project_id": cls.project.id, "name": "Foundation",
            "date_start": "2026-05-11", "date_end": "2026-05-22",
            "progress_pct": 50.0,
        })
        cls.t2 = Task.create({
            "project_id": cls.project.id, "name": "Structure",
            "date_start": "2026-05-25", "date_end": "2026-06-19",
        })
        cls.env["mierp.gantt.dependency"].create({
            "predecessor_task_id": cls.t1.id,
            "successor_task_id": cls.t2.id,
            "type": "FS", "lag_days": 1.0,
        })

    def test_export_xml_is_well_formed_and_has_tasks(self):
        wiz = self.env["mierp.gantt.export.msproject"].create(
            {"project_id": self.project.id}
        )
        wiz.action_generate()
        self.assertEqual(wiz.state, "done")
        raw = base64.b64decode(wiz.file_data)
        root = etree.fromstring(raw)
        self.assertEqual(root.tag.split("}")[-1], "Project")
        ns = "{http://schemas.microsoft.com/project}"
        tasks = root.findall(f".//{ns}Task")
        # sentinel + 2 real tasks = 3
        self.assertEqual(len(tasks), 3)
        names = [t.findtext(f"{ns}Name") for t in tasks]
        self.assertIn("Foundation", names)
        self.assertIn("Structure", names)

    def test_import_recreates_tasks(self):
        # Export then re-import into a fresh project; tasks/deps come back.
        wiz_exp = self.env["mierp.gantt.export.msproject"].create(
            {"project_id": self.project.id}
        )
        wiz_exp.action_generate()

        new_project = self.env["mierp.gantt.project"].create({
            "name": "Roundtrip target", "calendar_id": self.cal.id,
            "date_start": "2026-05-11",
        })
        wiz_imp = self.env["mierp.gantt.import.msproject"].create({
            "project_id": new_project.id,
            "file_data": wiz_exp.file_data,
            "file_name": "src.xml",
            "replace_existing": True,
        })
        wiz_imp.action_import()
        new_tasks = self.env["mierp.gantt.task"].search(
            [("project_id", "=", new_project.id)]
        )
        self.assertGreaterEqual(len(new_tasks), 2)
        self.assertIn("Foundation", new_tasks.mapped("name"))
        self.assertIn("Structure", new_tasks.mapped("name"))
        new_deps = self.env["mierp.gantt.dependency"].search(
            [("project_id", "=", new_project.id)]
        )
        self.assertGreaterEqual(len(new_deps), 1)
        self.assertEqual(new_deps[0].type, "FS")
