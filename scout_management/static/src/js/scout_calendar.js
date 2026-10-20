/** @odoo-module **/
// Vista de calendario scout: igual a la estándar, con la clase o_scout_calendar_view para darle estilo propio.
import { registry } from "@web/core/registry";
import { calendarView } from "@web/views/calendar/calendar_view";

registry.category("views").add("scout_calendar", { ...calendarView });
