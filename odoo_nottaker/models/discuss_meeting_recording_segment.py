# -*- coding: utf-8 -*-
import base64
import logging
import threading
from odoo import models, fields, api, SUPERUSER_ID, _
from odoo.exceptions import UserError
from odoo.modules.registry import Registry
from ..services.gemini_service import GeminiService

_logger = logging.getLogger(__name__)


class DiscussMeetingRecordingSegment(models.Model):
    _name = 'discuss.meeting.recording.segment'
    _description = 'Discuss Meeting Recording Segment'
    _order = 'recording_id, sequence'

    recording_id = fields.Many2one('discuss.meeting.recording', string='Meeting Recording',
                                    required=True, ondelete='cascade')
    sequence = fields.Integer(string='Segment Number', required=True)
    is_last = fields.Boolean(string='Last Segment of the Meeting', default=False)
    start_offset = fields.Float(string='Start Offset (seconds)', default=0.0,
                                 help='How far into the meeting this segment starts.')

    audio_attachment_id = fields.Many2one('ir.attachment', string='Segment File', ondelete='cascade')

    state = fields.Selection([
        ('draft', 'Uploaded'),
        ('processing', 'Processing (Gemini AI)'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ], string='Status', default='draft')

    transcript = fields.Html(string='Segment Transcript', sanitize=False)
    error_message = fields.Text(string='Processing Error')

    prompt_tokens = fields.Integer(string='Input Tokens', default=0,
                                    help='Tokens Gemini charged for the media + prompt sent for this segment.')
    output_tokens = fields.Integer(string='Output Tokens', default=0,
                                    help='Tokens Gemini charged for the generated transcript.')
    total_tokens = fields.Integer(string='Total Tokens', default=0)

    def action_process_segment(self, speaker_clips=None):
        """Kicks off this segment's (slow) Gemini call on a background thread, then checks
        whether the parent recording is ready to be finalized once this segment is done -
        mirrors discuss.meeting.recording's own background-processing approach so a segment
        upload/retry never blocks the request or the websocket."""
        for seg in self:
            if not seg.audio_attachment_id or not seg.audio_attachment_id.datas:
                raise UserError(_("No audio file attached to process."))
            seg.state = 'processing'
            seg.error_message = False
        self.env.cr.commit()

        for seg in self:
            threading.Thread(
                target=self._process_segment_thread,
                args=(self.env.cr.dbname, seg.id, speaker_clips),
                daemon=True,
            ).start()

    @api.model
    def _process_segment_thread(self, db_name, segment_id, speaker_clips):
        registry = Registry(db_name)
        with registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            segment = env['discuss.meeting.recording.segment'].browse(segment_id)
            try:
                segment._do_process_segment(speaker_clips=speaker_clips)
            except Exception:
                _logger.exception("Background processing failed for segment %s", segment_id)
                segment.write({'state': 'failed', 'error_message': 'Unexpected error, see server logs.'})
            cr.commit()

            # Whether this segment succeeded or failed, the meeting recording as a whole may
            # now be ready to finalize (or may need to report a partial failure).
            try:
                segment.recording_id._check_and_finalize()
                cr.commit()
            except Exception:
                _logger.exception("Finalize check failed for recording %s", segment.recording_id.id)

    def _do_process_segment(self, speaker_clips=None):
        for seg in self:
            audio_bytes = base64.b64decode(seg.audio_attachment_id.datas)
            mimetype = seg.audio_attachment_id.mimetype or 'audio/webm'
            try:
                result = GeminiService.process_audio(self.env, audio_bytes, mimetype=mimetype, speaker_clips=speaker_clips)
                seg.write({
                    'transcript': result.get('transcript'),
                    'state': 'completed',
                    'error_message': False,
                    'prompt_tokens': result.get('prompt_tokens', 0),
                    'output_tokens': result.get('output_tokens', 0),
                    'total_tokens': result.get('total_tokens', 0),
                })
                self.env['discuss.meeting.recording.usage.log'].log_call(seg.recording_id, 'segment', result)
            except Exception as e:
                _logger.exception("Error processing segment %s: %s", seg.id, str(e))
                seg.write({
                    'state': 'failed',
                    'error_message': str(e),
                })
