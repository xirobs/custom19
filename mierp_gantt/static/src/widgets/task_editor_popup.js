/** @odoo-module **/
import { Component, useState } from "@odoo/owl";

export class TaskEditorPopup extends Component {
    static template = "mierp_gantt.TaskEditorPopup";
    static props = ["task", "dependencies?", "allTasks?", "onSave", "onCancel"];

    setup() {
        this.state = useState({
            tab: "general",
            task: { ...this.props.task },
        });
        this.presetColors = [
            "#E8713A", "#B85620", "#F4B400", "#DB4437",
            "#3CC07A", "#138A47", "#4285F4", "#7C3AED",
            "#0891B2", "#94A3B8", "#1F2937", "#FFFFFF",
        ];
    }

    setTab(t) { this.state.tab = t; }

    onChange(field, ev) {
        let v = ev.target.value;
        if (ev.target.type === "checkbox") v = ev.target.checked;
        if (ev.target.type === "number") v = parseFloat(v);
        this.state.task[field] = v;
    }

    pickPreset(hex) { this.state.task.color_hex = hex; }
    clearColor() { this.state.task.color_hex = false; }

    save() { this.props.onSave({ ...this.state.task }); }
    cancel() { this.props.onCancel(); }

    predecessorsOf(taskId) {
        return (this.props.dependencies || []).filter(d => d.successor === taskId);
    }
    successorsOf(taskId) {
        return (this.props.dependencies || []).filter(d => d.predecessor === taskId);
    }
    nameOf(id) {
        const t = (this.props.allTasks || []).find(x => x.id === id);
        return t ? t.name : `#${id}`;
    }
}
