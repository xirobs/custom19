# -*- coding: utf-8 -*-
from odoo import models, fields

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    gemini_api_key = fields.Char(
        string='Google Gemini API Key',
        config_parameter='odoo_nottaker.gemini_api_key',
        help='Free API key from Google AI Studio (https://aistudio.google.com/)'
    )
    # Free-text on purpose: Google renames/retires model ids over time (already happened twice
    # during development of this module). A Selection field crashes the whole Settings page the
    # moment the stored value falls outside its hardcoded option list. The app already retries
    # several current model ids automatically if this one is unavailable (see GeminiService).
    gemini_model = fields.Char(
        string='Gemini Model',
        config_parameter='odoo_nottaker.gemini_model',
        default='gemini-flash-latest',
        help='Gemini model id to use, e.g. gemini-flash-latest (recommended, auto-updates) or '
             'gemini-pro-latest (deep reasoning, auto-updates). If this model becomes unavailable, '
             'the app automatically tries other current models.'
    )

    # Not a plain config_parameter field on purpose: ir.config_parameter.set_param() treats a
    # False value as "delete this parameter" rather than storing it. Combined with default=True,
    # unchecking this and saving would delete the row, and the next page load would fall right
    # back to the True default - the checkbox could never actually be turned off. Storing the
    # inverted ("disabled") flag instead means turning it off stores a truthy value, which
    # persists normally.
    auto_record_calls = fields.Boolean(
        string='Auto-Record Meetings',
        help='Automatically start recording when a meeting starts and stop/transcribe when the call ends.',
        compute='_compute_auto_record_calls',
        inverse='_inverse_auto_record_calls',
    )
    gemini_auto_post_to_chat = fields.Boolean(
        string='Auto-post Summary to Discuss Channel',
        help='Automatically post the generated meeting summary into the Discuss channel when processing is done.',
        compute='_compute_gemini_auto_post_to_chat',
        inverse='_inverse_gemini_auto_post_to_chat',
    )

    def _compute_auto_record_calls(self):
        ICP = self.env['ir.config_parameter'].sudo()
        disabled = ICP.get_param('odoo_nottaker.auto_record_disabled') == 'True'
        for rec in self:
            rec.auto_record_calls = not disabled

    def _inverse_auto_record_calls(self):
        ICP = self.env['ir.config_parameter'].sudo()
        for rec in self:
            ICP.set_param('odoo_nottaker.auto_record_disabled', False if rec.auto_record_calls else 'True')

    def _compute_gemini_auto_post_to_chat(self):
        ICP = self.env['ir.config_parameter'].sudo()
        disabled = ICP.get_param('odoo_nottaker.gemini_auto_post_disabled') == 'True'
        for rec in self:
            rec.gemini_auto_post_to_chat = not disabled

    def _inverse_gemini_auto_post_to_chat(self):
        ICP = self.env['ir.config_parameter'].sudo()
        for rec in self:
            ICP.set_param('odoo_nottaker.gemini_auto_post_disabled', False if rec.gemini_auto_post_to_chat else 'True')
    gemini_custom_prompt = fields.Char(
        string='Custom AI Instructions / Language',
        config_parameter='odoo_nottaker.gemini_custom_prompt',
        help='Optional instructions for transcription (e.g. specific language, custom terminology, formatting)'
    )

    # Defaults match current Gemini Flash-Lite pricing. These don't auto-update if Google's
    # pricing changes or you switch models - they only drive the "Estimated Cost" shown on each
    # recording, not actual billing.
    gemini_price_per_1m_input = fields.Float(
        string='Price per 1M Input Tokens ($)',
        config_parameter='odoo_nottaker.gemini_price_per_1m_input',
        default=0.30,
        digits=(16, 4),
        help='Used only to estimate the "Estimated Cost" shown on each recording. Check '
             'aistudio.google.com for your model\'s actual current price.'
    )
    gemini_price_per_1m_output = fields.Float(
        string='Price per 1M Output Tokens ($)',
        config_parameter='odoo_nottaker.gemini_price_per_1m_output',
        default=2.50,
        digits=(16, 4),
        help='Used only to estimate the "Estimated Cost" shown on each recording. Check '
             'aistudio.google.com for your model\'s actual current price.'
    )
