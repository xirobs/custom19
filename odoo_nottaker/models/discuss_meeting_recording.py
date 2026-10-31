# -*- coding: utf-8 -*-
import base64
import logging
import re
import threading
from odoo import models, fields, api, SUPERUSER_ID, _
from odoo.exceptions import UserError
from odoo.modules.registry import Registry
from ..services.gemini_service import GeminiService

_logger = logging.getLogger(__name__)

class DiscussMeetingRecording(models.Model):
    _name = 'discuss.meeting.recording'
    _description = 'Discuss Meeting Recording & AI Notes'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'start_time desc, id desc'

    name = fields.Char(
        string='Meeting Title',
        required=True,
        default=lambda self: _('Meeting Recording - %s') % fields.Datetime.to_string(fields.Datetime.now()),
        tracking=True
    )
    channel_id = fields.Many2one('discuss.channel', string='Discuss Channel', ondelete='set null', tracking=True)
    start_time = fields.Datetime(string='Start Time', default=fields.Datetime.now, tracking=True)
    end_time = fields.Datetime(string='End Time')
    duration = fields.Float(string='Duration (Seconds)', default=0.0)
    duration_display = fields.Char(string='Duration', compute='_compute_duration_display', store=True)

    partner_ids = fields.Many2many('res.partner', string='Attendees')
    user_id = fields.Many2one('res.users', string='Recorded By', default=lambda self: self.env.user)

    # Legacy single-file field: still populated (and still processed the old way) for
    # recordings uploaded before segmented recording existed. New recordings use segment_ids
    # instead, since a whole 1-3 hour meeting as one file/request doesn't fit Odoo's request
    # size limits or comfortably fit in browser memory - see segment model for why.
    audio_attachment_id = fields.Many2one('ir.attachment', string='Audio File', ondelete='cascade')
    audio_player_html = fields.Html(string='Audio Player', compute='_compute_audio_player_html', sanitize=False)

    segment_ids = fields.One2many('discuss.meeting.recording.segment', 'recording_id', string='Segments')
    total_segments = fields.Integer(
        string='Total Segments', default=0,
        help='Set once the meeting has ended and the last segment has been uploaded. 0 means '
             'still recording, or this is a legacy single-file recording.'
    )

    state = fields.Selection([
        ('recording', 'Recording'),
        ('draft', 'Ready to Process'),
        ('processing', 'Processing (Gemini AI)'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ], string='Status', default='draft', tracking=True)

    transcript = fields.Html(string='Full Transcript', sanitize=False)
    summary = fields.Html(string='Executive Summary', sanitize=False)
    key_points = fields.Html(string='Key Decisions & Points', sanitize=False)
    action_items = fields.Html(string='Action Items', sanitize=False)

    error_message = fields.Text(string='Processing Error')
    posted_to_chat = fields.Boolean(string='Posted to Channel Chat', default=False)

    # Real numbers from Gemini's own usageMetadata on every call this recording made (each
    # segment's transcription + the final synthesis pass for segmented recordings, or the one
    # call for legacy single-file recordings) - not an estimate of what was sent.
    prompt_tokens = fields.Integer(string='Input Tokens', default=0,
                                    help='Total tokens Gemini charged for media + prompts across all calls made for this recording.')
    output_tokens = fields.Integer(string='Output Tokens', default=0,
                                    help='Total tokens Gemini charged for generated text across all calls made for this recording.')
    total_tokens_used = fields.Integer(string='Total Tokens', default=0)
    estimated_cost = fields.Float(string='Estimated Cost ($)', default=0.0, digits=(16, 4),
                                   help='Estimated based on the per-1M-token prices configured in Settings. '
                                        'Actual billing depends on your Gemini plan/model and may differ.')

    @api.depends('duration')
    def _compute_duration_display(self):
        for rec in self:
            total_seconds = int(rec.duration or 0)
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            if hours > 0:
                rec.duration_display = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            else:
                rec.duration_display = f"{minutes:02d}:{seconds:02d}"

    @api.depends('audio_attachment_id', 'segment_ids.audio_attachment_id')
    def _compute_audio_player_html(self):
        for rec in self:
            segments = rec.segment_ids.sorted('sequence')
            if segments:
                players = []
                for seg in segments:
                    if not seg.audio_attachment_id:
                        continue
                    players.append(rec._render_player(
                        seg.audio_attachment_id, label=f"Segment {seg.sequence + 1}"
                    ))
                rec.audio_player_html = "".join(players) or "<div class='text-muted'><em>No recording attached.</em></div>"
            elif rec.audio_attachment_id:
                rec.audio_player_html = rec._render_player(rec.audio_attachment_id)
            else:
                rec.audio_player_html = "<div class='text-muted'><em>No recording attached.</em></div>"

    def _render_player(self, attachment, label=None):
        mimetype = attachment.mimetype or 'audio/webm'
        src = f"/web/content/{attachment.id}?download=false"
        label_html = f"<div style='font-weight: 600; margin-bottom: 4px;'>{label}</div>" if label else ""
        if mimetype.startswith('video/'):
            media_html = f"""
                <video controls="controls" style="width: 100%; max-height: 480px; outline: none; background: #000;">
                    <source src="{src}" type="{mimetype}"/>
                    Your browser does not support the video element.
                </video>
            """
        else:
            media_html = f"""
                <audio controls="controls" style="width: 100%; outline: none;">
                    <source src="{src}" type="{mimetype}"/>
                    Your browser does not support the audio element.
                </audio>
            """
        return f"""
        <div style="padding: 12px; background: #f8f9fa; border-radius: 6px; border: 1px solid #dee2e6; margin-bottom: 8px;">
            {label_html}{media_html}
        </div>
        """

    def _estimate_cost(self, prompt_tokens, output_tokens):
        """Rough cost estimate from the admin-configured per-1M-token prices (Settings). These
        default to current Gemini Flash-Lite pricing but won't automatically track whatever
        model/plan is actually in use - treat this as a ballpark, not a bill."""
        ICP = self.env['ir.config_parameter'].sudo()
        try:
            price_in = float(ICP.get_param('odoo_nottaker.gemini_price_per_1m_input', default='0.30'))
            price_out = float(ICP.get_param('odoo_nottaker.gemini_price_per_1m_output', default='2.50'))
        except ValueError:
            price_in, price_out = 0.30, 2.50
        return (prompt_tokens / 1_000_000.0) * price_in + (output_tokens / 1_000_000.0) * price_out

    # ------------------------------------------------------------------
    # Legacy single-file processing (pre-segmented recordings)
    # ------------------------------------------------------------------

    def action_process_recording(self):
        """Button-facing action for legacy (non-segmented) recordings. Gemini calls routinely
        take 30-180+ seconds - running that synchronously in the request would hold the single
        Odoo worker hostage for the whole duration, starving the websocket/bus heartbeat and
        showing the user a "Connection lost" error in the UI. So this only flips the status and
        hands the actual work to a background thread with its own cursor."""
        for rec in self:
            if rec.segment_ids:
                raise UserError(_("This is a segmented recording - segments are transcribed automatically. "
                                   "Use 'Finalize Now' if it's stuck instead."))
            if not rec.audio_attachment_id or not rec.audio_attachment_id.datas:
                raise UserError(_("No audio file attached to process."))
            rec.state = 'processing'
            rec.error_message = False
        self.env.cr.commit()

        for rec in self:
            threading.Thread(
                target=self._process_recording_thread,
                args=(self.env.cr.dbname, rec.id, None),
                daemon=True,
            ).start()

    @api.model
    def _process_recording_thread(self, db_name, recording_id, speaker_clips):
        """Runs on its own cursor/thread - see action_process_recording for why."""
        registry = Registry(db_name)
        with registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            recording = env['discuss.meeting.recording'].browse(recording_id)
            try:
                recording._do_process_recording(speaker_clips=speaker_clips)
            except Exception:
                _logger.exception("Background processing failed for recording %s", recording_id)
            cr.commit()

    def _do_process_recording(self, speaker_clips=None):
        """The actual (slow) Gemini call for a legacy single-file recording. Always run this
        from a background thread - see action_process_recording / the old upload_recording
        controller for the entry points.
        :param speaker_clips: optional list of (name, audio_bytes, mimetype) isolated
        per-participant clips, used only to help the AI attribute the transcript to real names
        instead of guessing generic "Speaker 1" labels."""
        for rec in self:
            if not rec.audio_attachment_id or not rec.audio_attachment_id.datas:
                raise UserError(_("No audio file attached to process."))

            rec.state = 'processing'
            rec.error_message = False

            try:
                audio_bytes = base64.b64decode(rec.audio_attachment_id.datas)
                mimetype = rec.audio_attachment_id.mimetype or 'audio/webm'

                result = GeminiService.process_audio(self.env, audio_bytes, mimetype=mimetype, speaker_clips=speaker_clips)

                prompt_tokens = result.get('prompt_tokens', 0)
                output_tokens = result.get('output_tokens', 0)
                rec.write({
                    'transcript': result.get('transcript'),
                    'summary': result.get('summary'),
                    'key_points': result.get('key_points'),
                    'action_items': result.get('action_items'),
                    'state': 'completed',
                    'error_message': False,
                    'prompt_tokens': prompt_tokens,
                    'output_tokens': output_tokens,
                    'total_tokens_used': result.get('total_tokens', 0),
                    'estimated_cost': rec._estimate_cost(prompt_tokens, output_tokens),
                })
                self.env['discuss.meeting.recording.usage.log'].log_call(rec, 'legacy', result)

                # Check auto post setting (stored inverted - see res_config_settings.py for why)
                ICP = self.env['ir.config_parameter'].sudo()
                auto_post = ICP.get_param('odoo_nottaker.gemini_auto_post_disabled') != 'True'
                if auto_post and rec.channel_id and not rec.posted_to_chat:
                    rec.action_post_to_chat()

            except Exception as e:
                _logger.exception("Error processing recording %s: %s", rec.id, str(e))
                rec.write({
                    'state': 'failed',
                    'error_message': str(e),
                })

    # ------------------------------------------------------------------
    # Segmented recording (long meetings)
    # ------------------------------------------------------------------

    def _check_and_finalize(self):
        """Called after each segment finishes processing. Finalizes (merges transcripts + runs
        one synthesis pass for the overall summary/key points/action items) once every segment
        up to total_segments has reached a terminal state. Guarded with a row lock since
        multiple segments' background threads can call this at nearly the same time."""
        for rec in self:
            if not rec.total_segments:
                continue  # last segment not uploaded yet, or this is a legacy recording
            self.env.cr.execute(
                "SELECT state FROM discuss_meeting_recording WHERE id = %s FOR UPDATE", (rec.id,)
            )
            rec.invalidate_recordset(['state'])
            if rec.state not in ('recording', 'processing'):
                continue  # already finalized (or being finalized) by another segment's thread
            segments = rec.segment_ids.filtered(lambda s: s.sequence < rec.total_segments)
            if len(segments) < rec.total_segments:
                continue  # not all segments have arrived yet
            if any(s.state not in ('completed', 'failed') for s in segments):
                continue  # still processing
            rec.state = 'processing'
            self.env.cr.commit()  # release the lock before the (possibly slow) synthesis call
            rec._do_finalize(segments)

    def _do_finalize(self, segments):
        """Merges all segment transcripts and runs one lightweight text-only Gemini pass to
        produce a single coherent Summary/Key Points/Action Items for the whole meeting -
        each segment's own transcript only covers its own ~5-minute window."""
        self.ensure_one()
        segments = segments.sorted('sequence')
        ok_segments = segments.filtered(lambda s: s.state == 'completed')
        try:
            transcript_parts = []
            plain_parts = []
            for seg in ok_segments:
                minutes = int(seg.start_offset // 60)
                transcript_parts.append(
                    f"<h5>Segment {seg.sequence + 1} (starting at {minutes} min)</h5>{seg.transcript or ''}"
                )
                plain_text = re.sub('<[^<]+?>', ' ', seg.transcript or '')
                plain_parts.append(f"[Segment {seg.sequence + 1}]\n{plain_text}")

            merged_transcript = "".join(transcript_parts) or "<p>No speech detected.</p>"

            if plain_parts:
                summary_result = GeminiService.synthesize_meeting_summary(self.env, plain_parts)
                self.env['discuss.meeting.recording.usage.log'].log_call(self, 'synthesis', summary_result)
            else:
                summary_result = {
                    'summary': '<p>No transcript available.</p>',
                    'key_points': '<p>None recorded.</p>',
                    'action_items': '<p>None recorded.</p>',
                }

            failed_segments = segments - ok_segments
            failed_note = ""
            if failed_segments:
                failed_note = (
                    f"<p class='text-danger'><em>{len(failed_segments)} of {len(segments)} segment(s) "
                    "could not be transcribed and are missing from this transcript/summary.</em></p>"
                )

            # Roll up real token usage from every call this recording made: each segment's
            # transcription plus the synthesis pass above.
            prompt_tokens = sum(segments.mapped('prompt_tokens')) + summary_result.get('prompt_tokens', 0)
            output_tokens = sum(segments.mapped('output_tokens')) + summary_result.get('output_tokens', 0)
            total_tokens = sum(segments.mapped('total_tokens')) + summary_result.get('total_tokens', 0)

            self.write({
                'transcript': failed_note + merged_transcript,
                'summary': summary_result.get('summary'),
                'key_points': summary_result.get('key_points'),
                'action_items': summary_result.get('action_items'),
                'state': 'completed' if ok_segments else 'failed',
                'error_message': False if ok_segments else 'All segments failed to transcribe.',
                'prompt_tokens': prompt_tokens,
                'output_tokens': output_tokens,
                'total_tokens_used': total_tokens,
                'estimated_cost': self._estimate_cost(prompt_tokens, output_tokens),
            })

            ICP = self.env['ir.config_parameter'].sudo()
            auto_post = ICP.get_param('odoo_nottaker.gemini_auto_post_disabled') != 'True'
            if auto_post and self.channel_id and not self.posted_to_chat and ok_segments:

                self.action_post_to_chat()
        except Exception as e:
            _logger.exception("Error finalizing recording %s: %s", self.id, str(e))
            self.write({'state': 'failed', 'error_message': str(e)})

    def action_finalize_now(self):
        """Manual fallback: force-finalize using whatever segments currently exist, in case the
        client never sent the final is_last segment (browser crash, connection dropped, etc.)."""
        for rec in self:
            segments = rec.segment_ids
            if not segments:
                raise UserError(_("No segments to finalize."))
            if any(s.state == 'processing' for s in segments):
                raise UserError(_("Some segments are still being transcribed - wait for them to finish first."))
            rec.total_segments = max(rec.total_segments, len(segments))
            rec.state = 'processing'
        self.env.cr.commit()

        for rec in self:
            threading.Thread(
                target=self._finalize_thread,
                args=(self.env.cr.dbname, rec.id),
                daemon=True,
            ).start()

    @api.model
    def _finalize_thread(self, db_name, recording_id):
        registry = Registry(db_name)
        with registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            rec = env['discuss.meeting.recording'].browse(recording_id)
            try:
                rec._do_finalize(rec.segment_ids)
            except Exception:
                _logger.exception("Manual finalize failed for recording %s", recording_id)
            cr.commit()

    def action_post_to_chat(self):
        for rec in self:
            if not rec.channel_id:
                raise UserError(_("No Discuss Channel linked to this recording."))

            body = f"""
            <div style="font-family: inherit; border-left: 4px solid #714B67; padding-left: 12px; margin: 8px 0;">
                <h4 style="margin: 0 0 6px 0; color: #714B67;">🎙️ <strong>AI Meeting Summary & Notes</strong></h4>
                <p style="margin: 0 0 8px 0; font-size: 12px; color: #666;">
                    <strong>Meeting:</strong> {rec.name} | <strong>Duration:</strong> {rec.duration_display or 'N/A'}
                </p>
                <div style="margin-bottom: 10px;">
                    <strong>Executive Summary:</strong>
                    {rec.summary or '<p>None</p>'}
                </div>
                <div style="margin-bottom: 10px;">
                    <strong>Key Points & Decisions:</strong>
                    {rec.key_points or '<p>None</p>'}
                </div>
                <div style="margin-bottom: 10px;">
                    <strong>Action Items:</strong>
                    {rec.action_items or '<p>None</p>'}
                </div>
            </div>
            """
            rec.channel_id.message_post(
                body=body,
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
                body_is_html=True,
            )
            rec.posted_to_chat = True
