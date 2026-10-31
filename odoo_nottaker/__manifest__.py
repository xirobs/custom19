# -*- coding: utf-8 -*-
{
    'name': 'Discuss Call Recorder & AI Note Taker',
    'version': '19.0.1.0.3',
    'category': 'Productivity/Discuss',
    'summary': 'Record Discuss calls, transcribe speech to text, and generate AI meeting minutes with Google Gemini',
    'description': """
Discuss Call Recorder & AI Note Taker
=====================================
- Record Odoo Discuss voice & video calls in high quality.
- Client-side Web Audio mixing of local microphone and remote attendee streams.
- Speech-to-Text (STT) transcription powered by Google Gemini (Free API).
- AI Meeting Minutes: Executive Summary, Key Decisions, and Action Items.
- Centralized Recordings Dashboard with HTML5 audio playback and formatted transcript viewer.
- Auto-post meeting summaries directly into the Discuss channel.
    """,
    'author': 'Elsner Technologies Pvt Ltd',
    'images': [
        'static/description/banner.png',
    ],
    'depends': ['base', 'mail', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'views/discuss_meeting_recording_send_wizard_views.xml',
        'views/discuss_meeting_recording_views.xml',
        'views/discuss_meeting_recording_usage_log_views.xml',
        'views/res_config_settings_views.xml',
        'views/discuss_channel_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'odoo_nottaker/static/src/**/*',
        ],
    },
    'application': True,
    'installable': True,
    'auto_install': False,
    'license': 'OPL-1',
    'price': 19.99,
    'currency': 'USD',
}
