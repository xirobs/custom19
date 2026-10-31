import { reactive } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { onChange } from "@mail/utils/common/misc";
import { rpc } from "@web/core/network/rpc";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

const AUDIO_RESCAN_INTERVAL = 2000;
// Recording is split into fixed-length segments uploaded as the meeting goes, instead of one
// giant file held in browser memory and uploaded at the end. This is what makes meetings of any
// length possible: a single request/file for a multi-hour meeting would blow past Odoo's
// request size limits and risk crashing the tab before upload even starts.
const SEGMENT_DURATION_MS = 5 * 60 * 1000;

export class CallRecordingService {
    constructor(env, services) {
        this.env = env;
        this.notification = services.notification;
        this.dialog = services.dialog;
        this.store = services["mail.store"];
        this.state = reactive({
            isRecording: false,
            duration: 0,
            channelId: null,
            threadName: "",
        });
        this.audioContext = null;
        this.timerInterval = null;
        this.startTime = null;
        this.sources = new Set();
        this.isVideoRecording = false;

        // full OS-level screen/window/tab capture - the only video source this module records
        this.screenCaptureStream = null;

        this._resetSegmentedState();

        // fetched once; default to true (matches the module's out-of-the-box behavior) if the
        // request fails for any reason, so a network hiccup never silently disables recording.
        this.autoRecordEnabledPromise = rpc("/odoo_nottaker/config")
            .then((res) => res.auto_record_enabled)
            .catch(() => true);

        // Listen for call join / leave on mail store
        if (this.store?.rtc) {
            onChange(this.store.rtc, "selfSession", () => {
                if (this.store.rtc.selfSession) {
                    setTimeout(async () => {
                        if (!this.store.rtc.selfSession || this.state.isRecording) {
                            return;
                        }
                        const store = this.store;
                        const thread = this.store.rtc.channel;
                        const autoRecordEnabled = await this.autoRecordEnabledPromise;
                        if (autoRecordEnabled) {
                            // no prompt - starts immediately (audio only, browsers won't allow
                            // the screen picker to open without a direct click)
                            this.startRecording({ store, thread });
                        } else {
                            this._promptStartRecording({ store, thread });
                        }
                    }, 1000);
                } else if (this.state.isRecording) {
                    this.stopRecording({ store: this.store, thread: this.state.channelId });
                }
            });
        }
    }

    _resetSegmentedState() {
        this.recordingId = null;
        this.segmentIndex = 0;
        this.meetingStartTime = null;
        this.segmentStartTime = null;
        this.segmentTimer = null;
        this.segmentMediaRecorder = null;
        this.segmentChunks = [];
        this.audioOnlyRecorder = null;
        this.audioOnlyChunks = [];
        this.mixAudioStream = null;
        this.heartbeatInterval = null;
        this.currentSegmentSpeakers = new Map();
        this.mixedAudioSourceKeys = new Set();
        this.pendingSegmentUploads = [];
        this.recordStream = null;
        this.micStreamForIsolation = null;
        this.myName = "";
        this.gainNode = null;
        this.audioRescanInterval = null;
        this._thread = null;
        this._rtc = null;
        this._store = null;
    }

    /**
     * Shows a confirmation popup asking whether to record this meeting. Clicking "Start
     * Recording" is a direct user click, so - unlike the auto-record timer - it can actually
     * open the browser's screen-share picker.
     */
    _promptStartRecording({ store, thread }) {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Record this meeting?"),
            body: _t(
                "Record your screen and the meeting audio? You'll be asked to choose what to share " +
                "(entire screen, a window, or a tab). The recording is automatically transcribed with AI when the call ends."
            ),
            confirmLabel: _t("Start Recording"),
            cancelLabel: _t("Don't Record"),
            confirm: () => this.startRecording({ store, thread, manual: true }),
            cancel: () => {},
        });
    }

    get isRecording() {
        return Boolean(this.state.isRecording);
    }

    get formattedDuration() {
        const total = Math.floor(this.state.duration || 0);
        const mins = Math.floor(total / 60);
        const secs = total % 60;
        return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }

    async toggleRecording({ store, thread }) {
        if (this.state.isRecording) {
            await this.stopRecording({ store, thread });
        } else {
            // manual: true - this is a direct click, so the browser will allow the
            // getDisplayMedia() screen/window/tab picker to open.
            await this.startRecording({ store, thread, manual: true });
        }
    }

    /**
     * Prompts the browser's native "Choose what to share" picker (entire screen, a window,
     * or a tab) and returns the resulting video stream, or null if unsupported/denied/cancelled.
     * Only works when called synchronously off a real user gesture (a click) - browsers refuse
     * it when triggered programmatically (e.g. from the auto-record timer).
     */
    async _captureFullScreen() {
        if (!navigator.mediaDevices?.getDisplayMedia) {
            console.warn("Screen capture (getDisplayMedia) is not supported in this browser.");
            return null;
        }
        try {
            return await navigator.mediaDevices.getDisplayMedia({
                video: {
                    frameRate: { ideal: 30, max: 30 },
                    width: { ideal: 1920 },
                    height: { ideal: 1080 },
                },
                audio: false,
            });
        } catch (e) {
            console.warn("Screen capture permission denied or cancelled:", e);
            return null;
        }
    }

    _teardownScreenCapture() {
        if (this.screenCaptureStream) {
            // this is our own captured stream (the OS-level share), safe and correct to stop it -
            // it also clears the browser's "you are sharing your screen" indicator.
            this.screenCaptureStream.getTracks().forEach((t) => t.stop());
            this.screenCaptureStream = null;
        }
    }

    _audioMimeType() {
        const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg"];
        return candidates.find((m) => MediaRecorder.isTypeSupported(m)) || "";
    }

    /** Starts a small dedicated MediaRecorder on one participant's audio for the current
     * segment only, so their speech in this segment can be matched to their real name. */
    _startSpeakerRecorder(key, name, stream) {
        if (this.currentSegmentSpeakers.has(key)) {
            return;
        }
        try {
            const mimeType = this._audioMimeType();
            const mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
            const entry = { name, mediaRecorder, chunks: [] };
            mediaRecorder.ondataavailable = (event) => {
                if (event.data && event.data.size > 0) {
                    entry.chunks.push(event.data);
                }
            };
            mediaRecorder.start(1000);
            this.currentSegmentSpeakers.set(key, entry);
        } catch (e) {
            console.warn(`Could not start isolated recorder for ${name}:`, e);
        }
    }

    /** Stops every recorder in the given per-speaker map and waits for each to flush. */
    async _stopSpeakerRecordersMap(map) {
        const stops = [];
        for (const entry of map.values()) {
            if (entry.mediaRecorder.state === "inactive") {
                continue;
            }
            stops.push(
                new Promise((resolve) => {
                    entry.mediaRecorder.addEventListener("stop", resolve, { once: true });
                    try {
                        entry.mediaRecorder.stop();
                    } catch (e) {
                        resolve();
                    }
                })
            );
        }
        await Promise.all(stops);
    }

    /**
     * Connects one participant's audio into the persistent meeting-long mix (once) AND starts
     * their isolated recorder for the current segment (once per segment). Safe to call
     * repeatedly - already-connected/already-isolated participants are skipped.
     */
    _connectParticipantAudio(key, name, stream) {
        if (!stream || !stream.getAudioTracks || stream.getAudioTracks().length === 0) {
            return false;
        }
        if (!this.mixedAudioSourceKeys.has(key)) {
            try {
                const source = this.audioContext.createMediaStreamSource(stream);
                source.connect(this.gainNode);
                this.sources.add(source);
                this.mixedAudioSourceKeys.add(key);
            } catch (e) {
                console.warn(`Could not mix audio for ${name}:`, e);
            }
        }
        this._startSpeakerRecorder(key, name, stream);
        return true;
    }

    /**
     * Scans the current call sessions (plus the local mic) for anyone whose audio isn't wired
     * up yet for the current segment - late joiners, peers whose WebRTC audio wasn't ready at
     * recording/segment start, etc. Runs once per segment start and on a timer throughout the
     * whole meeting so nobody's voice gets silently dropped.
     */
    _rescanParticipantAudio() {
        if (this.micStreamForIsolation) {
            this._connectParticipantAudio("self", this.myName, this.micStreamForIsolation);
        }
        const sessions = this._thread?.rtc_session_ids || this._rtc?.channel?.rtc_session_ids || [];
        for (const session of sessions) {
            if (this._rtc?.selfSession && session.eq && session.eq(this._rtc.selfSession)) {
                continue;
            }
            const remoteStream = session.audioStream || session.audioElement?.srcObject;
            const name = session.name || _t("Unknown participant");
            this._connectParticipantAudio(`session-${session.id}`, name, remoteStream);
        }
    }

    _computePartnerIds() {
        const partnerIds = [];
        const sessions = this._thread?.rtc_session_ids || this._store?.rtc?.channel?.rtc_session_ids || [];
        for (const s of sessions) {
            if (s.partner_id?.id) {
                partnerIds.push(s.partner_id.id);
            }
        }
        if (this._store?.self?.id && !partnerIds.includes(this._store.self.id)) {
            partnerIds.push(this._store.self.id);
        }
        return partnerIds;
    }

    /** Begins recording the next segment on the same underlying screen/audio streams. */
    _startSegment() {
        this.currentSegmentSpeakers = new Map();
        this._rescanParticipantAudio();

        const mimeCandidates = this.isVideoRecording
            ? ["video/webm;codecs=vp8,opus", "video/webm"]
            : ["audio/webm;codecs=opus", "audio/webm", "audio/ogg"];
        const mimeType = mimeCandidates.find((m) => MediaRecorder.isTypeSupported(m)) || "";
        const options = {
            ...(mimeType && { mimeType }),
            // higher bitrate matters a lot for screen content - text/UI stays legible instead
            // of turning to mush at a low bitrate.
            ...(this.isVideoRecording && { videoBitsPerSecond: 4_000_000 }),
            audioBitsPerSecond: 160_000,
        };
        this.segmentChunks = [];
        this.segmentMediaRecorder = new MediaRecorder(this.recordStream, options);
        this.segmentMediaRecorder.ondataavailable = (event) => {
            if (event.data && event.data.size > 0) {
                this.segmentChunks.push(event.data);
            }
        };
        // Screen video is huge (4 Mbps) and expensive to transcribe: record the same mixed
        // audio separately so only that small file goes to Gemini.
        this.audioOnlyChunks = [];
        this.audioOnlyRecorder = null;
        if (this.isVideoRecording && this.mixAudioStream) {
            try {
                const aMime = this._audioMimeType();
                this.audioOnlyRecorder = new MediaRecorder(this.mixAudioStream, {
                    ...(aMime && { mimeType: aMime }),
                    audioBitsPerSecond: 64_000,
                });
                const chunksRef = this.audioOnlyChunks;
                this.audioOnlyRecorder.ondataavailable = (event) => {
                    if (event.data && event.data.size > 0) {
                        chunksRef.push(event.data);
                    }
                };
                this.audioOnlyRecorder.start(1000);
            } catch (e) {
                console.warn("Note Taker: could not start the audio-only recorder:", e);
                this.audioOnlyRecorder = null;
            }
        }
        this.segmentStartTime = Date.now();
        this.segmentMediaRecorder.start(1000);
        this.segmentTimer = setTimeout(() => this._rotateSegment(), SEGMENT_DURATION_MS);
    }

    /** Stops the current segment's recorders and rolls them up into an upload-ready blob. */
    async _finalizeCurrentSegmentForUpload({ isLast }) {
        const mediaRecorder = this.segmentMediaRecorder;
        const chunks = this.segmentChunks;
        const audioOnlyRecorder = this.audioOnlyRecorder;
        const audioOnlyChunks = this.audioOnlyChunks;
        const speakers = this.currentSegmentSpeakers;
        const startOffset = (this.segmentStartTime - this.meetingStartTime) / 1000;
        const index = this.segmentIndex;
        this.segmentIndex += 1;

        await new Promise((resolve) => {
            if (!mediaRecorder || mediaRecorder.state === "inactive") {
                resolve();
                return;
            }
            mediaRecorder.addEventListener("stop", resolve, { once: true });
            try {
                mediaRecorder.stop();
            } catch (e) {
                resolve();
            }
        });
        await this._stopSpeakerRecordersMap(speakers);
        if (audioOnlyRecorder && audioOnlyRecorder.state !== "inactive") {
            await new Promise((resolve) => {
                audioOnlyRecorder.addEventListener("stop", resolve, { once: true });
                try {
                    audioOnlyRecorder.stop();
                } catch {
                    resolve();
                }
            });
        }
        const audioOnlyBlob = audioOnlyChunks.length
            ? new Blob(audioOnlyChunks, { type: audioOnlyRecorder?.mimeType || "audio/webm" })
            : null;

        const duration = (Date.now() - this.segmentStartTime) / 1000;
        const mimeType = mediaRecorder?.mimeType || (this.isVideoRecording ? "video/webm" : "audio/webm");
        const blob = new Blob(chunks, { type: mimeType });

        const speakerClips = [];
        for (const { name, chunks: sChunks } of speakers.values()) {
            const sMime = this._audioMimeType() || "audio/webm";
            const sBlob = new Blob(sChunks, { type: sMime });
            if (sBlob.size > 0) {
                speakerClips.push({ name, blob: sBlob });
            }
        }

        return { index, startOffset, duration, blob, audioOnlyBlob, speakerClips, isLast };
    }

    /** Rotation timer callback: closes out the current segment and immediately starts the next
     * one, so the meeting is never left un-recorded even for a moment. */
    async _rotateSegment() {
        if (!this.state.isRecording) {
            return;
        }
        const finishedSegment = await this._finalizeCurrentSegmentForUpload({ isLast: false });
        this._uploadSegmentInBackground(finishedSegment);
        this._startSegment();
    }

    /** Uploads one finished segment without blocking the caller - tracked in
     * pendingSegmentUploads so the meeting-end flow can wait for all of them to land. */
    _uploadSegmentInBackground(seg) {
        if (seg.blob.size === 0) {
            console.log(`Note Taker: segment ${seg.index} captured no data, skipping upload.`);
            return;
        }
        console.log(
            `Note Taker: uploading segment ${seg.index} (${seg.blob.size} bytes, ` +
            `${seg.duration.toFixed(1)}s, last=${seg.isLast})`
        );
        const uploadPromise = (async () => {
            const formData = new FormData();
            formData.append("audio", seg.blob, `segment_${seg.index}.webm`);
            formData.append("recording_id", this.recordingId);
            formData.append("sequence", seg.index.toString());
            formData.append("start_offset", seg.startOffset.toString());
            formData.append("duration", seg.duration.toString());
            formData.append("is_video", this.isVideoRecording ? "1" : "0");
            formData.append("is_last", seg.isLast ? "1" : "0");
            if (seg.audioOnlyBlob && seg.audioOnlyBlob.size > 0) {
                formData.append("transcribe_audio", seg.audioOnlyBlob, `segment_${seg.index}_audio.webm`);
            }
            for (const { name, blob } of seg.speakerClips) {
                formData.append("speaker_names", name);
                formData.append("speaker_audio", blob, "speaker.webm");
            }

            let resData = { status: "error", message: "upload never attempted" };
            for (let attempt = 1; attempt <= 3; attempt++) {
                try {
                    const response = await fetch("/odoo_nottaker/upload_segment", {
                        method: "POST",
                        body: formData,
                    });
                    resData = await response.json();
                } catch (err) {
                    resData = { status: "error", message: String(err) };
                }
                if (resData.status === "success" || attempt === 3) {
                    break;
                }
                console.warn(`Note Taker: segment ${seg.index} upload attempt ${attempt} failed (${resData.message}), retrying...`);
                await new Promise((resolve) => setTimeout(resolve, 3000 * attempt));
            }
            if (resData.status !== "success") {
                console.error(`Note Taker: segment ${seg.index} failed to upload:`, resData.message);
                this.notification.add(
                    _t("A recording segment failed to upload - the transcript may have a gap. ") + (resData.message || ""),
                    { title: _t("Note Taker"), type: "warning", sticky: true }
                );
            }
        })();
        this.pendingSegmentUploads.push(uploadPromise);
    }

    async startRecording({ store, thread, manual = false }) {
        if (this.state.isRecording) {
            return;
        }

        try {
            // 0. Full screen/window/tab capture - only possible from a direct user click.
            // This module only ever records the screen for video, never the camera - if this
            // isn't granted (or can't be requested, e.g. auto-record's timer-based start),
            // the recording falls back to audio-only rather than switching to camera video.
            if (manual) {
                this.screenCaptureStream = await this._captureFullScreen();
                if (!this.screenCaptureStream) {
                    this.notification.add(
                        _t("Screen sharing wasn't granted - recording audio only (no camera fallback)."),
                        { title: _t("Auto Note Taker"), type: "warning", sticky: true }
                    );
                }
            } else {
                this.notification.add(
                    _t("Auto-record can't open the screen-share picker (browsers require a click). " +
                       "Recording audio only - click \"Start Recording\" manually to capture your screen."),
                    { title: _t("Auto Note Taker"), type: "warning", sticky: false }
                );
            }

            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            if (!AudioContextClass) {
                console.warn("Web Audio API is not supported in this browser.");
                return;
            }

            this.audioContext = new AudioContextClass();
            if (this.audioContext.state === "suspended") {
                await this.audioContext.resume();
            }

            this._thread = thread;
            this._rtc = store?.rtc;
            this._store = store;

            const destination = this.audioContext.createMediaStreamDestination();
            this.sources.clear();
            this.mixedAudioSourceKeys = new Set();
            this.pendingSegmentUploads = [];
            this.segmentIndex = 0;
            this.meetingStartTime = Date.now();

            // Boost + normalize the mixed audio so speech stays clear and audible: a modest
            // gain lift feeding a compressor (evens out loud/quiet speakers and prevents
            // clipping from the boost). Every audio source connects into this chain, which
            // persists for the whole meeting regardless of segment rotation.
            this.gainNode = this.audioContext.createGain();
            this.gainNode.gain.value = 1.4;
            const compressor = this.audioContext.createDynamicsCompressor();
            compressor.threshold.value = -18;
            compressor.knee.value = 24;
            compressor.ratio.value = 8;
            compressor.attack.value = 0.003;
            compressor.release.value = 0.25;
            this.gainNode.connect(compressor);
            compressor.connect(destination);

            // Local Microphone Stream (kept for the whole meeting; connected into the mix and
            // (re-)isolated per segment via _rescanParticipantAudio/_connectParticipantAudio)
            const micTrack = this._rtc?.state?.audioTrack || this._rtc?.state?.micAudioTrack;
            this.myName = store?.self?.name || _t("Me");
            if (micTrack && micTrack.readyState === "live") {
                this.micStreamForIsolation = new MediaStream([micTrack]);
            } else {
                try {
                    this.micStreamForIsolation = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
                } catch (e) {
                    console.warn("Could not capture local mic directly:", e);
                    this.micStreamForIsolation = null;
                }
            }

            // Also check all audio elements currently playing in the document for RTC (not
            // individually isolated/named - same as before, just part of the general mix)
            const audioElements = document.querySelectorAll("audio");
            audioElements.forEach((audioEl) => {
                if (audioEl.srcObject && audioEl.srcObject.getAudioTracks && audioEl.srcObject.getAudioTracks().length > 0) {
                    try {
                        const elSource = this.audioContext.createMediaStreamSource(audioEl.srcObject);
                        elSource.connect(this.gainNode);
                        this.sources.add(elSource);
                    } catch (e) {
                        // ignore
                    }
                }
            });

            // Build the persistent record stream once: screen video (if granted) + mixed
            // audio, or audio only. Segments are just repeated MediaRecorder passes over this
            // same stream - the underlying tracks never stop/restart between segments.
            this.mixAudioStream = destination.stream;
            this.isVideoRecording = Boolean(this.screenCaptureStream && this.screenCaptureStream.getVideoTracks().length > 0);
            if (this.isVideoRecording) {
                this.recordStream = new MediaStream([
                    ...this.screenCaptureStream.getVideoTracks(),
                    ...destination.stream.getAudioTracks(),
                ]);
                // if the user clicks the browser's own "Stop sharing" bar, end the recording too
                this.screenCaptureStream.getVideoTracks()[0].addEventListener("ended", () => {
                    if (this.state.isRecording) {
                        this.stopRecording({ store, thread });
                    }
                });
            } else {
                this.recordStream = destination.stream;
            }

            // Create the parent recording record up front so segments have somewhere to attach
            // to as they're produced throughout the meeting.
            const startRes = await rpc("/odoo_nottaker/start_recording", {
                channel_id: thread?.id || this._rtc?.state?.channel?.id || null,
                partner_ids: this._computePartnerIds(),
            });
            if (startRes.already_recording) {
                this.notification.add(
                    _t("This call is already being recorded by %s.", startRes.recorded_by || "?"),
                    { title: _t("Auto Note Taker"), type: "info" }
                );
                this._teardownScreenCapture();
                try {
                    await this.audioContext.close();
                } catch {
                    // ignore
                }
                this.audioContext = null;
                this._resetSegmentedState();
                return;
            }
            const { recording_id } = startRes;
            this.recordingId = recording_id;
            this.heartbeatInterval = setInterval(() => {
                if (this.recordingId) {
                    rpc("/odoo_nottaker/heartbeat", { recording_id: this.recordingId }).catch(() => {});
                }
            }, 120000);

            this._startSegment();
            this.audioRescanInterval = setInterval(() => this._rescanParticipantAudio(), AUDIO_RESCAN_INTERVAL);

            this.state.isRecording = true;
            this.state.channelId = thread?.id || this._rtc?.state?.channel?.id || null;
            this.state.threadName = thread?.displayName || thread?.name || "Discuss Meeting";
            this.startTime = Date.now();
            this.state.duration = 0;

            this.timerInterval = setInterval(() => {
                if (this.startTime) {
                    this.state.duration = (Date.now() - this.startTime) / 1000;
                }
            }, 500);

            this.notification.add(
                this.isVideoRecording
                    ? _t("Recording your screen and the meeting audio - will be transcribed automatically with AI.")
                    : _t("Recording meeting audio - will be transcribed automatically with AI."),
                {
                    title: _t("Auto Note Taker"),
                    type: "info",
                    sticky: false,
                }
            );

        } catch (error) {
            console.error("Failed to start meeting recording:", error);
            this.state.isRecording = false;
            if (this.timerInterval) {
                clearInterval(this.timerInterval);
            }
            if (this.audioRescanInterval) {
                clearInterval(this.audioRescanInterval);
            }
            if (this.heartbeatInterval) {
                clearInterval(this.heartbeatInterval);
            }
            if (this.segmentTimer) {
                clearTimeout(this.segmentTimer);
            }
            this._teardownScreenCapture();
            this._resetSegmentedState();
        }
    }

    async stopRecording({ store, thread } = {}) {
        if (!this.state.isRecording) {
            return;
        }

        if (this.timerInterval) {
            clearInterval(this.timerInterval);
            this.timerInterval = null;
        }
        if (this.audioRescanInterval) {
            clearInterval(this.audioRescanInterval);
            this.audioRescanInterval = null;
        }
        if (this.segmentTimer) {
            clearTimeout(this.segmentTimer);
            this.segmentTimer = null;
        }

        if (this.heartbeatInterval) {
            clearInterval(this.heartbeatInterval);
            this.heartbeatInterval = null;
        }
        this.state.isRecording = false;
        const recordingId = this.recordingId;
        const meetingDuration = this.meetingStartTime ? (Date.now() - this.meetingStartTime) / 1000 : 0;

        const finalSegment = await this._finalizeCurrentSegmentForUpload({ isLast: true });
        this._uploadSegmentInBackground(finalSegment);

        this._teardownScreenCapture();
        if (this.audioContext) {
            try {
                await this.audioContext.close();
            } catch (e) {
                // ignore
            }
            this.audioContext = null;
        }

        this.notification.add(_t("Call ended: uploading final recording segment(s)..."), {
            title: _t("Note Taker Processing"),
            type: "info",
            sticky: false,
        });

        await Promise.allSettled(this.pendingSegmentUploads);
        try {
            await rpc("/odoo_nottaker/finish_recording", {
                recording_id: recordingId,
                total_segments: this.segmentIndex,
                duration: meetingDuration,
            });
        } catch (e) {
            console.warn("Note Taker: finish_recording failed", e);
        }

        this.notification.add(
            _t("Recording uploaded - transcribing and generating AI notes in the background. Check Meeting Recordings & AI Notes shortly."),
            {
                title: _t("Note Taker"),
                type: "success",
                sticky: true,
            }
        );

        this._resetSegmentedState();
    }
}

export const callRecordingService = {
    dependencies: ["notification", "dialog", "mail.store"],
    start(env, services) {
        return new CallRecordingService(env, services);
    },
};

registry.category("services").add("odoo_nottaker.call_recording", callRecordingService);
