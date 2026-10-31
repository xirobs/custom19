# -*- coding: utf-8 -*-
import base64
import json
import logging
import time
import requests
from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class GeminiService:
    # Gemini's inline base64 payload is capped around 20MB; stay safely under that.
    # Larger media (typically video recordings) goes through the File API instead.
    INLINE_SIZE_LIMIT = 15 * 1024 * 1024

    @staticmethod
    def get_config(env):
        ICP = env['ir.config_parameter'].sudo()
        api_key = ICP.get_param('odoo_nottaker.gemini_api_key', default='').strip()
        model = ICP.get_param('odoo_nottaker.gemini_model', default='gemini-flash-latest').strip()
        custom_prompt = ICP.get_param('odoo_nottaker.gemini_custom_prompt', default='').strip()
        return api_key, model, custom_prompt

    @classmethod
    def find_best_available_model(cls, api_key):
        """Fetch available models from Gemini API that support generateContent."""
        urls_to_try = [
            f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
            f"https://generativelanguage.googleapis.com/v1/models?key={api_key}",
        ]
        for url in urls_to_try:
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    models = data.get("models", [])
                    valid_models = []
                    for m in models:
                        name = m.get("name", "").replace("models/", "")
                        methods = m.get("supportedGenerationMethods", [])
                        if "generateContent" in methods:
                            valid_models.append(name)
                    
                    # Preference order (aliases first: they auto-follow whatever Google currently serves)
                    for pref in ["gemini-flash-latest", "gemini-pro-latest", "gemini-flash-lite-latest", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]:
                        for vm in valid_models:
                            if pref in vm:
                                return vm
                    if valid_models:
                        return valid_models[0]
            except Exception as e:
                _logger.warning("Failed to list models from %s: %s", url, str(e))
        return None

    @classmethod
    def _upload_file_to_gemini(cls, api_key, media_bytes, mimetype, display_name='meeting_recording'):
        """Upload media too large for inline base64 via Gemini's File API (multipart, single-shot).
        Returns (file_name, file_uri) once the file has finished server-side processing."""
        boundary = 'odoo_nottaker_boundary_0d6efd'
        metadata = json.dumps({'file': {'display_name': display_name}}).encode('utf-8')
        body = (
            f'--{boundary}\r\n'
            f'Content-Type: application/json; charset=UTF-8\r\n\r\n'
        ).encode('utf-8') + metadata + (
            f'\r\n--{boundary}\r\n'
            f'Content-Type: {mimetype}\r\n\r\n'
        ).encode('utf-8') + media_bytes + f'\r\n--{boundary}--'.encode('utf-8')

        url = f'https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}'
        headers = {
            'X-Goog-Upload-Protocol': 'multipart',
            'Content-Type': f'multipart/related; boundary={boundary}',
        }
        resp = requests.post(url, headers=headers, data=body, timeout=120)
        if resp.status_code != 200:
            raise UserError(_("Failed to upload media to Google Gemini File API (Code %s): %s") % (
                resp.status_code, resp.text[:500]))

        file_info = resp.json().get('file', {})
        file_name = file_info.get('name')
        file_uri = file_info.get('uri')
        state = file_info.get('state', 'PROCESSING')

        # Video files need server-side processing before they can be referenced; poll until ACTIVE.
        max_wait = 120
        waited = 0
        while state == 'PROCESSING' and waited < max_wait:
            time.sleep(3)
            waited += 3
            poll_url = f'https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}'
            poll_resp = requests.get(poll_url, timeout=30)
            if poll_resp.status_code == 200:
                file_info = poll_resp.json()
                state = file_info.get('state', state)
                file_uri = file_info.get('uri', file_uri)

        if state != 'ACTIVE':
            raise UserError(_("Google Gemini could not process the uploaded recording in time (state: %s).") % state)

        return file_name, file_uri

    @classmethod
    def _delete_gemini_file(cls, api_key, file_name):
        if not file_name:
            return
        try:
            url = f'https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}'
            requests.delete(url, timeout=15)
        except Exception as e:
            _logger.warning("Failed to delete temporary Gemini file %s: %s", file_name, str(e))

    @classmethod
    def process_audio(cls, env, audio_bytes, mimetype='audio/webm', speaker_clips=None):
        """:param speaker_clips: optional list of (name, audio_bytes, mimetype) isolated
        per-participant clips. When given, they're included so the transcript can be labeled
        with real names instead of generic "Speaker 1" guesses."""
        api_key, model, custom_prompt = cls.get_config(env)
        if not api_key:
            raise UserError(_("Google Gemini API Key is missing. Please configure it in Settings > General Settings > Discuss."))

        if not audio_bytes:
            raise UserError(_("No audio data provided for transcription."))

        # Clean mimetype
        if not mimetype:
            mimetype = 'audio/webm'
        elif ';' in mimetype:
            mimetype = mimetype.split(';')[0].strip()

        # Video recordings can easily exceed Gemini's inline base64 cap (~20MB);
        # route large media through the File API instead of embedding it in the request.
        uploaded_file_name = None
        if len(audio_bytes) > cls.INLINE_SIZE_LIMIT:
            _logger.info("Media size %s bytes exceeds inline limit, uploading via Gemini File API...", len(audio_bytes))
            uploaded_file_name, file_uri = cls._upload_file_to_gemini(api_key, audio_bytes, mimetype)
            media_part = {"file_data": {"mime_type": mimetype, "file_uri": file_uri}}
        else:
            audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
            media_part = {"inline_data": {"mime_type": mimetype, "data": audio_b64}}

        # Isolated per-participant clips: small, always sent inline regardless of the main
        # media's size, since a lone voice track is tiny compared to the main recording.
        speaker_names = []
        speaker_parts = []
        for name, clip_bytes, clip_mimetype in (speaker_clips or []):
            if not clip_bytes:
                continue
            clean_mimetype = (clip_mimetype or 'audio/webm').split(';')[0].strip()
            speaker_names.append(name)
            speaker_parts.append({"text": f'The following audio clip is only "{name}" speaking, in isolation:'})
            speaker_parts.append({
                "inline_data": {
                    "mime_type": clean_mimetype,
                    "data": base64.b64encode(clip_bytes).decode('utf-8'),
                }
            })

        system_instruction = (
            "You are an expert AI meeting assistant and transcription specialist. "
            "Your task is to transcribe this audio recording accurately and produce comprehensive, structured meeting notes. "
            "Format your output as a valid JSON object with the following keys:\n"
            "1. 'transcript': The complete, word-for-word transcript. Format speaker dialogue clearly.\n"
            "2. 'summary': A concise executive summary of the meeting highlights.\n"
            "3. 'key_points': A list of key topics, discussions, and decisions made.\n"
            "4. 'action_items': A list of actionable tasks, who they are assigned to, and next steps.\n"
        )
        if speaker_names:
            system_instruction += (
                f"\nThe meeting participants are: {', '.join(speaker_names)}. After the main recording, "
                "you are given an isolated audio clip of each participant speaking alone so you can learn "
                "their voice. Use those to identify who is speaking in the main recording, and label the "
                f"transcript with each participant's real name (e.g. \"{speaker_names[0]}: ...\") instead of "
                "generic labels like 'Speaker 1'. If a voice in the main recording doesn't clearly match any "
                "isolated clip, label it 'Unknown Speaker' rather than guessing.\n"
            )
        if custom_prompt:
            system_instruction += f"\nAdditional Instructions: {custom_prompt}\n"

        prompt_text = (
            "Please transcribe and summarize this meeting recording completely and return the response in JSON format."
        )

        payload = {
            "contents": [
                {
                    "parts": [
                        media_part,
                        *speaker_parts,
                        {
                            "text": prompt_text
                        }
                    ]
                }
            ],
            "system_instruction": {
                "parts": [
                    {
                        "text": system_instruction
                    }
                ]
            },
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.2,
            }
        }

        try:
            result_json, usage = cls._generate_content_json(api_key, model, payload, log_label=f"media ({len(audio_bytes)} bytes, {mimetype})")
        finally:
            if uploaded_file_name:
                cls._delete_gemini_file(api_key, uploaded_file_name)

        formatted = cls._format_results(result_json)
        formatted.update(usage)
        return formatted

    @classmethod
    def _generate_content_json(cls, api_key, model, payload, log_label="request"):
        """Calls Gemini's generateContent, trying `model` then a list of known-good fallbacks,
        retrying on 404 (retired model)/503 (overloaded)/429 (rate limited) rather than giving
        up on the whole request. Returns (result_json, usage) where result_json is the parsed
        JSON object from the model's response text, and usage is
        {'prompt_tokens': int, 'output_tokens': int, 'total_tokens': int} taken from Gemini's
        own usageMetadata - real token counts, not an estimate. Shared by process_audio
        (transcription) and synthesize_meeting_summary (text-only)."""
        # Aliases (*-latest) are listed first since they auto-follow whatever Google currently
        # serves, rather than pinning to a specific generation that Google can retire later.
        models_to_try = [model]
        for fallback in ["gemini-flash-latest", "gemini-pro-latest", "gemini-flash-lite-latest", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash-lite"]:
            if fallback not in models_to_try:
                models_to_try.append(fallback)

        response = None
        successful_model = None
        last_err_msg = ""
        success = False

        for cur_model in models_to_try:
            for api_ver in ["v1beta", "v1"]:
                url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{cur_model}:generateContent?key={api_key}"
                headers = {"Content-Type": "application/json"}
                try:
                    _logger.info("Sending %s to Gemini API (%s/%s)...", log_label, api_ver, cur_model)
                    resp = requests.post(url, json=payload, headers=headers, timeout=180)
                    if resp.status_code == 200:
                        response = resp
                        successful_model = cur_model
                        success = True
                        break
                    elif resp.status_code in (404, 503, 429):
                        # 404: model not found/retired. 503: model temporarily overloaded on
                        # Google's side. 429: rate limited. All three are worth trying the
                        # next model/version for instead of giving up on the whole request.
                        _logger.info("Model %s unavailable on %s (HTTP %s), trying next fallback...", cur_model, api_ver, resp.status_code)
                        last_err_msg = resp.text
                        continue
                    else:
                        err_msg = resp.text
                        try:
                            err_json = resp.json()
                            if 'error' in err_json and 'message' in err_json['error']:
                                err_msg = err_json['error']['message']
                        except Exception:
                            pass
                        raise UserError(_("Google Gemini API error (Code %s): %s") % (resp.status_code, err_msg))
                except UserError:
                    raise
                except Exception as e:
                    _logger.warning("Error calling %s: %s", url, str(e))
                    last_err_msg = str(e)
            if success:
                break

        if not success or not response:
            # Try dynamically discovering model
            best_model = cls.find_best_available_model(api_key)
            if best_model and best_model not in models_to_try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{best_model}:generateContent?key={api_key}"
                try:
                    resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=180)
                    if resp.status_code == 200:
                        response = resp
                        successful_model = best_model
                        success = True
                except Exception:
                    pass

        if not success or not response:
            raise UserError(_("Could not reach Google Gemini API with any available model. Details: %s") % last_err_msg)

        res_data = response.json()
        usage_metadata = res_data.get("usageMetadata", {})
        usage = {
            'model': successful_model,
            'prompt_tokens': usage_metadata.get('promptTokenCount', 0),
            'output_tokens': usage_metadata.get('candidatesTokenCount', 0),
            'total_tokens': usage_metadata.get('totalTokenCount', 0),
        }
        try:
            candidates = res_data.get("candidates", [])
            if not candidates:
                raise ValueError("No response generated by Gemini.")
            text_content = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
            result_json = json.loads(text_content)
        except Exception as e:
            _logger.warning("Could not parse JSON response directly from Gemini: %s. Raw text: %s", str(e), response.text[:500])
            result_json = {
                "transcript": text_content if 'text_content' in locals() else response.text,
                "summary": "Meeting recorded and processed.",
                "key_points": [],
                "action_items": []
            }
        return result_json, usage

    @classmethod
    def synthesize_meeting_summary(cls, env, segment_transcripts):
        """Text-only pass over already-transcribed segment texts, producing one coherent
        Summary/Key Points/Action Items for the whole meeting - used to stitch together a long,
        segmented recording where each segment was only transcribed for its own ~5-minute
        window. Much cheaper/faster than another audio call since it's just processing text."""
        api_key, model, custom_prompt = cls.get_config(env)
        if not api_key:
            raise UserError(_("Google Gemini API Key is missing. Please configure it in Settings > General Settings > Discuss."))

        combined_text = "\n\n".join(segment_transcripts)

        system_instruction = (
            "You are an expert AI meeting assistant. You are given the full transcript of a long "
            "meeting, split into sequential segments. Read all segments as one continuous meeting "
            "and produce a valid JSON object with the following keys:\n"
            "1. 'summary': A concise executive summary of the whole meeting.\n"
            "2. 'key_points': A list of key topics, discussions, and decisions made across the meeting.\n"
            "3. 'action_items': A list of actionable tasks, who they are assigned to, and next steps.\n"
            "Do not repeat a 'transcript' key - only summary, key_points, and action_items."
        )
        if custom_prompt:
            system_instruction += f"\nAdditional Instructions: {custom_prompt}\n"

        payload = {
            "contents": [{"parts": [{"text": combined_text}]}],
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.2,
            }
        }

        result_json, usage = cls._generate_content_json(api_key, model, payload, log_label=f"segment synthesis ({len(combined_text)} chars)")
        formatted = cls._format_results(result_json)
        return {
            'summary': formatted['summary'],
            'key_points': formatted['key_points'],
            'action_items': formatted['action_items'],
            **usage,
        }

    @classmethod
    def _format_results(cls, result_json):
        transcript_raw = result_json.get('transcript', '')
        if isinstance(transcript_raw, list):
            transcript_html = "<br/>".join(f"<p>{line}</p>" for line in transcript_raw)
        else:
            transcript_html = f"<div class='o_meeting_transcript'>{str(transcript_raw).replace(chr(10), '<br/>')}</div>"

        summary_raw = result_json.get('summary', '')
        if isinstance(summary_raw, list):
            summary_html = "".join(f"<p>{item}</p>" for item in summary_raw)
        else:
            summary_html = f"<div class='o_meeting_summary'>{str(summary_raw).replace(chr(10), '<br/>')}</div>"

        key_points_raw = result_json.get('key_points', [])
        if isinstance(key_points_raw, list):
            kp_items = "".join(f"<li>{item}</li>" for item in key_points_raw if item)
            key_points_html = f"<ul class='o_meeting_key_points'>{kp_items}</ul>" if kp_items else "<p>None recorded.</p>"
        else:
            key_points_html = f"<div>{str(key_points_raw).replace(chr(10), '<br/>')}</div>"

        action_items_raw = result_json.get('action_items', [])
        if isinstance(action_items_raw, list):
            ai_items = "".join(f"<li><input type='checkbox' disabled='disabled'/> <strong>{item}</strong></li>" for item in action_items_raw if item)
            action_items_html = f"<ul class='o_meeting_action_items' style='list-style: none; padding-left: 0;'>{ai_items}</ul>" if ai_items else "<p>No specific action items identified.</p>"
        else:
            action_items_html = f"<div>{str(action_items_raw).replace(chr(10), '<br/>')}</div>"

        return {
            'transcript': transcript_html,
            'summary': summary_html,
            'key_points': key_points_html,
            'action_items': action_items_html,
            'raw_json': json.dumps(result_json, indent=2)
        }
