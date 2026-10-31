# -*- coding: utf-8 -*-
from odoo import models, fields, api

class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    meeting_recording_ids = fields.One2many(
        'discuss.meeting.recording', 
        'channel_id', 
        string='Meeting Recordings'
    )
    meeting_recording_count = fields.Integer(
        string='Recordings Count', 
        compute='_compute_meeting_recording_count'
    )

    @api.depends('meeting_recording_ids')
    def _compute_meeting_recording_count(self):
        for channel in self:
            channel.meeting_recording_count = len(channel.meeting_recording_ids)

    def action_view_recordings(self):
        self.ensure_one()
        return {
            'name': f'Recordings - {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'discuss.meeting.recording',
            'view_mode': 'list,form',
            'domain': [('channel_id', '=', self.id)],
            'context': {'default_channel_id': self.id},
        }
