# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "mierp_gantt")
class TestDependencyValidation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cal = cls.env["mierp.gantt.calendar"].create({"name": "C", "workdays": "0,1,2,3,4"})
        cls.project = cls.env["mierp.gantt.project"].create({
            "name": "P", "calendar_id": cls.cal.id, "date_start": "2026-05-11",
        })
        Task = cls.env["mierp.gantt.task"]
        cls.t1 = Task.create({"project_id": cls.project.id, "name": "T1",
                              "date_start": "2026-05-11", "date_end": "2026-05-15"})
        cls.t2 = Task.create({"project_id": cls.project.id, "name": "T2",
                              "date_start": "2026-05-18", "date_end": "2026-05-22"})
        cls.t3 = Task.create({"project_id": cls.project.id, "name": "T3",
                              "date_start": "2026-05-25", "date_end": "2026-05-29"})

    def test_basic_link_creates(self):
        d = self.env["mierp.gantt.dependency"].create({
            "predecessor_task_id": self.t1.id,
            "successor_task_id": self.t2.id,
            "type": "FS",
        })
        self.assertEqual(d.type, "FS")

    def test_self_link_rejected(self):
        with self.assertRaises(Exception):
            self.env["mierp.gantt.dependency"].create({
                "predecessor_task_id": self.t1.id,
                "successor_task_id": self.t1.id,
            }).flush_recordset()

    def test_cycle_rejected(self):
        self.env["mierp.gantt.dependency"].create({
            "predecessor_task_id": self.t1.id, "successor_task_id": self.t2.id,
        })
        self.env["mierp.gantt.dependency"].create({
            "predecessor_task_id": self.t2.id, "successor_task_id": self.t3.id,
        })
        with self.assertRaises(ValidationError):
            self.env["mierp.gantt.dependency"].create({
                "predecessor_task_id": self.t3.id, "successor_task_id": self.t1.id,
            })
