import { app } from "/scripts/app.js";

const NODE_NAME = "CLLoadTextFile";
const MAX_FILE_BYTES = 16 * 1024 * 1024;
const PICKER_ACCEPT = [
    "text/*",
    ".txt",
    ".md",
    ".markdown",
    ".srt",
    ".vtt",
    ".csv",
    ".tsv",
    ".json",
    ".yaml",
    ".yml",
    ".log",
    ".prompt",
].join(",");

function hideSerializedWidget(widget) {
    if (!widget) {
        return;
    }
    widget.type = "hidden";
    widget.computeSize = () => [0, -4];
    widget.draw = () => {};
    for (const element of new Set([widget.inputEl, widget.element])) {
        if (element?.style) {
            element.style.display = "none";
            element.style.height = "0";
            element.style.margin = "0";
        }
    }
}

function bytesToBase64(bytes) {
    const chunks = [];
    const chunkSize = 0x8000;
    for (let offset = 0; offset < bytes.length; offset += chunkSize) {
        chunks.push(
            String.fromCharCode.apply(
                null,
                bytes.subarray(offset, Math.min(offset + chunkSize, bytes.length)),
            ),
        );
    }
    return btoa(chunks.join(""));
}

function readableSize(bytes) {
    if (bytes < 1024) {
        return `${bytes} B`;
    }
    if (bytes < 1024 * 1024) {
        return `${(bytes / 1024).toFixed(1)} KiB`;
    }
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function reportError(node, button, message) {
    console.error(`[cl_textfile] ${message}`);
    button.name = `読み込み失敗: ${message}`;
    button.options ??= {};
    button.options.textColor = "#ff8a80";
    node.graph?.setDirtyCanvas(true, true);
}

app.registerExtension({
    name: "cl_japanese2json.text_file_loader",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) {
            return;
        }

        const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const result = originalOnNodeCreated?.apply(this, args);
            const node = this;
            const fileNameWidget = node.widgets?.find((w) => w.name === "file_name");
            const fileBase64Widget = node.widgets?.find((w) => w.name === "file_base64");
            const fileSignatureWidget = node.widgets?.find(
                (w) => w.name === "file_signature",
            );

            for (const widget of [
                fileNameWidget,
                fileBase64Widget,
                fileSignatureWidget,
            ]) {
                hideSerializedWidget(widget);
            }

            const fileInput = document.createElement("input");
            fileInput.type = "file";
            fileInput.accept = PICKER_ACCEPT;
            fileInput.style.display = "none";
            document.body.append(fileInput);

            let readGeneration = 0;
            const button = node.addWidget(
                "button",
                "テキストファイルを選択 / D&D",
                "select",
                () => {
                    fileInput.value = "";
                    fileInput.click();
                },
            );
            button.serialize = false;

            const refreshButton = () => {
                if (fileNameWidget?.value) {
                    let size = null;
                    try {
                        size = JSON.parse(fileSignatureWidget?.value || "{}").size;
                    } catch (_error) {
                        // Old workflows may contain a non-JSON signature.
                    }
                    const suffix = Number.isFinite(size) ? ` (${readableSize(size)})` : "";
                    button.name = `選択済み: ${fileNameWidget.value}${suffix}`;
                } else {
                    button.name = "テキストファイルを選択 / D&D";
                }
                button.options ??= {};
                button.options.textColor = undefined;
                node.graph?.setDirtyCanvas(true, true);
            };

            const assignWidgetValue = (widget, value) => {
                widget.value = value;
                widget.callback?.(value, node, widget);
            };

            const loadFile = async (file) => {
                if (!(file instanceof File)) {
                    return false;
                }
                if (file.size > MAX_FILE_BYTES) {
                    reportError(
                        node,
                        button,
                        `16 MiBを超えています (${readableSize(file.size)})`,
                    );
                    return true;
                }

                const generation = ++readGeneration;
                button.name = `読み込み中: ${file.name}`;
                node.graph?.setDirtyCanvas(true, true);
                try {
                    const bytes = new Uint8Array(await file.arrayBuffer());
                    new TextDecoder("utf-8", { fatal: true }).decode(bytes);
                    if (generation !== readGeneration) {
                        return true;
                    }
                    assignWidgetValue(fileNameWidget, file.name);
                    assignWidgetValue(fileBase64Widget, bytesToBase64(bytes));
                    assignWidgetValue(
                        fileSignatureWidget,
                        JSON.stringify({
                            size: file.size,
                            lastModified: file.lastModified,
                        }),
                    );
                    refreshButton();
                    app.graph?.setDirtyCanvas(true, true);
                    return true;
                } catch (error) {
                    reportError(
                        node,
                        button,
                        error instanceof TypeError
                            ? "UTF-8テキストではありません"
                            : String(error),
                    );
                    return true;
                }
            };

            fileInput.addEventListener("change", async () => {
                if (fileInput.files?.length) {
                    await loadFile(fileInput.files[0]);
                }
            });

            const originalOnDragOver = node.onDragOver;
            node.onDragOver = function (event) {
                if (event.dataTransfer?.types?.includes("Files")) {
                    return true;
                }
                return originalOnDragOver?.call(this, event) ?? false;
            };

            const originalOnDragDrop = node.onDragDrop;
            node.onDragDrop = async function (event) {
                const file = event.dataTransfer?.files?.[0];
                if (file) {
                    return loadFile(file);
                }
                return originalOnDragDrop?.call(this, event) ?? false;
            };

            const originalOnRemoved = node.onRemoved;
            node.onRemoved = function (...removedArgs) {
                fileInput.remove();
                return originalOnRemoved?.apply(this, removedArgs);
            };

            refreshButton();
            const size = node.computeSize?.();
            if (Array.isArray(size)) {
                node.setSize([Math.max(340, size[0]), Math.max(110, size[1])]);
            }
            return result;
        };
    },
});
