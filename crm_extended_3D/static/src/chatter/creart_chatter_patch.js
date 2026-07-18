/** @odoo-module **/

import { Chatter } from "@mail/chatter/web_portal/chatter";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

patch(Chatter.prototype, {
    get showCreartTeamNotes() {
        return this.props.threadModel === "crm.lead";
    },

    async openCreartTeamNotes() {
        this.closeSearch();
        const open = async (thread) => {
            await new Promise((resolve) =>
                this.env.services.action.doAction(
                    {
                        type: "ir.actions.act_window",
                        name: _t("Notas internas del equipo"),
                        res_model: "creart.crm.chatter.note.wizard",
                        view_mode: "form",
                        views: [[false, "form"]],
                        target: "new",
                        context: {
                            default_lead_id: thread.id,
                        },
                    },
                    {
                        onClose: resolve,
                        additionalContext: {
                            dialog_size: "medium",
                        },
                    }
                )
            );
            this.load(thread, ["messages"]);
            if (this.props.hasParentReloadOnMessagePosted) {
                await this.reloadParentView();
            }
        };
        if (this.state.thread?.id) {
            await open(this.state.thread);
        } else {
            this.onThreadCreated = open;
            await this.props.saveRecord?.();
        }
    },
});
