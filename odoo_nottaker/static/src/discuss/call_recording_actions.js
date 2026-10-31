import { registerCallAction } from "@mail/discuss/call/common/call_actions";
import { ACTION_TAGS } from "@mail/core/common/action";
import { _t } from "@web/core/l10n/translation";

export const callRecordAction = {
    condition: ({ thread }) => Boolean(thread?.isSelfInCall),
    name: ({ store }) => {
        const recService = store?.env?.services?.["odoo_nottaker.call_recording"];
        if (recService?.isRecording) {
            return _t("Recording (%s) - Click to Stop", recService.formattedDuration);
        }
        return _t("Start Recording");
    },
    isActive: ({ store }) => {
        const recService = store?.env?.services?.["odoo_nottaker.call_recording"];
        return Boolean(recService?.isRecording);
    },
    isTracked: true,
    icon: ({ store }) => {
        const recService = store?.env?.services?.["odoo_nottaker.call_recording"];
        return recService?.isRecording ? "fa fa-dot-circle-o text-danger pulse-recording" : "fa fa-circle";
    },
    onSelected: ({ store, thread }) => {
        const recService = store?.env?.services?.["odoo_nottaker.call_recording"];
        if (recService) {
            recService.toggleRecording({ store, thread });
        }
    },
    sequence: 20,
    // A dedicated group (not one of core's 100/120/200/300) so this always renders as a
    // visible quick-action in the call bar. Group 200 (raise-hand/share-screen/auto-focus)
    // only ever shows its single lowest-sequence action directly and buries the rest in the
    // "More" (...) dropdown - that's why this button was invisible mid-call before.
    sequenceGroup: 150,
    tags: ({ store }) => {
        const recService = store?.env?.services?.["odoo_nottaker.call_recording"];
        if (recService?.isRecording) {
            return [ACTION_TAGS.DANGER];
        }
        return [];
    },
};

registerCallAction("meeting-recording", callRecordAction);
