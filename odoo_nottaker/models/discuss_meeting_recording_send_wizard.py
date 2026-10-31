# -*- coding: utf-8 -*-
from markupsafe import escape
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class DiscussMeetingRecordingSendWizard(models.TransientModel):
    _name = 'discuss.meeting.recording.send.wizard'
    _description = 'Send Meeting Recording & Notes'

    recording_id = fields.Many2one('discuss.meeting.recording', string='Meeting Recording', required=True)
    partner_ids = fields.Many2many('res.partner', string='Send to Attendees',
                                    help='Only attendees with an email on file are actually emailed.')
    extra_emails = fields.Char(
        string='Additional Emails',
        help='Comma-separated email addresses - use this for guests who joined without an Odoo '
             "account, since they usually don't have an email on file to pick from above."
    )
    note = fields.Text(string='Note', help='Optional message to include above the meeting notes.')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        recording_id = self.env.context.get('default_recording_id')
        if recording_id and 'partner_ids' in fields_list:
            recording = self.env['discuss.meeting.recording'].browse(recording_id)
            res['partner_ids'] = [(6, 0, recording.partner_ids.filtered('email').ids)]
        return res

    def _get_recipient_emails(self):
        emails = list(self.partner_ids.filtered('email').mapped('email'))
        for raw in (self.extra_emails or '').split(','):
            email = raw.strip()
            if email:
                emails.append(email)
        # de-duplicate while preserving order
        seen = set()
        unique_emails = []
        for email in emails:
            if email.lower() not in seen:
                seen.add(email.lower())
                unique_emails.append(email)
        return unique_emails

    def action_send(self):
        self.ensure_one()
        recording = self.recording_id
        if recording.state != 'completed':
            raise UserError(_("This recording isn't marked Completed yet - wait for transcription to finish first."))

        recipient_emails = self._get_recipient_emails()
        if not recipient_emails:
            raise UserError(_("Pick at least one attendee with an email on file, or type an email address manually."))

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')

        # Public, token-gated links so guests (no Odoo login) can still open the recording -
        # the same mechanism Odoo's own "Share" links use elsewhere.
        segments = recording.segment_ids.sorted('sequence')
        attachments = segments.mapped('audio_attachment_id') if segments else recording.audio_attachment_id
        attachments = attachments.filtered(lambda a: a)
        if not attachments:
            raise UserError(_("This recording has no media file attached to share."))
        attachments.generate_access_token()

        # Links point to our own /odoo_nottaker/view landing page (inline player + a separate
        # Download button), not the raw /web/content URL directly - opening a raw media URL as
        # if it were a page uses the browser's minimal built-in media viewer, which has proven
        # unreliable for multi-MB files over a tunnel; a real page with an embedded <video> tag
        # uses the standard, well-tested playback pipeline instead.
        if segments:
            links_html = "".join(
                f'<li><a href="{base_url}/odoo_nottaker/view/{seg.audio_attachment_id.id}'
                f'?access_token={seg.audio_attachment_id.access_token}">'
                f'Segment {seg.sequence + 1} recording</a></li>'
                for seg in segments if seg.audio_attachment_id
            )
        else:
            att = recording.audio_attachment_id
            links_html = (
                f'<li><a href="{base_url}/odoo_nottaker/view/{att.id}'
                f'?access_token={att.access_token}">Meeting recording</a></li>'
            )

        note_html = f"<p>{escape(self.note).replace(chr(10), '<br/>')}</p>" if self.note else ""

        body_html = f"""
        <div style="font-family: inherit;">
            <h3 style="color:#714B67;">Meeting Recording &amp; AI Notes</h3>
            <p><strong>Meeting:</strong> {recording.name}<br/>
               <strong>Duration:</strong> {recording.duration_display or 'N/A'}</p>
            {note_html}
            <h4>Executive Summary</h4>
            {recording.summary or '<p>None</p>'}
            <h4>Key Points &amp; Decisions</h4>
            {recording.key_points or '<p>None</p>'}
            <h4>Action Items</h4>
            {recording.action_items or '<p>None</p>'}
            <h4>Recording</h4>
            <ul>{links_html}</ul>
        </div>
        """

        mail = self.env['mail.mail'].sudo().create({
            'subject': f"Meeting Recording & Notes: {recording.name}",
            'body_html': body_html,
            'email_to': ','.join(recipient_emails),
            'auto_delete': True,
        })
        try:
            # raise_exception=True: mail.mail.send() otherwise swallows delivery failures
            # (e.g. no outgoing mail server configured) completely silently - it just marks the
            # record 'exception' and returns normally, with no error shown to the user at all.
            mail.send(raise_exception=True)
        except Exception as e:
            raise UserError(_(
                "Failed to send the email: %s\n\n"
                "Check Settings > Technical > Email > Outgoing Mail Servers - an outgoing mail "
                "server must be configured before this app can send anything."
            ) % str(e))

        return {'type': 'ir.actions.act_window_close'}
