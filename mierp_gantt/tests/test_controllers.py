# -*- coding: utf-8 -*-
from odoo.tests.common import HttpCase, tagged


@tagged("post_install", "-at_install", "mierp_gantt")
class TestGanttControllers(HttpCase):

    def setUp(self):
        super().setUp()
        self.cal = self.env["mierp.gantt.calendar"].create({"name": "TC", "workdays": "0,1,2,3,4"})
        self.project = self.env["mierp.gantt.project"].create({
            "name": "Ctrl test", "calendar_id": self.cal.id,
            "date_start": "2026-05-11", "date_end": "2026-08-31",
        })
        self.t1 = self.env["mierp.gantt.task"].create({
            "project_id": self.project.id, "name": "Foundation",
            "date_start": "2026-05-11", "date_end": "2026-05-22",
        })

    def test_data_endpoint_returns_payload(self):
        self.authenticate("admin", "admin")
        resp = self.url_open(
            "/mierp/gantt/data",
            data='{"jsonrpc":"2.0","params":{"project_id": %d}}' % self.project.id,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json().get("result")
        self.assertTrue(payload, "data endpoint returned no result: %s" % resp.text)
        self.assertEqual(payload["project"]["id"], self.project.id)
        self.assertEqual(len(payload["tasks"]), 1)
        self.assertEqual(payload["tasks"][0]["name"], "Foundation")
