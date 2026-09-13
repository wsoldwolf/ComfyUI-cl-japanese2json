import { app } from "/scripts/app.js";

const NODE_NAME = "CLSeed32";
const MAX_SEED_32 = 0x7fffffff;

function widget(node, name) {
    return node.widgets?.find((value) => value.name === name);
}

function hideTransport(target) {
    if (!target) return;
    target.type = "hidden";
    target.computeSize = () => [0, -4];
    target.draw = () => {};
}

function setWidget(node, name, value) {
    const target = widget(node, name);
    if (!target) return;
    target.value = value;
    target.callback?.(value, node, target);
}

function randomSeed32() {
    if (globalThis.crypto?.getRandomValues) {
        const values = new Uint32Array(1);
        do {
            globalThis.crypto.getRandomValues(values);
        } while (values[0] === 0 || values[0] > MAX_SEED_32);
        return values[0];
    }
    return Math.floor(Math.random() * MAX_SEED_32) + 1;
}

function currentSeed(node) {
    const value = Number(widget(node, "seed")?.value);
    return Number.isInteger(value) && value >= 1 && value <= MAX_SEED_32
        ? value
        : randomSeed32();
}

function lockCurrent(node) {
    setWidget(node, "seed", currentSeed(node));
    setWidget(node, "mode", "fixed");
    setWidget(node, "hold_next", false);
    node.graph?.setDirtyCanvas(true, true);
}

function chooseRandom(node) {
    // Choose now so the user can see/save the next seed. hold_next makes the
    // first queued execution use it unchanged; later random executions reroll.
    setWidget(node, "seed", randomSeed32());
    setWidget(node, "mode", "random");
    setWidget(node, "hold_next", true);
    node.graph?.setDirtyCanvas(true, true);
}

function addButton(node, name, callback) {
    const button = node.addWidget("button", name, null, callback, { serialize: false });
    button.serialize = false;
    return button;
}

function firstValue(value) {
    return Array.isArray(value) ? value[0] : value;
}

app.registerExtension({
    name: "cl_japanese2json.seed32",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const result = originalCreated?.apply(this, args);
            hideTransport(widget(this, "hold_next"));
            addButton(this, "fixed", () => lockCurrent(this));
            addButton(this, "reuse", () => lockCurrent(this));
            addButton(this, "random", () => chooseRandom(this));
            const size = this.computeSize?.();
            if (Array.isArray(size)) {
                this.setSize([Math.max(260, size[0]), Math.max(size[1], this.size?.[1] ?? 0)]);
            }
            return result;
        };

        const originalConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (...args) {
            const result = originalConfigure?.apply(this, args);
            hideTransport(widget(this, "hold_next"));
            return result;
        };

        const originalExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const result = originalExecuted?.apply(this, arguments);
            const resolved = Number(firstValue(message?.seed));
            if (Number.isInteger(resolved) && resolved >= 1 && resolved <= MAX_SEED_32) {
                setWidget(this, "seed", resolved);
            }
            setWidget(this, "hold_next", false);
            this.graph?.setDirtyCanvas(true, true);
            return result;
        };
    },
});
