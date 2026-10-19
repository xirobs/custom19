# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import _, SUPERUSER_ID, api, models
from odoo.exceptions import AccessError


class ScoutApprovalMixin(models.AbstractModel):
    """Utilerías para los puntos de aprobación del jefe de grupo."""
    _name = 'scout.approval.mixin'
    _description = 'Aprobaciones del jefe de grupo'

    @api.model
    def _scout_is_manager(self):
        return (self.env.su or self.env.uid == SUPERUSER_ID
                or self.env.user.has_group('scout_management.group_scout_manager'))

    @api.model
    def _scout_check_manager(self, action_label):
        if not self._scout_is_manager():
            raise AccessError(_('Solo el jefe de grupo puede %s.', action_label))

    @api.model
    def _scout_manager_users(self):
        group = self.env.ref('scout_management.group_scout_manager', raise_if_not_found=False)
        if not group:
            return self.env['res.users']
        return group.sudo().all_user_ids.filtered(lambda u: u.active and not u.share and u.id != 1)

    def _scout_request_approval(self, summary, note=''):
        """Avisa a los jefes de grupo con una actividad 'Por hacer' en cada registro."""
        return self.env['scout.approval.mixin']._scout_request_approval_for(self, summary, note)

    @api.model
    def _scout_request_approval_for(self, records, summary, note=''):
        managers = self._scout_manager_users()
        todo = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        for record in records:
            if 'activity_ids' not in record._fields:
                continue
            for user in managers[:3]:
                record.sudo().activity_schedule(
                    activity_type_id=todo.id if todo else False,
                    summary=summary, note=note, user_id=user.id)
            if 'message_post' in dir(record):
                record.message_post(body=Markup('<b>%s</b>') % _('Aprobación solicitada: %s', summary))

    def _scout_close_approval_activities(self, feedback):
        return self.env['scout.approval.mixin']._scout_close_approval_for(self, feedback)

    @api.model
    def _scout_close_approval_for(self, records, feedback):
        managers = self._scout_manager_users()
        for record in records:
            if 'activity_ids' in record._fields:
                record.sudo().activity_ids.filtered(lambda a: a.user_id in managers).action_feedback(feedback=feedback)
