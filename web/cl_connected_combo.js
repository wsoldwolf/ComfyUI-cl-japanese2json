import { app } from "/scripts/app.js";

const NODE_NAME = "CLConnectedCombo";
const ENUM_TRANSPORT = "enum_values_json";
const SELECTED_VALUE = "selected_value";
const REFRESH_INTERVAL_MS = 750;
const MAX_TRAVERSAL_DEPTH = 64;
const EMPTY_LABEL = "(COMBO入力へ接続してください)";
const CONFLICT_LABEL = "(接続先COMBOの候補が一致しません)";

// Node definitions are authoritative for widgets converted to inputs.  The
// optional connected_combo_source key lets a free STRING override explicitly
// point at the sibling COMBO that defines its legal values.
const inputDefinitions = new Map();

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

function rememberInputDefinitions(nodeData) {
    const definitions = new Map();
    for (const groupName of ["required", "optional"]) {
        const group = nodeData?.input?.[groupName] ?? {};
        for (const [name, spec] of Object.entries(group)) {
            if (!Array.isArray(spec)) continue;
            const declaredType = spec[0];
            const options = spec[1] ?? {};
            definitions.set(name, {
                values: Array.isArray(declaredType) ? declaredType.slice() : null,
                connectedComboSource:
                    typeof options.connected_combo_source === "string"
                        ? options.connected_combo_source
                        : null,
            });
        }
    }
    inputDefinitions.set(nodeData.name, definitions);
}

function nodeDefinition(node) {
    const name = node?.comfyClass ?? node?.type ?? node?.constructor?.comfyClass;
    return inputDefinitions.get(name) ?? null;
}

function stringValues(raw) {
    if (!Array.isArray(raw) || raw.length === 0) return null;
    if (!raw.every((value) => typeof value === "string" && value.length > 0)) {
        return null;
    }
    if (new Set(raw).size !== raw.length) return null;
    return raw.slice();
}

function widgetValues(targetWidget) {
    const raw = targetWidget?.options?.values;
    // Do not execute a destination callback while inspecting the graph.  The
    // registered node definition remains the safe fallback for dynamic UIs.
    return typeof raw === "function" ? null : stringValues(raw);
}

function slotIndex(node, input, fallback = -1) {
    if (Number.isInteger(input)) return input;
    const byIdentity = node?.inputs?.indexOf?.(input) ?? -1;
    if (byIdentity >= 0) return byIdentity;
    if (Number.isInteger(input?.slot_index)) return input.slot_index;
    if (Number.isInteger(input?.slotIndex)) return input.slotIndex;
    return fallback;
}

function widgetFromInput(node, input, index) {
    for (const key of [input, index]) {
        if (key === undefined || key === null || key === -1) continue;
        try {
            const resolved = node?.getWidgetFromSlot?.(key);
            if (resolved) return resolved;
        } catch (_error) {
            // Older LiteGraph builds only accept one of the two slot forms.
        }
    }
    const widgetName = input?.widget?.name ?? input?.name;
    return widgetName ? widget(node, widgetName) : null;
}

function enumAtDestination(node, input, index) {
    const inputName = input?.name ?? node?.inputs?.[index]?.name;
    if (!inputName) return null;
    const definitions = nodeDefinition(node);
    const definition = definitions?.get(inputName);
    const sourceName = definition?.connectedComboSource ?? inputName;

    let targetWidget = null;
    if (sourceName === inputName) {
        targetWidget = widgetFromInput(node, input, index);
    } else {
        targetWidget = widget(node, sourceName);
    }
    const liveValues = widgetValues(targetWidget);
    if (liveValues) return liveValues;

    return stringValues(definitions?.get(sourceName)?.values);
}

function graphLink(graph, linkOrId) {
    if (linkOrId && typeof linkOrId === "object") return linkOrId;
    const fromMethod = graph?.getLink?.(linkOrId);
    if (fromMethod) return fromMethod;
    const links = graph?.links;
    if (links instanceof Map) return links.get(linkOrId) ?? null;
    return links?.[linkOrId] ?? null;
}

function graphNode(graph, id) {
    return graph?.getNodeById?.(id)
        ?? graph?._nodes_by_id?.[id]
        ?? graph?._nodes?.find?.((node) => node.id === id)
        ?? null;
}

function resolveLink(graph, link) {
    try {
        const resolved = link?.resolve?.(graph);
        if (resolved?.inputNode) return resolved;
    } catch (_error) {
        // Fall through to legacy LiteGraph link fields.
    }
    const inputNode = graphNode(graph, link?.target_id ?? link?.targetId);
    const index = link?.target_slot ?? link?.targetSlot ?? -1;
    return inputNode
        ? { inputNode, input: inputNode.inputs?.[index], inputIndex: index }
        : null;
}

function graphNodes(graph) {
    if (Array.isArray(graph?._nodes)) return graph._nodes;
    if (Array.isArray(graph?.nodes)) return graph.nodes;
    return [];
}

function findSubgraphHosts(rootGraph, targetSubgraph) {
    const hosts = [];
    const visited = new Set();
    const visit = (graph) => {
        if (!graph || visited.has(graph)) return;
        visited.add(graph);
        for (const node of graphNodes(graph)) {
            if (node?.subgraph === targetSubgraph) hosts.push(node);
            if (node?.subgraph) visit(node.subgraph);
        }
    };
    visit(rootGraph);
    return hosts;
}

function discoverConnectedEnums(sourceNode) {
    const candidates = [];
    const visitedLinks = new WeakSet();

    const walkOutput = (graph, node, outputIndex, depth) => {
        if (depth > MAX_TRAVERSAL_DEPTH) return;
        const links = node?.outputs?.[outputIndex]?.links ?? [];
        for (const linkOrId of links) walkLink(graph, linkOrId, depth + 1);
    };

    const walkResolved = (graph, resolved, link, depth) => {
        if (depth > MAX_TRAVERSAL_DEPTH || !resolved?.inputNode) return;
        const targetNode = resolved.inputNode;
        const fallbackIndex = resolved.inputIndex
            ?? link?.target_slot
            ?? link?.targetSlot
            ?? -1;
        const index = slotIndex(targetNode, resolved.input, fallbackIndex);
        const input = typeof resolved.input === "object"
            ? resolved.input
            : targetNode.inputs?.[index];

        const values = enumAtDestination(targetNode, input, index);
        if (values) {
            candidates.push({
                values,
                target: `${targetNode.title ?? targetNode.type}:${input?.name ?? index}`,
            });
            return;
        }

        // Source node can live inside a subgraph.  Follow its output through
        // every host instance and continue in the parent graph.
        if (graph?.outputNode === targetNode) {
            const outputIndex = index >= 0 ? index : fallbackIndex;
            for (const host of findSubgraphHosts(app.rootGraph, graph)) {
                walkOutput(host.graph ?? app.rootGraph, host, outputIndex, depth + 1);
            }
            return;
        }

        // Source node can also live outside a subgraph.  Resolve a promoted or
        // plain host input to all concrete destinations inside the definition.
        if (targetNode?.subgraph) {
            const subgraph = targetNode.subgraph;
            const boundarySlot = subgraph.inputNode?.slots?.[index];
            const innerLinkIds = boundarySlot?.linkIds ?? [];
            if (innerLinkIds.length > 0) {
                for (const innerLinkId of innerLinkIds) {
                    walkLink(subgraph, innerLinkId, depth + 1);
                }
                return;
            }
            try {
                const innerConnections = targetNode.resolveSubgraphInputLinks?.(index);
                for (const inner of innerConnections ?? []) {
                    walkResolved(subgraph, inner, null, depth + 1);
                }
            } catch (_error) {
                // A temporarily incomplete subgraph is treated as unresolved.
            }
            return;
        }

        const typeName = String(
            targetNode?.comfyClass ?? targetNode?.type ?? targetNode?.constructor?.name ?? "",
        ).toLowerCase();
        if (typeName.includes("reroute")) {
            for (let outputIndex = 0; outputIndex < (targetNode.outputs?.length ?? 0); outputIndex += 1) {
                walkOutput(graph, targetNode, outputIndex, depth + 1);
            }
        }
    };

    const walkLink = (graph, linkOrId, depth) => {
        if (depth > MAX_TRAVERSAL_DEPTH) return;
        const link = graphLink(graph, linkOrId);
        if (!link || (typeof link === "object" && visitedLinks.has(link))) return;
        if (typeof link === "object") visitedLinks.add(link);
        const resolved = resolveLink(graph, link);
        if (resolved) walkResolved(graph, resolved, link, depth + 1);
    };

    walkOutput(sourceNode.graph ?? app.canvas?.graph ?? app.rootGraph, sourceNode, 0, 0);

    if (candidates.length === 0) {
        return { values: [], status: "empty", targets: [] };
    }
    const firstSignature = JSON.stringify(candidates[0].values);
    const conflict = candidates.some(
        (candidate) => JSON.stringify(candidate.values) !== firstSignature,
    );
    if (conflict) {
        return {
            values: [],
            status: "conflict",
            targets: candidates.map((candidate) => candidate.target),
        };
    }
    return {
        values: candidates[0].values,
        status: "ready",
        targets: candidates.map((candidate) => candidate.target),
    };
}

function replaceSelectedWidgetWithCombo(node) {
    const existing = widget(node, SELECTED_VALUE);
    if (!existing) return null;
    if (existing.__clConnectedComboWidget === true) return existing;

    const existingIndex = node.widgets?.indexOf(existing) ?? -1;
    const existingValue = String(existing.value ?? "");
    const existingCallback = existing.callback;
    if (existingIndex >= 0) node.widgets.splice(existingIndex, 1);
    existing.onRemove?.();
    for (const element of new Set([existing.inputEl, existing.element])) {
        element?.remove?.();
    }

    const combo = node.addWidget(
        "combo",
        SELECTED_VALUE,
        existingValue,
        (value) => {
            existingCallback?.(value, node, combo);
            node.graph?.setDirtyCanvas(true, true);
        },
        { values: [EMPTY_LABEL] },
    );
    combo.__clConnectedComboWidget = true;

    const appendedIndex = node.widgets.indexOf(combo);
    if (existingIndex >= 0 && appendedIndex >= 0 && appendedIndex !== existingIndex) {
        node.widgets.splice(appendedIndex, 1);
        node.widgets.splice(existingIndex, 0, combo);
    }
    return combo;
}

function updateTransport(node, values) {
    const transport = widget(node, ENUM_TRANSPORT);
    if (!transport) return;
    const serialized = JSON.stringify(values);
    if (transport.value === serialized) return;
    transport.value = serialized;
    transport.callback?.(serialized, node, transport);
}

function refreshConnectedCombo(node) {
    const selectedWidget = replaceSelectedWidgetWithCombo(node);
    const transport = widget(node, ENUM_TRANSPORT);
    if (!selectedWidget || !transport) return;
    hideSerializedWidget(transport);

    const discovery = discoverConnectedEnums(node);
    const signature = JSON.stringify(discovery);
    if (signature === node.__clConnectedComboSignature) return;
    node.__clConnectedComboSignature = signature;
    node.__clConnectedComboStatus = discovery.status;

    updateTransport(node, discovery.values);
    selectedWidget.options ??= {};
    if (discovery.status === "ready") {
        selectedWidget.options.values = discovery.values;
        selectedWidget.options.tooltip = `接続先: ${discovery.targets.join(", ")}`;
        if (!discovery.values.includes(String(selectedWidget.value ?? ""))) {
            selectedWidget.value = discovery.values[0];
            selectedWidget.callback?.(selectedWidget.value, node, selectedWidget);
        }
    } else {
        const placeholder = discovery.status === "conflict"
            ? CONFLICT_LABEL
            : EMPTY_LABEL;
        selectedWidget.options.values = [placeholder];
        selectedWidget.value = placeholder;
        selectedWidget.options.tooltip = discovery.status === "conflict"
            ? `候補が異なる接続先: ${discovery.targets.join(", ")}`
            : "出力を文字列COMBO又は宣言済みoverride入力へ接続してください。";
        selectedWidget.callback?.(selectedWidget.value, node, selectedWidget);
        if (discovery.status === "conflict") {
            console.warn(
                "[cl_connected_combo] downstream COMBO inputs expose incompatible value lists",
                discovery.targets,
            );
        }
    }
    node.graph?.setDirtyCanvas(true, true);
}

function scheduleRefresh(node) {
    clearTimeout(node.__clConnectedComboRefreshTimer);
    node.__clConnectedComboRefreshTimer = setTimeout(
        () => refreshConnectedCombo(node),
        0,
    );
}

function startPolling(node) {
    clearInterval(node.__clConnectedComboPollTimer);
    node.__clConnectedComboPollTimer = setInterval(
        () => refreshConnectedCombo(node),
        REFRESH_INTERVAL_MS,
    );
}

app.registerExtension({
    name: "cl_japanese2json.connected_combo",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        rememberInputDefinitions(nodeData);
        if (nodeData.name !== NODE_NAME) return;

        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const result = originalCreated?.apply(this, args);
            hideSerializedWidget(widget(this, ENUM_TRANSPORT));
            replaceSelectedWidgetWithCombo(this);
            const size = this.computeSize?.();
            if (Array.isArray(size)) {
                this.setSize([
                    Math.max(280, size[0]),
                    Math.max(size[1], this.size?.[1] ?? 0),
                ]);
            }
            scheduleRefresh(this);
            startPolling(this);
            return result;
        };

        const originalConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (...args) {
            const serialized = args[0];
            const savedSelection = serialized?.widgets_values_named?.selected_value
                ?? serialized?.widgets_values?.at?.(-1);
            const result = originalConfigure?.apply(this, args);
            hideSerializedWidget(widget(this, ENUM_TRANSPORT));
            const selectedWidget = replaceSelectedWidgetWithCombo(this);
            if (savedSelection !== undefined && selectedWidget) {
                selectedWidget.value = String(savedSelection);
            }
            this.__clConnectedComboSignature = null;
            scheduleRefresh(this);
            startPolling(this);
            return result;
        };

        const originalConnectionsChange = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function (...args) {
            const result = originalConnectionsChange?.apply(this, args);
            this.__clConnectedComboSignature = null;
            scheduleRefresh(this);
            return result;
        };

        const originalAdded = nodeType.prototype.onAdded;
        nodeType.prototype.onAdded = function (...args) {
            const result = originalAdded?.apply(this, args);
            this.__clConnectedComboSignature = null;
            scheduleRefresh(this);
            startPolling(this);
            return result;
        };

        const originalRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function (...args) {
            clearTimeout(this.__clConnectedComboRefreshTimer);
            clearInterval(this.__clConnectedComboPollTimer);
            return originalRemoved?.apply(this, args);
        };
    },
});
