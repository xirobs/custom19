# -*- coding: utf-8 -*-
from datetime import date
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "mierp_gantt")
class TestGanttModels(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.calendar = cls.env["mierp.gantt.calendar"].create({
            "name": "Test Mon-Fri",
            "code": "TEST",
            "workdays": "0,1,2,3,4",
            "daily_hours": 8.0,
        })
        cls.project = cls.env["mierp.gantt.project"].create({
            "name": "Test project",
            "calendar_id": cls.calendar.id,
            "date_start": "2026-05-11",
            "date_end": "2026-08-31",
        })

    def test_create_task_default_duration(self):
        t = self.env["mierp.gantt.task"].create({
            "project_id": self.project.id,
            "name": "Task A",
            "date_start": "2026-05-11",
            "date_end": "2026-05-22",
        })
        # 12 calendar days from May 11 (Mon) → May 22 (Fri) inclusive
        self.assertEqual(t.duration_days, 12.0)
        self.assertFalse(t.is_summary)

    def test_summary_rollup_via_children(self):
        parent = self.env["mierp.gantt.task"].create({
            "project_id": self.project.id,
            "name": "Phase",
            "date_start": "2026-05-11",
            "date_end": "2026-06-30",
        })
        child = self.env["mierp.gantt.task"].create({
            "project_id": self.project.id,
            "parent_id": parent.id,
            "name": "Activity",
            "date_start": "2026-05-11",
            "date_end": "2026-05-22",
        })
        parent.invalidate_recordset(["is_summary", "child_ids"])
        self.assertTrue(parent.is_summary)
        self.assertIn(child, parent.child_ids)

    def test_wbs_codes_are_sequential(self):
        t1 = self.env["mierp.gantt.task"].create({
            "project_id": self.project.id, "name": "A",
            "date_start": "2026-05-11", "date_end": "2026-05-12", "sequence": 1,
        })
        t2 = self.env["mierp.gantt.task"].create({
            "project_id": self.project.id, "name": "B",
            "date_start": "2026-05-11", "date_end": "2026-05-12", "sequence": 2,
        })
        self.env.invalidate_all()
        self.assertEqual(t1.wbs, "1")
        self.assertEqual(t2.wbs, "2")

    def test_progress_constraint(self):
        from psycopg2 import IntegrityError
        with self.assertRaises(Exception):
            self.env["mierp.gantt.task"].create({
                "project_id": self.project.id, "name": "Bad",
                "date_start": "2026-05-11", "date_end": "2026-05-12",
                "progress_pct": 150.0,
            }).flush_recordset()
