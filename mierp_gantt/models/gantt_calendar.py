# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class GanttCalendar(models.Model):
    _name = "mierp.gantt.calendar"
    _description = "Gantt Working Days Calendar"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(index=True)
    country_id = fields.Many2one("res.country", string="Country")
    workdays = fields.Char(
        default="0,1,2,3,4",
        required=True,
        help="0=Monday … 6=Sunday. Comma-separated weekday indices.",
    )
    daily_hours = fields.Float(default=8.0, required=True)
    holiday_ids = fields.One2many(
        "mierp.gantt.holiday", "calendar_id", string="Holidays"
    )
    note = fields.Text()
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company
    )

    @api.constrains("workdays")
    def _check_workdays(self):
        for rec in self:
            try:
                vals = [int(x) for x in (rec.workdays or "").split(",") if x.strip()]
            except ValueError:
                raise ValidationError(_("Workdays must be a comma-separated list of integers 0..6."))
            if not vals:
                raise ValidationError(_("At least one working day is required."))
            if any(v < 0 or v > 6 for v in vals):
                raise ValidationError(_("Workday indices must be between 0 (Mon) and 6 (Sun)."))

    def to_payload(self):
        """Return JSON-serialisable dict for the OWL frontend."""
        self.ensure_one()
        return {
            "id": self.id,
            "name": self.name,
            "workdays": [int(x) for x in (self.workdays or "").split(",") if x.strip()],
            "daily_hours": self.daily_hours,
            "holidays": [
                {
                    "date": h.date.isoformat() if h.date else None,
                    "name": h.name or "",
                    "recurring": h.is_recurring,
                }
                for h in self.holiday_ids
            ],
        }


class GanttHoliday(models.Model):
    _name = "mierp.gantt.holiday"
    _description = "Gantt Calendar Holiday"
    _order = "date"

    calendar_id = fields.Many2one(
        "mierp.gantt.calendar", required=True, ondelete="cascade", index=True
    )
    date = fields.Date(required=True)
    name = fields.Char()
    is_recurring = fields.Boolean(
        string="Recurring (year over year)",
        help="When true, the day-of-year repeats every year.",
    )
