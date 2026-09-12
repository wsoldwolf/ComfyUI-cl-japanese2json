import { app } from "/scripts/app.js";

const NODE_NAME = "CLStringCombo";
const LIST_PROPERTY = "string_list";
const DEFAULT_LIST = "foo|bar";

function widget(node, name) {
    return node.widgets?.find((value) => value.name === name);
}

function hideSerializedWidget(target) {
    if (!target) return;
    target.type = "hidden";
    target.computeSize = () => [0, -4];
    target.draw = () => {};
    for (const element of new Set([target.inputEl, target.element])) {
        if (element?.style) {
            element.style.display = "none";
            element.style.height = "0";
            element.style.margin = "0";
        }
    }
}

function ensureListProperty(node) {
    node.properties ??= {};
    const current = Object.prototype.hasOwnProperty.call(node.properties, LIST_PROPERTY)
        ? String(node.properties[LIST_PROPERTY] ?? "")
        : DEFAULT_LIST;
    const hasDefinition = node.properties_info?.some(
        (info) => info?.name === LIST_PROPERTY,
    );
    if (!hasDefinition) {
        node.addProperty(LIST_PROPERTY, current, "string");
    } else {
        node.properties[LIST_PROPERTY] = current;
    }
}

function parseStringList(value) {
    if (typeof value !== "string") {
        throw new Error("string_list must be a string");
    }
    if (value.includes("\r") || value.includes("\n")) {
        throw new Error("string_list must be one physical line");
    }

    const items = [];
    let current = "";
    for (let index = 0; index < value.length;) {
        if (value[index] !== "|") {
            current += value[index];
            index += 1;
        } else if (index + 1 < value.length && value[index + 1] === "|") {
            current += "|";
            index += 2;
        } else {
            items.push(current.trim());
            current = "";
            index += 1;
        }
    }
    items.push(current.trim());

    if (items.some((item) => item.length === 0)) {
        throw new Error(
            "empty item: remove leading/trailing separators or use || for a literal pipe",
        );
    }
    if (new Set(items).size !== items.length) {
        throw new Error("duplicate items are not allowed");
    }
    return items;
}

function syncPropertyToTransport(node, propertyValue) {
    const listWidget = widget(node, LIST_PROPERTY);
    if (!listWidget) return;
    const value = String(propertyValue ?? "");
    listWidget.value = value;
    listWidget.callback?.(value, node, listWidget);
}

function replaceSelectedWidgetWithCombo(node) {
    const existing = widget(node, "selected_value");
    if (!existing) return null;
    if (existing.__clStringComboWidget === true) return existing;

    const existingIndex = node.widgets?.indexOf(existing) ?? -1;
    const existingValue = String(existing.value ?? "");
    const existingCallback = existing.callback;
    if (existingIndex >= 0) {
        node.widgets.splice(existingIndex, 1);
    }
    existing.onRemove?.();
    for (const element of new Set([existing.inputEl, existing.element])) {
        element?.remove?.();
    }

    const combo = node.addWidget(
        "combo",
        "selected_value",
        existingValue,
        (value) => {
            existingCallback?.(value, node, combo);
            node.graph?.setDirtyCanvas(true, true);
        },
        { values: [] },
    );
    combo.__clStringComboWidget = true;

    // addWidget appends. Restore the original backend widget position so old
    // widgets_values arrays and newly serialized workflows keep the same order.
    const appendedIndex = node.widgets.indexOf(combo);
    if (existingIndex >= 0 && appendedIndex >= 0 && appendedIndex !== existingIndex) {
        node.widgets.splice(appendedIndex, 1);
        node.widgets.splice(existingIndex, 0, combo);
    }
    return combo;
}

function refreshCombo(node) {
    const listWidget = widget(node, LIST_PROPERTY);
    const selectedWidget = replaceSelectedWidgetWithCombo(node);
    if (!listWidget || !selectedWidget) return;

    const listValue = String(node.properties?.[LIST_PROPERTY] ?? DEFAULT_LIST);
    syncPropertyToTransport(node, listValue);
    hideSerializedWidget(listWidget);

    selectedWidget.options ??= {};
    try {
        const values = parseStringList(listValue);
        selectedWidget.options.values = values;
        if (!values.includes(String(selectedWidget.value ?? ""))) {
            selectedWidget.value = values[0];
            selectedWidget.callback?.(selectedWidget.value, node, selectedWidget);
        }
        node.__clStringComboError = null;
    } catch (error) {
        selectedWidget.options.values = [];
        node.__clStringComboError = String(error?.message ?? error);
        console.warn("[cl_string_combo] " + node.__clStringComboError);
    }
    node.graph?.setDirtyCanvas(true, true);
}

function scheduleRefresh(node) {
    clearTimeout(node.__clStringComboTimer);
    node.__clStringComboTimer = setTimeout(() => refreshCombo(node), 0);
}

app.registerExtension({
    name: "cl_japanese2json.string_combo",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const result = originalCreated?.apply(this, args);
            ensureListProperty(this);
            hideSerializedWidget(widget(this, LIST_PROPERTY));
            replaceSelectedWidgetWithCombo(this);
            const size = this.computeSize?.();
            if (Array.isArray(size)) {
                this.setSize([
                    Math.max(260, size[0]),
                    Math.max(size[1], this.size?.[1] ?? 0),
                ]);
            }
            scheduleRefresh(this);
            return result;
        };

        const originalConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (...args) {
            const serialized = args[0];
            const savedSelection = serialized?.widgets_values_named?.selected_value
                ?? serialized?.widgets_values?.at?.(-1);
            const result = originalConfigure?.apply(this, args);
            ensureListProperty(this);

            const savedProperties = serialized?.properties;
            if (
                !savedProperties
                || !Object.prototype.hasOwnProperty.call(savedProperties, LIST_PROPERTY)
            ) {
                // Migration from the first implementation: the list used to
                // be a visible serialized widget and no node property existed.
                const legacyValue = widget(this, LIST_PROPERTY)?.value;
                this.properties[LIST_PROPERTY] = String(legacyValue ?? DEFAULT_LIST);
            }
            syncPropertyToTransport(this, this.properties[LIST_PROPERTY]);
            hideSerializedWidget(widget(this, LIST_PROPERTY));
            const selectedWidget = replaceSelectedWidgetWithCombo(this);
            if (savedSelection !== undefined && selectedWidget) {
                selectedWidget.value = String(savedSelection);
            }
            scheduleRefresh(this);
            return result;
        };

        const originalPropertyChanged = nodeType.prototype.onPropertyChanged;
        nodeType.prototype.onPropertyChanged = function (
            propertyName,
            propertyValue,
            ...args
        ) {
            const result = originalPropertyChanged?.call(
                this,
                propertyName,
                propertyValue,
                ...args,
            );
            if (propertyName === LIST_PROPERTY) {
                this.properties ??= {};
                const nextValue = propertyValue === undefined
                    ? this.properties[LIST_PROPERTY]
                    : propertyValue;
                this.properties[LIST_PROPERTY] = String(nextValue ?? "");
                syncPropertyToTransport(this, this.properties[LIST_PROPERTY]);
                scheduleRefresh(this);
            }
            return result;
        };

        const originalRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function (...args) {
            clearTimeout(this.__clStringComboTimer);
            return originalRemoved?.apply(this, args);
        };
    },
});
