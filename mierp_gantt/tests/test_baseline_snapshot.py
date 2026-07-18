# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "mierp_gantt")
class TestBaselineSnapshot(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cal = cls.env["mierp.gantt.calendar"].create({"name": "C", "workdays": "0,1,2,3,4"})
        cls.project = cls.env["mierp.gantt.project"].create({
            "name": "P", "calendar_id": cls.cal.id, "date_start": "2026-05-11",
        })
        Task = cls.env["mierp.gantt.task"]
        cls.tasks = Task.create([
            {"project_id": cls.project.id, "name": f"T{i}",
             "date_start": "2026-05-11", "date_end": "2026-05-15", "sequence": i}
            for i in range(1, 4)
        ])

    def test_snapshot_creates_baseline_with_lines(self):
        wiz = self.env["mierp.gantt.snapshot.baseline"].create({
            "project_id": self.project.id,
            "name": "Initial baseline",
            "set_as_overlay": True,
        })
        wiz.action_create()
        baseline = self.env["mierp.gantt.baseline"].search(
            [("project_id", "=", self.project.id)], limit=1
        )
        self.assertTrue(baseline)
        self.assertTrue(baseline.is_active_overlay)
        self.assertEqual(len(baseline.line_ids), 3)
        self.assertEqual(
            sorted(l.task_id.id for l in baseline.line_ids),
            sorted(self.tasks.ids),
        )

    def test_only_one_overlay_active(self):
        Wiz = self.env["mierp.gantt.snapshot.baseline"]
        Wiz.create({"project_id": self.project.id, "name": "B1", "set_as_overlay": True}).action_create()
        Wiz.create({"project_id": self.project.id, "name": "B2", "set_as_overlay": True}).action_create()
        actives = self.env["mierp.gantt.baseline"].search(
            [("project_id", "=", self.project.id), ("is_active_overlay", "=", True)]
        )
        self.assertEqual(len(actives), 1)
        self.assertEqual(actives.name, "B2")
