# -*- coding: utf-8 -*-
import base64
import json
import logging
import time
import requests
from odoo import _
from markupsafe import escape
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class GeminiRequestError(UserError):
    """UserError carrying the HTTP status Gemini answered with (0 = no usable answer)."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class GeminiService:
    # Gemini's inline base64 payload is capped around 20MB; stay safely under that.
    # Larger media (typically video recordings) goes through the File API instead.
    INLINE_SIZE_LIMIT = 15 * 1024 * 1024

    # Real, currently served ids only (aliases first: they follow whatever Google serves).
    FALLBACK_MODELS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-flash-lite-latest",
                       "gemini-2.5-flash-lite", "gemini-2.0-flash"]
    LANGUAGES = {
        'es': 'Spanish (Mexico)', 'en': 'English', 'pt': 'Portuguese', 'fr': 'French',
    }

    @staticmethod
    def _fallback_key(env):
        """If this module has no key but scout_management already has a Gemini key configured
        (Ajustes > Grupo scout > IA), reuse it instead of failing every segment."""
        Company = env['res.company'].sudo()
        if 'scout_ai_api_key' not in Company._fields:
            return ''
        companies = env.company.sudo() | Company.search([('scout_ai_api_key', '!=', False)])
        for company in companies:
            key = (company.scout_ai_api_key or '').strip()
            if key and (company.scout_ai_provider or 'gemini') == 'gemini':
                return key
        return ''

    @classmethod
    def get_config(cls, env):
        ICP = env['ir.config_parameter'].sudo()
        api_key = (ICP.get_param('odoo_nottaker.gemini_api_key', default='') or '').strip()
        if not api_key:
            api_key = cls._fallback_key(env)
        model = (ICP.get_param('odoo_nottaker.gemini_model', default='') or '').strip() or 'gemini-flash-latest'
        # people paste "models/gemini-..." from AI Studio
        model = model.replace('models/', '')
        custom_prompt = (ICP.get_param('odoo_nottaker.gemini_custom_prompt', default='') or '').strip()
        lang = ICP.get_param('odoo_nottaker.output_language', default='es') or 'es'
        if lang == 'auto':
            custom_prompt = ("Write the summary, key_points and action_items in the main language spoken "
                             "in the meeting. " + custom_prompt).strip()
        elif lang in cls.LANGUAGES:
            custom_prompt = (
                f"Write the summary, key_points and action_items in {cls.LANGUAGES[lang]}. "
                "Keep the transcript in the language actually spoken. " + custom_prompt
            ).strip()
        return api_key, model, custom_prompt

    @staticmethod
    def _missing_key_error():
        return UserError(_("No hay clave de Google Gemini configurada. Ve a Ajustes > Ajustes generales > "
                           "«Discuss AI Note Taker», pega tu API key de https://aistudio.google.com/apikey "
                           "y pulsa «Probar conexión»; luego usa «Reintentar segmentos fallidos»."))

    @classmethod
    def find_best_available_model(cls, api_key):
        """Fetch available models from Gemini API that support generateContent."""
        urls_to_try = [
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
        ]
        for url in urls_to_try:
            try:
                resp = requests.get(url, headers={'x-goog-api-key': api_key}, timeout=10)
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
                    for pref in ["gemini-flash-latest", "gemini-2.5-flash", "gemini-flash-lite-latest", "gemini-2.5-flash-lite"]:
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

        url = 'https://generativelanguage.googleapis.com/upload/v1beta/files'
        headers = {
            'X-Goog-Upload-Protocol': 'multipart',
            'x-goog-api-key': api_key,
            'Content-Type': f'multipart/related; boundary={boundary}',
        }
        resp = requests.post(url, headers=headers, data=body, timeout=300)
        if resp.status_code != 200:
            raise GeminiRequestError(resp.status_code, _("No se pudo subir el audio a Google Gemini (HTTP %s): %s") % (
                resp.status_code, resp.text[:500]))

        file_info = resp.json().get('file', {})
        file_name = file_info.get('name')
        file_uri = file_info.get('uri')
        state = file_info.get('state', 'PROCESSING')

        # Video files need server-side processing before they can be referenced; poll until ACTIVE.
        max_wait = 300
        waited = 0
        while state == 'PROCESSING' and waited < max_wait:
            time.sleep(3)
            waited += 3
            poll_url = f'https://generativelanguage.googleapis.com/v1beta/{file_name}'
            poll_resp = requests.get(poll_url, headers={'x-goog-api-key': api_key}, timeout=30)
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
            url = f'https://generativelanguage.googleapis.com/v1beta/{file_name}'
            requests.delete(url, headers={'x-goog-api-key': api_key}, timeout=15)
        except Exception as e:
            _logger.warning("Failed to delete temporary Gemini file %s: %s", file_name, str(e))

    @classmethod
    def process_audio(cls, env, audio_bytes, mimetype='audio/webm', speaker_clips=None):
        """:param speaker_clips: optional list of (name, audio_bytes, mimetype) isolated
        per-participant clips. When given, they're included so the transcript can be labeled
        with real names instead of generic "Speaker 1" guesses."""
        api_key, model, custom_prompt = cls.get_config(env)
        if not api_key:
            raise cls._missing_key_error()

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
                "maxOutputTokens": 32768,
            }
        }

        def payload_with(parts):
            return dict(payload, contents=[{"parts": parts}])

        try:
            try:
                result_json, usage = cls._generate_content_json(
                    api_key, model, payload, log_label=f"media ({len(audio_bytes)} bytes, {mimetype})")
            except GeminiRequestError as e:
                if e.status != 400:
                    raise
                # 400 = Gemini could not use something in the request. The usual culprit is one
                # of the isolated per-speaker clips (a participant muted the whole segment, a
                # remote stream that never produced a valid WebM header...). Retry with only
                # the main recording, and if that is inline, through the File API.
                _logger.warning("Gemini rejected the request (%s); retrying with the main recording only", e)
                parts = [media_part, {"text": prompt_text}]
                try:
                    result_json, usage = cls._generate_content_json(
                        api_key, model, payload_with(parts), log_label="media (no speaker clips)")
                except GeminiRequestError as e2:
                    if e2.status != 400 or uploaded_file_name:
                        raise
                    uploaded_file_name, file_uri = cls._upload_file_to_gemini(api_key, audio_bytes, mimetype)
                    parts = [{"file_data": {"mime_type": mimetype, "file_uri": file_uri}}, {"text": prompt_text}]
                    result_json, usage = cls._generate_content_json(
                        api_key, model, payload_with(parts), log_label="media (File API)")
        finally:
            if uploaded_file_name:
                cls._delete_gemini_file(api_key, uploaded_file_name)

        formatted = cls._format_results(result_json)
        formatted.update(usage)
        return formatted

    @staticmethod
    def _error_text(resp):
        try:
            err = resp.json().get('error', {})
            return (err.get('message') or resp.text[:400]).strip().rstrip('.'), err
        except Exception:
            return resp.text[:400], {}

    @staticmethod
    def _retry_delay(err):
        for d in (err or {}).get('details', []) or []:
            delay = d.get('retryDelay')
            if delay:
                try:
                    return float(str(delay).rstrip('s'))
                except ValueError:
                    pass
        return 0.0

    @classmethod
    def _generate_content_json(cls, api_key, model, payload, log_label="request"):
        """Calls Gemini's generateContent with `model`, then the fallback models.
        - 404 / "model not found / not supported": try the next model.
        - 429 / 500 / 503: wait (honouring Google's retryDelay, max 25s) and retry, then next model.
        - 400 (bad request) and 401/403 (bad key / API disabled): stop and raise a clear error.
        Returns (result_json, usage)."""
        models_to_try = [model] + [m for m in cls.FALLBACK_MODELS if m != model]
        errors = []
        response = successful_model = None
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}

        for cur_model in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{cur_model}:generateContent"
            for attempt in range(3):
                try:
                    _logger.info("Sending %s to Gemini (%s, attempt %s)", log_label, cur_model, attempt + 1)
                    resp = requests.post(url, json=payload, headers=headers, timeout=300)
                except requests.exceptions.Timeout:
                    errors.append(f"{cur_model}: tiempo de espera agotado")
                    break
                except Exception as e:
                    errors.append(f"{cur_model}: {e}")
                    break
                if resp.status_code == 200:
                    response, successful_model = resp, cur_model
                    break
                msg, err = cls._error_text(resp)
                low = msg.lower()
                if resp.status_code == 404 or (resp.status_code == 400 and 'model' in low and (
                        'not found' in low or 'not supported' in low or 'unsupported' in low)):
                    errors.append(f"{cur_model}: modelo no disponible")
                    break
                if resp.status_code in (429, 500, 503):
                    errors.append(f"{cur_model}: HTTP {resp.status_code} {msg[:160]}")
                    quota_zero = resp.status_code == 429 and 'limit: 0' in low
                    if attempt < 2 and not quota_zero:
                        time.sleep(min(cls._retry_delay(err) or (4 * (attempt + 1)), 25))
                        continue
                    break
                if resp.status_code in (401, 403) or 'api key' in low:
                    raise GeminiRequestError(resp.status_code, _(
                        "Google rechazó la API key (HTTP %(code)s): %(msg)s. Revisa que la clave sea correcta "
                        "y que la 'Generative Language API' esté habilitada en tu proyecto.",
                        code=resp.status_code, msg=msg))
                raise GeminiRequestError(resp.status_code, _(
                    "Error de Google Gemini (HTTP %(code)s, modelo %(model)s): %(msg)s",
                    code=resp.status_code, model=cur_model, msg=msg))
            if response is not None:
                break

        if response is None:
            best_model = cls.find_best_available_model(api_key)
            if best_model and best_model not in models_to_try:
                try:
                    resp = requests.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{best_model}:generateContent",
                        json=payload, headers=headers, timeout=300)
                    if resp.status_code == 200:
                        response, successful_model = resp, best_model
                    else:
                        errors.append(f"{best_model}: HTTP {resp.status_code} {cls._error_text(resp)[0][:160]}")
                except Exception as e:
                    errors.append(f"{best_model}: {e}")

        if response is None:
            detail = "; ".join(errors[-6:])
            if any('HTTP 429' in e for e in errors):
                raise GeminiRequestError(429, _(
                    "Se agotó la cuota de Google Gemini (HTTP 429). Espera unos minutos y usa "
                    "'Reintentar segmentos fallidos', o activa facturación en tu proyecto de Google. Detalle: %s",
                    detail))
            raise GeminiRequestError(0, _("No se pudo usar ningún modelo de Google Gemini. Detalle: %s", detail))

        res_data = response.json()
        usage_metadata = res_data.get("usageMetadata", {})
        usage = {
            'model': successful_model,
            'prompt_tokens': usage_metadata.get('promptTokenCount', 0),
            'output_tokens': usage_metadata.get('candidatesTokenCount', 0),
            'total_tokens': usage_metadata.get('totalTokenCount', 0),
        }
        candidates = res_data.get("candidates") or []
        if not candidates:
            block = (res_data.get("promptFeedback") or {}).get("blockReason")
            raise GeminiRequestError(0, _("Gemini no devolvió respuesta%s.", f" (bloqueado: {block})" if block else ""))
        parts = (candidates[0].get("content") or {}).get("parts") or []
        # thinking models can return a "thought" part first: use the real answer parts
        text_content = "".join(p.get("text", "") for p in parts if not p.get("thought")) or "{}"
        result_json = cls._parse_json(text_content)
        if result_json is None:
            _logger.warning("Gemini answer is not valid JSON (finish=%s); keeping raw text",
                            candidates[0].get("finishReason"))
            result_json = {"transcript": text_content, "summary": "", "key_points": [], "action_items": []}
        return result_json, usage

    @staticmethod
    def _parse_json(text):
        text = (text or '').strip()
        if text.startswith('```'):
            text = text.strip('`')
            text = text[text.find('{'):] if '{' in text else text
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {"transcript": data}
        except Exception:
            pass
        a, b = text.find('{'), text.rfind('}')
        if a >= 0 and b > a:
            try:
                return json.loads(text[a:b + 1])
            except Exception:
                return None
        return None

    @classmethod
    def test_connection(cls, env):
        """Small text-only call used by the Settings 'Probar conexión' button."""
        api_key, model, _prompt = cls.get_config(env)
        if not api_key:
            raise cls._missing_key_error()
        payload = {
            "contents": [{"parts": [{"text": 'Reply with the JSON {"ok": true}'}]}],
            "generationConfig": {"response_mime_type": "application/json", "temperature": 0},
        }
        _res, usage = cls._generate_content_json(api_key, model, payload, log_label="connection test")
        return usage.get('model')

    @classmethod
    def synthesize_meeting_summary(cls, env, segment_transcripts):
        """Text-only pass over already-transcribed segment texts, producing one coherent
        Summary/Key Points/Action Items for the whole meeting - used to stitch together a long,
        segmented recording where each segment was only transcribed for its own ~5-minute
        window. Much cheaper/faster than another audio call since it's just processing text."""
        api_key, model, custom_prompt = cls.get_config(env)
        if not api_key:
            raise cls._missing_key_error()

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

    @staticmethod
    def _item_text(item):
        if isinstance(item, dict):
            main = item.get('task') or item.get('action') or item.get('item') or item.get('description') \
                or item.get('text') or item.get('point') or ''
            who = item.get('assignee') or item.get('owner') or item.get('responsible') or item.get('assigned_to')
            due = item.get('due') or item.get('due_date') or item.get('deadline')
            extra = " — ".join(str(x) for x in (who, due) if x)
            main = main or ", ".join(f"{k}: {v}" for k, v in item.items())
            return f"{main} ({extra})" if extra else str(main)
        return str(item)

    @classmethod
    def _format_results(cls, result_json):
        def esc(value):
            return str(escape(str(value))).replace('\n', '<br/>')

        transcript_raw = result_json.get('transcript', '')
        if isinstance(transcript_raw, list):
            lines = []
            for line in transcript_raw:
                if isinstance(line, dict):
                    who = line.get('speaker') or line.get('name') or ''
                    said = line.get('text') or line.get('content') or ''
                    ts = line.get('timestamp') or line.get('time') or ''
                    line = f"{'[' + str(ts) + '] ' if ts else ''}{who + ': ' if who else ''}{said}"
                lines.append(f"<p>{esc(line)}</p>")
            transcript_html = "".join(lines)
        else:
            transcript_html = f"<div class='o_meeting_transcript'>{esc(transcript_raw)}</div>"

        summary_raw = result_json.get('summary', '')
        if isinstance(summary_raw, list):
            summary_html = "".join(f"<p>{esc(cls._item_text(item))}</p>" for item in summary_raw)
        else:
            summary_html = f"<div class='o_meeting_summary'>{esc(summary_raw)}</div>"

        key_points_raw = result_json.get('key_points', [])
        if isinstance(key_points_raw, list):
            kp_items = "".join(f"<li>{esc(cls._item_text(item))}</li>" for item in key_points_raw if item)
            key_points_html = f"<ul class='o_meeting_key_points'>{kp_items}</ul>" if kp_items else "<p>—</p>"
        else:
            key_points_html = f"<div>{esc(key_points_raw)}</div>"

        action_items_raw = result_json.get('action_items', [])
        if isinstance(action_items_raw, list):
            ai_items = "".join(
                f"<li><input type='checkbox' disabled='disabled'/> <strong>{esc(cls._item_text(item))}</strong></li>"
                for item in action_items_raw if item)
            action_items_html = (f"<ul class='o_meeting_action_items' style='list-style: none; padding-left: 0;'>"
                                 f"{ai_items}</ul>") if ai_items else "<p>—</p>"
        else:
            action_items_html = f"<div>{esc(action_items_raw)}</div>"

        return {
            'transcript': transcript_html,
            'summary': summary_html,
            'key_points': key_points_html,
            'action_items': action_items_html,
            'raw_json': json.dumps(result_json, indent=2, ensure_ascii=False)
        }
