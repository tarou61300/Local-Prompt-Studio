import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";


const API_PREFIX = "/mmh3-bridge/v1";
const SET_TEXT_EVENT = "mmh3.bridge.set_text";
const TARGET_CHANGED_EVENT = "mmh3.bridge.target_changed";
const EXTENSION_NAME = "mmh3.prompt.bridge";
const TEXT_WIDGET_TYPES = new Set(["text", "string", "customtext"]);

const stringInputsByNodeType = new Map();
let currentGraphId = createId();
let activeVisual = null;
let pairingToken = "";


function createId() {
    if (globalThis.crypto?.randomUUID) {
        return globalThis.crypto.randomUUID();
    }
    return `mmh3-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}


function notify(severity, summary, detail) {
    const toast = app.extensionManager?.toast;
    if (toast?.add) {
        toast.add({ severity, summary, detail, life: 5000 });
        return;
    }
    const logger = severity === "error" ? console.error : console.info;
    logger(`[${summary}] ${detail}`);
}


function nodeTypeName(node) {
    return String(node?.comfyClass ?? node?.type ?? node?.constructor?.comfyClass ?? "");
}


function inputType(definition) {
    if (Array.isArray(definition)) {
        return definition[0];
    }
    if (definition && typeof definition === "object") {
        return definition.type ?? definition[0];
    }
    return undefined;
}


function collectStringInputs(nodeData) {
    const names = new Set();
    for (const groupName of ["required", "optional"]) {
        const group = nodeData?.input?.[groupName];
        if (!group || typeof group !== "object") {
            continue;
        }
        for (const [name, definition] of Object.entries(group)) {
            if (inputType(definition) === "STRING") {
                names.add(name);
            }
        }
    }
    return names;
}


function isConvertedWidget(widget) {
    const type = String(widget?.type ?? "").toLowerCase();
    return type.includes("converted") || type === "hidden" || widget?.options?.forceInput === true;
}


function isStringWidget(node, widget) {
    if (!widget || typeof widget.name !== "string" || isConvertedWidget(widget)) {
        return false;
    }

    const declaredNames = stringInputsByNodeType.get(nodeTypeName(node));
    if (declaredNames?.has(widget.name)) {
        return typeof widget.value === "string";
    }

    const widgetType = String(widget.type ?? "").toLowerCase();
    return TEXT_WIDGET_TYPES.has(widgetType) && typeof widget.value === "string";
}


function eligibleWidgets(node) {
    return (node?.widgets ?? []).filter((widget) => isStringWidget(node, widget));
}


function sameTarget(node, widget, target) {
    return String(node?.id) === String(target?.node_id)
        && widget?.name === target?.widget_name
        && nodeTypeName(node) === target?.node_type
        && currentGraphId === target?.graph_id;
}


function clearVisual() {
    if (!activeVisual) {
        return;
    }
    const { node, widget, originalLabel, inputElement, originalOutline } = activeVisual;
    if (widget) {
        widget.label = originalLabel;
    }
    if (inputElement?.style) {
        inputElement.style.outline = originalOutline;
    }
    node?.setDirtyCanvas?.(true, true);
    app.canvas?.setDirty?.(true, true);
    activeVisual = null;
}


function markVisual(node, widget) {
    clearVisual();
    const inputElement = widget.inputEl ?? widget.element ?? null;
    const originalLabel = widget.label;
    const originalOutline = inputElement?.style?.outline ?? "";
    const label = String(originalLabel ?? widget.name);
    widget.label = `★ MMH3: ${label}`;
    if (inputElement?.style) {
        inputElement.style.outline = "2px solid #6aa9ff";
    }
    activeVisual = { node, widget, originalLabel, inputElement, originalOutline };
    node?.setDirtyCanvas?.(true, true);
    app.canvas?.setDirty?.(true, true);
}


function findNode(nodeId) {
    const direct = app.graph?.getNodeById?.(nodeId);
    if (direct) {
        return direct;
    }
    return (app.graph?._nodes ?? []).find((node) => String(node.id) === String(nodeId)) ?? null;
}


async function promptForPairingToken() {
    const dialog = app.extensionManager?.dialog;
    if (typeof dialog?.prompt !== "function") {
        notify(
            "error",
            "MMH3 Prompt Bridge",
            "This ComfyUI frontend does not provide the secure extension dialog API.",
        );
        return false;
    }

    const value = await dialog.prompt({
        title: "MMH3 Prompt Bridge Pairing",
        message: "Paste the token from MMH3PromptBridge/data/bridge.json. It is kept in this tab's memory only.",
        type: "password",
    });
    if (typeof value !== "string" || !value.trim()) {
        return false;
    }
    pairingToken = value.trim();
    notify("success", "MMH3 Prompt Bridge", "Pairing token set for this browser tab.");
    return true;
}


async function ensurePairingToken() {
    return Boolean(pairingToken) || promptForPairingToken();
}


function clearPairingToken() {
    pairingToken = "";
    clearVisual();
    notify("info", "MMH3 Prompt Bridge", "Pairing token cleared from this browser tab.");
}


async function postJson(path, payload) {
    if (!pairingToken) {
        throw new Error("Set the MMH3 Prompt Bridge pairing token first.");
    }
    const response = await api.fetchApi(path, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${pairingToken}`,
        },
        body: JSON.stringify(payload),
    });
    let result;
    try {
        result = await response.json();
    } catch {
        throw new Error(`Bridge returned HTTP ${response.status} without JSON.`);
    }
    if (response.status === 401) {
        pairingToken = "";
    }
    if (!response.ok || !result?.ok) {
        throw new Error(result?.error?.message ?? `Bridge returned HTTP ${response.status}.`);
    }
    return result;
}


function currentSessionId() {
    return api.clientId ?? sessionStorage.getItem("clientId") ?? window.name ?? "";
}


async function registerTarget(node, widget) {
    if (!(await ensurePairingToken())) {
        return;
    }
    const sessionId = currentSessionId();
    if (!sessionId) {
        notify("error", "MMH3 Prompt Bridge", "ComfyUI WebSocket session is not ready.");
        return;
    }

    try {
        await postJson(`${API_PREFIX}/register`, {
            session_id: sessionId,
            node_id: String(node.id),
            widget_name: widget.name,
            node_type: nodeTypeName(node),
            graph_id: currentGraphId,
        });
        markVisual(node, widget);
        notify(
            "success",
            "MMH3 Prompt Bridge",
            `Target set to ${node.title ?? nodeTypeName(node)} → ${widget.name}.`,
        );
    } catch (error) {
        notify("error", "MMH3 Prompt Bridge", error?.message ?? "Target registration failed.");
    }
}


async function sendAck(requestId, status) {
    const sessionId = currentSessionId();
    if (!requestId || !sessionId) {
        return;
    }
    await postJson(`${API_PREFIX}/ack`, {
        request_id: requestId,
        session_id: sessionId,
        status,
        detail: "",
    });
}


async function applyTextEvent(event) {
    const message = event.detail ?? {};
    const requestId = message.request_id;
    const target = message.target ?? {};
    let status = "internal_error";

    if (!pairingToken) {
        notify(
            "error",
            "MMH3 Prompt Bridge",
            "Pairing token is not set in this browser tab. Select the target again to pair.",
        );
        return;
    }

    try {
        if (!requestId || target.session_id !== currentSessionId()) {
            status = "stale_session";
        } else if (target.graph_id !== currentGraphId) {
            status = "target_not_found";
        } else {
            const node = findNode(target.node_id);
            if (!node || nodeTypeName(node) !== target.node_type) {
                status = "target_not_found";
            } else {
                const widget = (node.widgets ?? []).find((item) => item.name === target.widget_name);
                if (!widget) {
                    status = "widget_not_found";
                } else if (!isStringWidget(node, widget)) {
                    status = "invalid_widget";
                } else {
                    widget.value = message.text;
                    const inputElement = widget.inputEl ?? widget.element ?? null;
                    if (inputElement && "value" in inputElement) {
                        inputElement.value = message.text;
                    }
                    if (typeof widget.callback === "function") {
                        await Promise.resolve(
                            widget.callback(message.text, app.canvas, node, app.canvas?.graph_mouse),
                        );
                    }
                    app.graph?.change?.();
                    node.setDirtyCanvas?.(true, true);
                    app.graph?.setDirtyCanvas?.(true, true);
                    app.canvas?.setDirty?.(true, true);
                    markVisual(node, widget);
                    status = "success";
                }
            }
        }
    } catch {
        status = "internal_error";
    }

    try {
        await sendAck(requestId, status);
        if (status === "success") {
            notify("success", "MMH3 Prompt Bridge", "Text was applied to the selected widget.");
        }
    } catch (error) {
        notify("error", "MMH3 Prompt Bridge", error?.message ?? "ACK delivery failed.");
    }
}


function handleTargetChanged(event) {
    const message = event.detail ?? {};
    const target = message.target ?? {};
    if (target.session_id !== currentSessionId()) {
        return;
    }

    if (!message.active) {
        if (activeVisual && sameTarget(activeVisual.node, activeVisual.widget, target)) {
            clearVisual();
        }
        return;
    }

    if (target.graph_id !== currentGraphId) {
        return;
    }
    const node = findNode(target.node_id);
    const widget = (node?.widgets ?? []).find((item) => item.name === target.widget_name);
    if (node && widget && isStringWidget(node, widget)) {
        markVisual(node, widget);
    }
}


api.addEventListener(SET_TEXT_EVENT, applyTextEvent);
api.addEventListener(TARGET_CHANGED_EVENT, handleTargetChanged);


app.registerExtension({
    name: EXTENSION_NAME,

    beforeRegisterNodeDef(nodeType, nodeData) {
        const typeName = String(nodeData?.name ?? nodeType?.comfyClass ?? "");
        if (typeName) {
            stringInputsByNodeType.set(typeName, collectStringInputs(nodeData));
        }
    },

    afterConfigureGraph() {
        currentGraphId = createId();
        clearVisual();
    },

    getNodeMenuItems(node) {
        const widgets = eligibleWidgets(node);
        const options = [
            {
                content: pairingToken ? "Replace Pairing Token" : "Set Pairing Token",
                callback: () => promptForPairingToken(),
            },
        ];
        if (pairingToken) {
            options.push({
                content: "Clear Pairing Token",
                callback: () => clearPairingToken(),
            });
        }
        if (widgets.length > 0) {
            options.push({
                content: "Set as MMH3 Target",
                submenu: {
                    options: widgets.map((widget) => ({
                        content: widget.name,
                        callback: () => registerTarget(node, widget),
                    })),
                },
            });
        }
        return [
            {
                content: "MMH3 Prompt Bridge",
                submenu: {
                    options,
                },
            },
        ];
    },
});
