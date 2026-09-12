import { app } from "/scripts/app.js";

const NODE_NAME = "CLImageAnalyzerVisionGGUF";
const H3_NODE_TYPES = new Set(["MiniMaxH3ReferenceToVideo"]);
const REF_INPUT = /^(?:ref_images\.)?ref_image_([0-8])$/;

function widget(node, name) {
    return node.widgets?.find((value) => value.name === name);
}

function linkById(id) {
    const links = app.graph?.links;
    if (!links) return null;
    if (links instanceof Map) return links.get(id) ?? null;
    return links[id] ?? links[String(id)] ?? null;
}

function autoReferences(node) {
    const references = new Set();
    const output = node.outputs?.[0];
    for (const linkId of output?.links ?? []) {
        const link = linkById(linkId);
        if (!link) continue;
        const target = app.graph?.getNodeById?.(link.target_id);
        if (!target || !H3_NODE_TYPES.has(target.type)) continue;
        const name = target.inputs?.[link.target_slot]?.name ?? "";
        const match = REF_INPUT.exec(name);
        if (match) references.add(Number(match[1]) + 1);
    }
    return [...references].sort((a, b) => a - b);
}

function labelState(node) {
    const mode = String(widget(node, "picture_reference_mode")?.value ?? "auto_h3");
    if (mode === "none") {
        return { text: "Picture参照: 無効", color: "#aeb7c2" };
    }
    if (mode === "manual") {
        const index = Number(widget(node, "picture_index")?.value ?? 1);
        return { text: `Picture参照: <Picture ${index}> (manual)`, color: "#58e5eb" };
    }
    const references = autoReferences(node);
    if (references.length === 0) {
        return { text: "Picture参照: なし", color: "#aeb7c2" };
    }
    if (references.length === 1) {
        return { text: `Picture参照: <Picture ${references[0]}>`, color: "#58e5eb" };
    }
    return {
        text: `Picture参照: 競合 (${references.map((value) => `<Picture ${value}>`).join(", ")})`,
        color: "#ff7272",
    };
}

function scheduleUpdate(node) {
    clearTimeout(node.__clVisionLabelTimer);
    node.__clVisionLabelTimer = setTimeout(() => {
        const display = node.__clVisionPictureWidget;
        if (!display) return;
        const state = labelState(node);
        display.name = state.text;
        display.options ??= {};
        display.options.textColor = state.color;
        node.graph?.setDirtyCanvas(true, true);
    }, 0);
}

function migrateLegacyWidgets(node, serialized) {
    const named = serialized?.widgets_values_named;
    if (!named || Object.prototype.hasOwnProperty.call(named, "subject_hint")) {
        return;
    }

    // Older workflows ended with the IMAGEUPLOAD sentinel "image". After
    // subject_hint was inserted, positional LiteGraph restore could put that
    // sentinel in the new text widget. Named absence proves this is the old
    // schema, so resetting the new widgets does not overwrite user data.
    const hint = widget(node, "subject_hint");
    if (hint) hint.value = "";
    const mode = widget(node, "hint_mode");
    if (mode) mode.value = "lock_identity";
    const conflict = widget(node, "hint_conflict");
    if (conflict) conflict.value = "warn";
}

function wrapHook(prototype, name) {
    const original = prototype[name];
    prototype[name] = function (...args) {
        const result = original?.apply(this, args);
        scheduleUpdate(this);
        return result;
    };
}

app.registerExtension({
    name: "cl_japanese2json.image_analyzer_vision",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const result = originalCreated?.apply(this, args);
            const display = this.addWidget(
                "button",
                "Picture参照: なし",
                null,
                () => {},
                { serialize: false },
            );
            display.serialize = false;
            display.disabled = true;
            this.__clVisionPictureWidget = display;

            for (const name of ["picture_reference_mode", "picture_index"]) {
                const source = widget(this, name);
                if (!source || source.__clVisionWrapped) continue;
                const callback = source.callback;
                source.callback = (...callbackArgs) => {
                    const value = callback?.apply(source, callbackArgs);
                    scheduleUpdate(this);
                    return value;
                };
                source.__clVisionWrapped = true;
            }
            const size = this.computeSize?.();
            if (Array.isArray(size)) {
                this.setSize([Math.max(390, size[0]), Math.max(size[1], this.size?.[1] ?? 0)]);
            }
            scheduleUpdate(this);
            return result;
        };

        const originalConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (...args) {
            const result = originalConfigure?.apply(this, args);
            migrateLegacyWidgets(this, args[0]);
            scheduleUpdate(this);
            return result;
        };

        for (const hook of ["onAdded", "onConnectionsChange"]) {
            wrapHook(nodeType.prototype, hook);
        }
    },
});
