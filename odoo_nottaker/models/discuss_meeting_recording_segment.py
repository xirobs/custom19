# -*- coding: utf-8 -*-
import base64
import logging
import threading
from datetime import timedelta
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
    # Audio-only copy of the segment, recorded next to the screen video: transcribing the audio
    # is ~10x cheaper/faster than sending a 4 Mbps screen video to Gemini.
    transcription_attachment_id = fields.Many2one('ir.attachment', string='Audio for Transcription',
                                                  ondelete='set null')
    # Isolated per-participant clips (attachment name = participant name), kept so a retry or
    # the recovery cron can still label the transcript with real names.
    speaker_attachment_ids = fields.Many2many('ir.attachment', 'discuss_rec_segment_speaker_rel',
                                              'segment_id', 'attachment_id', string='Speaker Clips')
    attempts = fields.Integer(string='Attempts', default=0)
    processing_start = fields.Datetime(string='Processing Since')

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

    def _speaker_clips(self):
        self.ensure_one()
        clips = []
        for att in self.speaker_attachment_ids:
            if att.datas:
                clips.append((att.description or att.name or _("Participant"), base64.b64decode(att.datas),
                              att.mimetype or 'audio/webm'))
        return clips

    def action_process_segment(self, speaker_clips=None):
        """Kicks off this segment's (slow) Gemini call on a background thread, then checks
        whether the parent recording is ready to be finalized once this segment is done.
        If the worker dies mid-call (prefork restart, timeout...), _cron_recover_segments picks
        the segment up again."""
        for seg in self:
            if not seg.audio_attachment_id or not seg.audio_attachment_id.datas:
                raise UserError(_("No audio file attached to process."))
            seg.write({'state': 'processing', 'error_message': False,
                       'processing_start': fields.Datetime.now(), 'attempts': seg.attempts + 1})
        self.env.cr.commit()

        for seg in self:
            threading.Thread(
                target=self._process_segment_thread,
                args=(self.env.cr.dbname, seg.id, None),
                daemon=True,
            ).start()

    @api.model
    def _process_segment_thread(self, db_name, segment_id, speaker_clips):
        registry = Registry(db_name)
        with registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            segment = env['discuss.meeting.recording.segment'].browse(segment_id)
            try:
                segment._do_process_segment()
            except Exception as e:
                _logger.exception("Background processing failed for segment %s", segment_id)
                cr.rollback()
                segment.write({'state': 'failed', 'error_message': str(e) or 'Unexpected error, see server logs.'})
            cr.commit()

            # Whether this segment succeeded or failed, the meeting recording as a whole may
            # now be ready to finalize (or may need to report a partial failure).
            try:
                segment.recording_id._check_and_finalize()
                cr.commit()
            except Exception:
                _logger.exception("Finalize check failed for recording %s", segment.recording_id.id)

    def _do_process_segment(self):
        for seg in self:
            attachment = seg.transcription_attachment_id or seg.audio_attachment_id
            audio_bytes = base64.b64decode(attachment.datas or b'')
            mimetype = (attachment.mimetype or 'audio/webm').split(';')[0]
            if mimetype in ('application/octet-stream', 'binary/octet-stream'):
                mimetype = 'audio/webm'
            if len(audio_bytes) < 2048:
                seg.write({'state': 'completed', 'error_message': False,
                           'transcript': "<p><em>%s</em></p>" % _("(segment without audio)")})
                continue
            try:
                result = GeminiService.process_audio(self.env, audio_bytes, mimetype=mimetype,
                                                     speaker_clips=seg._speaker_clips())
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
                    'error_message': str(e.args[0] if getattr(e, 'args', None) else e),
                })

    @api.model
    def _cron_recover_segments(self):
        """Safety net for the background threads: segments stuck in 'processing' (worker
        restarted or killed mid-call) or never started are processed again (max 3 attempts);
        recordings whose segments are all done but were never finalized get finalized."""
        limit = fields.Datetime.now() - timedelta(minutes=20)
        stuck = self.search(['|', '&', ('state', '=', 'processing'), ('processing_start', '<', limit),
                             '&', ('state', '=', 'draft'), ('create_date', '<', fields.Datetime.now() - timedelta(minutes=5))],
                            limit=10)
        for seg in stuck:
            if seg.attempts >= 3:
                seg.write({'state': 'failed', 'error_message': seg.error_message or _(
                    "Processing was interrupted 3 times (server restart or timeout).")})
                continue
            seg.write({'processing_start': fields.Datetime.now(), 'attempts': seg.attempts + 1})
            self.env.cr.commit()
            seg._do_process_segment()
            self.env.cr.commit()
        recordings = self.env['discuss.meeting.recording'].search(
            [('state', 'in', ('recording', 'processing')), ('total_segments', '>', 0)], limit=20)
        recordings._check_and_finalize()
        # some segment never arrived (empty segment skipped by the browser, upload lost):
        # finalize with what we have once nothing moved for 30 minutes
        quiet = fields.Datetime.now() - timedelta(minutes=30)
        for rec in recordings:
            segs = rec.segment_ids
            if (len(segs.filtered(lambda s: s.sequence < rec.total_segments)) < rec.total_segments
                    and segs and not segs.filtered(lambda s: s.state in ('processing', 'draft'))
                    and max(segs.mapped('write_date')) < quiet and not rec.finalize_start):
                rec.write({'state': 'processing', 'finalize_start': fields.Datetime.now()})
                self.env.cr.commit()
                rec._do_finalize(segs)
                self.env.cr.commit()
        # recordings abandoned while "recording" (browser closed): finalize after 30 min idle
        idle = self.env['discuss.meeting.recording'].search(
            [('state', '=', 'recording'), ('total_segments', '=', 0), ('segment_ids', '!=', False),
             ('write_date', '<', fields.Datetime.now() - timedelta(minutes=30))], limit=10)
        for rec in idle:
            last = rec.segment_ids.filtered(lambda s: s.write_date and s.write_date > fields.Datetime.now() - timedelta(minutes=30))
            if not last and not rec.segment_ids.filtered(lambda s: s.state == 'processing'):
                rec.total_segments = max(rec.segment_ids.mapped('sequence')) + 1
                rec._check_and_finalize()
