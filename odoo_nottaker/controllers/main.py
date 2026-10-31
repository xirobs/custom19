# -*- coding: utf-8 -*-
import base64
import json
import logging
from markupsafe import escape
from datetime import timedelta

from odoo import http, fields, _
from odoo.http import request
from odoo.tools import consteq

_logger = logging.getLogger(__name__)

# A single segment (~5 minutes at current bitrate settings) tops out well under this, but the
# margin matters: Odoo's own default request cap is 128MB, which a single segment at higher
# quality settings or a slightly longer-than-usual segment could otherwise bump into.
SEGMENT_MAX_CONTENT_LENGTH = 300 * 1024 * 1024


class DiscussNoteTakerController(http.Controller):

    @http.route('/odoo_nottaker/view/<int:attachment_id>', type='http', auth='public')
    def view_recording(self, attachment_id, access_token=None, **kwargs):
        """Public landing page for a shared recording link (no Odoo login needed - gated by the
        attachment's own access_token, same mechanism Odoo's own Share links use). Renders a
        real HTML page with an embedded <video>/<audio> tag - a raw media URL opened directly as
        a "page" goes through the browser's minimal built-in media viewer, which has proven
        unreliable for multi-MB files over a tunnel; an actual page with a proper <video> element
        uses the standard, well-tested HTMLMediaElement + Range-request pipeline instead. Also
        gives a separate explicit Download button, since streamed playback and a full download
        are two different things people want from the same link."""
        attachment = request.env['ir.attachment'].sudo().browse(attachment_id)
        if not attachment.exists() or not access_token or not consteq(attachment.access_token or '', access_token):
            return request.not_found()

        mimetype = attachment.mimetype or 'application/octet-stream'
        stream_url = f'/web/content/{attachment.id}?access_token={access_token}&download=false'
        download_url = f'/web/content/{attachment.id}?access_token={access_token}&download=true'
        name = escape(attachment.name or 'Recording')

        if mimetype.startswith('video/'):
            media_tag = f'<video controls preload="metadata" style="width:100%;max-height:75vh;background:#000;border-radius:8px;" src="{stream_url}"></video>'
        else:
            media_tag = f'<audio controls style="width:100%;" src="{stream_url}"></audio>'

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>{name}</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; background: #1c1c1c; color: #f1f1f1;
                margin: 0; padding: 24px 16px; }}
        .wrap {{ max-width: 900px; margin: 0 auto; }}
        h2 {{ font-weight: 500; font-size: 18px; word-break: break-word; }}
        a.download-btn {{ display: inline-block; margin-top: 16px; padding: 10px 22px;
                background: #714B67; color: #fff; text-decoration: none; border-radius: 6px;
                font-size: 14px; }}
        a.download-btn:hover {{ background: #5c3c54; }}
    </style>
</head>
<body>
    <div class="wrap">
        <h2>{name}</h2>
        {media_tag}
        <div><a class="download-btn" href="{download_url}">Download Recording</a></div>
    </div>
</body>
</html>"""
        return request.make_response(html, headers=[('Content-Type', 'text/html; charset=utf-8')])

    @http.route('/odoo_nottaker/config', type='jsonrpc', auth='user')
    def get_config(self):
        """Exposes only the non-sensitive flags the Discuss client needs (never the API key)."""
        ICP = request.env['ir.config_parameter'].sudo()
        return {
            'auto_record_enabled': ICP.get_param('odoo_nottaker.auto_record_disabled') != 'True',
        }

    @http.route('/odoo_nottaker/start_recording', type='jsonrpc', auth='user')
    def start_recording(self, channel_id=None, partner_ids=None):
        """Creates the parent recording record immediately, before any audio exists, so
        segments uploaded throughout a long meeting all have somewhere to attach to. This is
        what makes meetings of any length possible - previously the whole recording sat in
        browser memory as one giant blob until the call ended, which doesn't scale past a few
        minutes of screen video."""
        channel = False
        channel_name = "Meeting"
        if channel_id:
            channel = request.env['discuss.channel'].browse(int(channel_id))
            if channel.exists():
                channel_name = channel.name

        Recording = request.env['discuss.meeting.recording']
        if channel and channel.exists():
            # Only one recorder per call: every participant with auto-record on would otherwise
            # upload (and pay Gemini for) their own copy of the same meeting.
            active = Recording.sudo().search([
                ('channel_id', '=', channel.id), ('state', '=', 'recording'),
                ('write_date', '>', fields.Datetime.now() - timedelta(minutes=8)),
            ], limit=1)
            if active and active.user_id != request.env.user:
                return {'already_recording': True, 'recorded_by': active.user_id.name or ''}
        recording = Recording.create({
            'name': f"Meeting Recording - {channel_name} ({fields.Datetime.now().strftime('%Y-%m-%d %H:%M')})",
            'channel_id': channel.id if channel and channel.exists() else False,
            'partner_ids': [(6, 0, partner_ids)] if partner_ids else False,
            'state': 'recording',
        })
        return {'recording_id': recording.id}

    @http.route('/odoo_nottaker/heartbeat', type='jsonrpc', auth='user')
    def heartbeat(self, recording_id):
        """Keeps the 'one recorder per call' lock alive during long segments."""
        recording = request.env['discuss.meeting.recording'].browse(int(recording_id)).exists()
        if recording and recording.state == 'recording':
            recording.sudo().write({'end_time': fields.Datetime.now()})
        return True

    @http.route('/odoo_nottaker/finish_recording', type='jsonrpc', auth='user')
    def finish_recording(self, recording_id, total_segments, duration=0.0):
        """Called when the call ends, so the recording gets finalized even when the last
        segment was empty and therefore never uploaded."""
        recording = request.env['discuss.meeting.recording'].browse(int(recording_id)).exists()
        if not recording:
            return False
        if not recording.total_segments:
            uploaded = recording.segment_ids.mapped('sequence')
            vals = {'total_segments': (max(uploaded) + 1) if uploaded else 0,
                    'end_time': fields.Datetime.now()}
            if duration:
                vals['duration'] = float(duration)
            if not uploaded:
                vals.update(state='failed', error_message=_("No audio was captured during the call."))
            recording.write(vals)
            if uploaded:
                recording._check_and_finalize_async()
        return True

    @http.route('/odoo_nottaker/upload_segment', type='http', auth='user', methods=['POST'],
                csrf=False, max_content_length=SEGMENT_MAX_CONTENT_LENGTH)
    def upload_segment(self, **post):
        try:
            audio_file = request.httprequest.files.get('audio')
            recording_id = post.get('recording_id')
            sequence = post.get('sequence')
            start_offset = float(post.get('start_offset', 0.0))
            duration = float(post.get('duration', 0.0))
            is_last = post.get('is_last') == '1'

            if not audio_file or not recording_id or sequence is None:
                return request.make_json_response({
                    'status': 'error',
                    'message': 'Missing audio file, recording_id, or sequence',
                }, status=400)

            recording = request.env['discuss.meeting.recording'].browse(int(recording_id))
            if not recording.exists():
                return request.make_json_response({'status': 'error', 'message': 'Recording not found'}, status=404)

            sequence = int(sequence)
            audio_bytes = audio_file.read()
            audio_b64 = base64.b64encode(audio_bytes)
            mimetype = audio_file.content_type or 'audio/webm'
            filename = f"{recording.name}_segment{sequence}.webm"

            # Isolated per-participant clips for this segment only, paired by position (the
            # i-th name goes with the i-th audio file) - helps Gemini label the segment's
            # transcript with real names instead of guessing "Speaker 1".
            existing = request.env['discuss.meeting.recording.segment'].search(
                [('recording_id', '=', recording.id), ('sequence', '=', sequence)], limit=1)
            if existing:
                # the browser retried an upload that actually reached us: don't duplicate it
                return request.make_json_response({'status': 'success', 'segment_id': existing.id,
                                                   'recording_id': recording.id})

            speaker_names = request.httprequest.form.getlist('speaker_names')
            speaker_files = request.httprequest.files.getlist('speaker_audio')
            Attachment = request.env['ir.attachment']
            speaker_attachments = Attachment
            for name, sfile in zip(speaker_names, speaker_files):
                try:
                    clip = sfile.read()
                    if len(clip) < 2048:  # muted the whole segment: nothing useful (and often invalid)
                        continue
                    speaker_attachments |= Attachment.create({
                        'name': f"{recording.name}_segment{sequence}_speaker.webm",
                        'description': name,
                        'type': 'binary',
                        'datas': base64.b64encode(clip),
                        'mimetype': (sfile.content_type or 'audio/webm').split(';')[0],
                        'res_model': 'discuss.meeting.recording.segment',
                    })
                except Exception:
                    _logger.warning("Could not read isolated speaker clip for %s", name)

            attachment = Attachment.create({
                'name': filename,
                'type': 'binary',
                'datas': audio_b64,
                'mimetype': mimetype.split(';')[0],
                'res_model': 'discuss.meeting.recording.segment',
            })
            transcription_attachment = Attachment
            tfile = request.httprequest.files.get('transcribe_audio')
            if tfile:
                tbytes = tfile.read()
                if tbytes:
                    transcription_attachment = Attachment.create({
                        'name': f"{recording.name}_segment{sequence}_audio.webm",
                        'type': 'binary',
                        'datas': base64.b64encode(tbytes),
                        'mimetype': (tfile.content_type or 'audio/webm').split(';')[0],
                        'res_model': 'discuss.meeting.recording.segment',
                    })

            segment = request.env['discuss.meeting.recording.segment'].create({
                'recording_id': recording.id,
                'sequence': sequence,
                'start_offset': start_offset,
                'is_last': is_last,
                'audio_attachment_id': attachment.id,
                'transcription_attachment_id': transcription_attachment.id or False,
                'speaker_attachment_ids': [(6, 0, speaker_attachments.ids)],
            })
            (attachment | transcription_attachment | speaker_attachments).write({'res_id': segment.id})

            if is_last:
                recording.write({
                    'end_time': fields.Datetime.now(),
                    'total_segments': sequence + 1,
                    'duration': start_offset + duration,
                })

            # Commit now: the background thread needs its own cursor to see this row, and the
            # response below must return immediately regardless of how long Gemini takes.
            request.env.cr.commit()

            segment.action_process_segment()

            return request.make_json_response({
                'status': 'success',
                'segment_id': segment.id,
                'recording_id': recording.id,
            })

        except Exception as e:
            _logger.exception("Failed to upload meeting recording segment: %s", str(e))
            return request.make_json_response({'status': 'error', 'message': str(e)}, status=500)
