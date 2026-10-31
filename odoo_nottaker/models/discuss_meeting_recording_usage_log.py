# -*- coding: utf-8 -*-
from odoo import models, fields, api


class DiscussMeetingRecordingUsageLog(models.Model):
    _name = 'discuss.meeting.recording.usage.log'
    _description = 'Gemini API Usage Log'
    _order = 'create_date desc'

    # kept even if the recording is later deleted, so historical usage/cost reporting doesn't
    # silently lose rows when someone cleans up old recordings.
    recording_id = fields.Many2one('discuss.meeting.recording', string='Meeting Recording', ondelete='set null')

    call_type = fields.Selection([
        ('segment', 'Segment Transcription'),
        ('legacy', 'Single-File Transcription'),
        ('synthesis', 'Summary Synthesis'),
    ], string='Call Type', required=True)

    model = fields.Char(string='Gemini Model', required=True)

    prompt_tokens = fields.Integer(string='Input Tokens', default=0)
    output_tokens = fields.Integer(string='Output Tokens', default=0)
    total_tokens = fields.Integer(string='Total Tokens', default=0)
    estimated_cost = fields.Float(string='Estimated Cost ($)', default=0.0, digits=(16, 4))

    @api.model
    def log_call(self, recording, call_type, usage):
        """Writes one usage-log row for a single Gemini API call. `usage` is the dict returned
        by GeminiService (has 'model', 'prompt_tokens', 'output_tokens', 'total_tokens')."""
        prompt_tokens = usage.get('prompt_tokens', 0)
        output_tokens = usage.get('output_tokens', 0)
        cost = recording._estimate_cost(prompt_tokens, output_tokens) if recording else 0.0
        self.sudo().create({
            'recording_id': recording.id if recording else False,
            'call_type': call_type,
            'model': usage.get('model') or 'unknown',
            'prompt_tokens': prompt_tokens,
            'output_tokens': output_tokens,
            'total_tokens': usage.get('total_tokens', 0),
            'estimated_cost': cost,
        })
