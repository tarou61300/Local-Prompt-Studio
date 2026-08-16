import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const API_PREFIX = "/mmh3-bridge/v1";
const BROWSER_CAPABILITY_EVENT = "mmh3.bridge.browser_capability";
const PAIR_REQUEST_EVENT = "mmh3.bridge.pair_request";
const PAIR_RESOLVED_EVENT = "mmh3.bridge.pair_resolved";
const SET_TEXT_EVENT = "mmh3.bridge.set_text";
const TARGET_CHANGED_EVENT = "mmh3.bridge.target_changed";
const EXTENSION_NAME = "mmh3.prompt.bridge";
const TEXT_WIDGET_TYPES = new Set(["text", "string", "customtext"]);
const HELLO_TIMEOUT_MS = 5000;

const stringInputsByNodeType = new Map();
const helloWaiters = new Map();
const pendingPairRequests = new Map();

let currentGraphId = createGraphId();
let activeTarget = null;
let browserCapability = "";
let capabilitySessionId = "";
let helloInFlight = null;
let activePairDialog = null;

function randomBase64Url(byteCount = 32) {
    const cryptoApi = globalThis.crypto;
    if (!cryptoApi?.getRandomValues) {
        throw new Error("Secure browser randomness is unavailable.");
    }
    const bytes = new Uint8Array(byteCount);
    cryptoApi.getRandomValues(bytes);
    let binary = "";
    for (const value of bytes) {
        binary += String.fromCharCode(value);
    }
    return globalThis.btoa(binary)
        .replaceAll("+", "-")
        .replaceAll("/", "_")
        .replace(/=+$/u, "");
}

function createGraphId() {
    if (globalThis.crypto?.randomUUID) {
        return globalThis.crypto.randomUUID();
    }
    try {
        return "mmh3-" + randomBase64Url(16);
    } catch {
        return "mmh3-graph-" + Date.now();
    }
}

function notify(severity, summary, detail) {
    const toast = app.extensionManager?.toast;
    if (toast?.add) {
        toast.add({ severity, summary, detail, life: 5000 });
        return;
    }
    const logger = severity === "error" ? console.error : console.info;
    logger("[" + summary + "] " + detail);
}

function nodeTypeName(node) {
    return String(
        node?.comfyClass
        ?? node?.type
        ?? node?.constructor?.comfyClass
        ?? "",
    );
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
    return type.includes("converted")
        || type === "hidden"
        || widget?.options?.forceInput === true;
}

function isStringWidget(node, widget) {
    if (
        !widget
        || typeof widget.name !== "string"
        || isConvertedWidget(widget)
    ) {
        return false;
    }
    const declaredNames = stringInputsByNodeType.get(nodeTypeName(node));
    if (declaredNames?.has(widget.name)) {
        return typeof widget.value === "string";
    }
    const widgetType = String(widget.type ?? "").toLowerCase();
    return TEXT_WIDGET_TYPES.has(widgetType)
        && typeof widget.value === "string";
}

function eligibleWidgets(node) {
    return (node?.widgets ?? []).filter(
        (widget) => isStringWidget(node, widget),
    );
}

function sameTarget(node, widget, target) {
    return String(node?.id) === String(target?.node_id)
        && widget?.name === target?.widget_name
        && nodeTypeName(node) === target?.node_type
        && currentGraphId === target?.graph_id;
}

function clearTargetIndication() {
    activeTarget = null;
    app.canvas?.setDirty?.(true, true);
}

function findNode(nodeId) {
    const direct = app.graph?.getNodeById?.(nodeId);
    if (direct) {
        return direct;
    }
    return (app.graph?._nodes ?? []).find(
        (node) => String(node.id) === String(nodeId),
    ) ?? null;
}

async function readBridgeResponse(response) {
    let result;
    try {
        result = await response.json();
    } catch {
        throw new Error(
            "Bridge returned HTTP "
            + response.status
            + " without JSON.",
        );
    }
    if (!response.ok || !result?.ok) {
        const error = new Error(
            result?.error?.message
            ?? ("Bridge returned HTTP " + response.status + "."),
        );
        error.httpStatus = response.status;
        error.bridgeCode = result?.status;
        throw error;
    }
    return result;
}

async function postJson(path, payload, authorization = "") {
    const headers = { "Content-Type": "application/json" };
    if (authorization) {
        headers.Authorization = "Bearer " + authorization;
    }
    const response = await api.fetchApi(path, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
    });
    return readBridgeResponse(response);
}

function cancelHelloWaiter(nonce) {
    const waiter = helloWaiters.get(nonce);
    if (!waiter) {
        return;
    }
    helloWaiters.delete(nonce);
    globalThis.clearTimeout(waiter.timeoutId);
    waiter.resolve(false);
}

function cancelAllHelloWaiters() {
    for (const nonce of [...helloWaiters.keys()]) {
        cancelHelloWaiter(nonce);
    }
}

function handleBrowserCapability(event) {
    const message = event.detail ?? {};
    const nonce = message.hello_nonce;
    const capability = message.capability;
    const waiter = helloWaiters.get(nonce);
    if (
        !waiter
        || typeof capability !== "string"
        || !capability
        || api.clientId !== waiter.sessionId
    ) {
        return;
    }
    helloWaiters.delete(nonce);
    globalThis.clearTimeout(waiter.timeoutId);
    browserCapability = capability;
    capabilitySessionId = waiter.sessionId;
    waiter.resolve(true);
}

async function performBrowserHello() {
    const sessionId = api.clientId;
    if (!sessionId) {
        return false;
    }
    if (browserCapability && capabilitySessionId === sessionId) {
        return true;
    }
    if (helloInFlight) {
        return helloInFlight;
    }
    const operation = (async () => {
        const nonce = randomBase64Url(32);
        const capabilityPromise = new Promise((resolve, reject) => {
            const timeoutId = globalThis.setTimeout(() => {
                helloWaiters.delete(nonce);
                reject(
                    new Error(
                        "Timed out waiting for the browser capability.",
                    ),
                );
            }, HELLO_TIMEOUT_MS);
            helloWaiters.set(nonce, {
                resolve,
                reject,
                timeoutId,
                sessionId,
            });
        });
        try {
            await postJson(API_PREFIX + "/browser/hello", {
                session_id: sessionId,
                hello_nonce: nonce,
            });
            return await capabilityPromise;
        } catch (error) {
            cancelHelloWaiter(nonce);
            throw error;
        }
    })();
    helloInFlight = operation;
    try {
        return await operation;
    } catch (error) {
        notify(
            "error",
            "MMH3 Prompt Bridge",
            error?.message ?? "Browser-session pairing failed.",
        );
        return false;
    } finally {
        if (helloInFlight === operation) {
            helloInFlight = null;
        }
    }
}

async function ensureBrowserCapability() {
    if (
        browserCapability
        && capabilitySessionId
        && capabilitySessionId === api.clientId
    ) {
        return true;
    }
    return performBrowserHello();
}

function clearBrowserCapability() {
    browserCapability = "";
    capabilitySessionId = "";
}

async function postBrowserJson(path, payload) {
    if (!(await ensureBrowserCapability())) {
        throw new Error(
            "The ComfyUI browser session is not ready for MMH3 Bridge.",
        );
    }
    const capability = browserCapability;
    try {
        return await postJson(path, payload, capability);
    } catch (error) {
        if (error?.httpStatus === 401) {
            clearBrowserCapability();
            clearTargetIndication();
        }
        throw error;
    }
}

function createTextElement(tagName, text, className = "") {
    const element = document.createElement(tagName);
    element.textContent = String(text);
    if (className) {
        element.className = className;
    }
    return element;
}

function removeActivePairDialog() {
    if (!activePairDialog) {
        return;
    }
    globalThis.clearTimeout(activePairDialog.timeoutId);
    const dialog = activePairDialog.dialog;
    if (dialog.open) {
        dialog.close();
    }
    dialog.remove();
    activePairDialog = null;
}

function closePairDialog(pairId) {
    pendingPairRequests.delete(pairId);
    if (activePairDialog?.pairId === pairId) {
        removeActivePairDialog();
        showNextPairDialog();
    }
}

function closeAllPairDialogs() {
    pendingPairRequests.clear();
    removeActivePairDialog();
}

async function submitPairDecision(pairId, decision, controls) {
    for (const control of controls) {
        control.disabled = true;
    }
    try {
        await postBrowserJson(API_PREFIX + "/pair/decision", {
            pair_id: pairId,
            decision,
        });
    } catch (error) {
        if (error?.bridgeCode !== "pairing_already_resolved") {
            notify(
                "error",
                "MMH3 Prompt Bridge",
                error?.message ?? "Pairing decision failed.",
            );
        }
    } finally {
        closePairDialog(pairId);
    }
}

function showNextPairDialog() {
    if (activePairDialog || pendingPairRequests.size === 0) {
        return;
    }
    const [pairId, request] = pendingPairRequests.entries().next().value;
    const dialog = document.createElement("dialog");
    dialog.className = "mmh3-bridge-pair-dialog";
    dialog.style.maxWidth = "30rem";
    dialog.style.padding = "1.25rem";
    dialog.style.borderRadius = "0.75rem";
    dialog.style.border = "1px solid var(--border-color, #666)";
    dialog.style.background = "var(--comfy-menu-bg, #222)";
    dialog.style.color = "var(--input-text, #fff)";

    const title = createTextElement(
        "h2",
        "Pair with Local Prompt Studio",
    );
    title.style.marginTop = "0";
    dialog.append(title);
    const clientLabel = createTextElement(
        "p",
        "Client requesting access:",
    );
    const clientName = createTextElement(
        "strong",
        request.client_name,
    );
    clientLabel.append(document.createElement("br"), clientName);
    dialog.append(clientLabel);
    dialog.append(
        createTextElement(
            "p",
            "Verify that this code matches the code shown in MMH3:",
        ),
    );
    const code = createTextElement(
        "div",
        request.verification_code,
    );
    code.style.fontSize = "2rem";
    code.style.fontWeight = "700";
    code.style.letterSpacing = "0.2em";
    code.style.textAlign = "center";
    code.style.margin = "1rem 0";
    dialog.append(code);
    dialog.append(
        createTextElement(
            "p",
            "Allow only if you started this pairing request "
            + "and the codes match.",
        ),
    );

    const actions = document.createElement("div");
    actions.style.display = "flex";
    actions.style.justifyContent = "flex-end";
    actions.style.gap = "0.75rem";
    const rejectButton = createTextElement("button", "Reject");
    const allowButton = createTextElement("button", "Allow");
    allowButton.className = "p-button";
    actions.append(rejectButton, allowButton);
    dialog.append(actions);
    const controls = [rejectButton, allowButton];
    rejectButton.addEventListener("click", () => {
        void submitPairDecision(pairId, "reject", controls);
    });
    allowButton.addEventListener("click", () => {
        void submitPairDecision(pairId, "allow", controls);
    });
    dialog.addEventListener("cancel", (event) => {
        event.preventDefault();
    });

    document.body.append(dialog);
    const expiresMs = Math.max(
        1,
        Math.min(60, Number(request.expires_in) || 60),
    ) * 1000;
    const timeoutId = globalThis.setTimeout(() => {
        closePairDialog(pairId);
    }, expiresMs);
    activePairDialog = { pairId, dialog, timeoutId };
    if (typeof dialog.showModal === "function") {
        dialog.showModal();
    } else {
        removeActivePairDialog();
        pendingPairRequests.delete(pairId);
        notify(
            "error",
            "MMH3 Prompt Bridge",
            "This frontend does not support the required pairing dialog.",
        );
    }
}

function handlePairRequest(event) {
    const message = event.detail ?? {};
    if (
        typeof message.pair_id !== "string"
        || typeof message.verification_code !== "string"
        || !/^\d{6}$/u.test(message.verification_code)
        || typeof message.client_name !== "string"
    ) {
        return;
    }
    pendingPairRequests.set(message.pair_id, {
        pair_id: message.pair_id,
        verification_code: message.verification_code,
        client_name: message.client_name,
        expires_in: message.expires_in,
    });
    showNextPairDialog();
}

function handlePairResolved(event) {
    const message = event.detail ?? {};
    if (typeof message.pair_id === "string") {
        closePairDialog(message.pair_id);
    }
}

async function registerTarget(node, widget) {
    try {
        const result = await postBrowserJson(
            API_PREFIX + "/register",
            {
                node_id: String(node.id),
                widget_name: widget.name,
                node_type: nodeTypeName(node),
                graph_id: currentGraphId,
            },
        );
        activeTarget = result.target;
        app.canvas?.setDirty?.(true, true);
        notify(
            "success",
            "MMH3 Prompt Bridge",
            "Target set to "
            + (node.title ?? nodeTypeName(node))
            + " → "
            + widget.name
            + ".",
        );
    } catch (error) {
        notify(
            "error",
            "MMH3 Prompt Bridge",
            error?.message ?? "Target registration failed.",
        );
    }
}

async function sendAck(deliveryId, status) {
    if (!deliveryId) {
        return;
    }
    await postBrowserJson(API_PREFIX + "/ack", {
        delivery_id: deliveryId,
        status,
        detail: "",
    });
}

async function applyTextEvent(event) {
    const message = event.detail ?? {};
    const deliveryId = message.delivery_id;
    const target = message.target ?? {};
    let status = "internal_error";
    if (!browserCapability) {
        return;
    }
    try {
        if (!activeTarget) {
            status = "stale_session";
        } else if (
            !deliveryId
            || target.graph_id !== currentGraphId
            || target.node_id !== activeTarget.node_id
            || target.widget_name !== activeTarget.widget_name
            || target.node_type !== activeTarget.node_type
            || target.graph_id !== activeTarget.graph_id
        ) {
            status = "stale_session";
        } else {
            const node = findNode(target.node_id);
            if (!node || nodeTypeName(node) !== target.node_type) {
                status = "target_not_found";
            } else {
                const widget = (node.widgets ?? []).find(
                    (item) => item.name === target.widget_name,
                );
                if (!widget) {
                    status = "widget_not_found";
                } else if (!isStringWidget(node, widget)) {
                    status = "invalid_widget";
                } else {
                    widget.value = message.text;
                    const inputElement = (
                        widget.inputEl
                        ?? widget.element
                        ?? null
                    );
                    if (inputElement && "value" in inputElement) {
                        inputElement.value = message.text;
                    }
                    if (typeof widget.callback === "function") {
                        await Promise.resolve(
                            widget.callback(
                                message.text,
                                app.canvas,
                                node,
                                app.canvas?.graph_mouse,
                            ),
                        );
                    }
                    app.graph?.change?.();
                    node.setDirtyCanvas?.(true, true);
                    app.graph?.setDirtyCanvas?.(true, true);
                    app.canvas?.setDirty?.(true, true);
                    status = "success";
                }
            }
        }
    } catch {
        status = "internal_error";
    }
    try {
        await sendAck(deliveryId, status);
        if (status === "success") {
            notify(
                "success",
                "MMH3 Prompt Bridge",
                "Text was applied to the selected widget.",
            );
        }
    } catch (error) {
        notify(
            "error",
            "MMH3 Prompt Bridge",
            error?.message ?? "ACK delivery failed.",
        );
    }
}

function handleTargetChanged(event) {
    const message = event.detail ?? {};
    const target = message.target ?? {};
    if (!message.active) {
        if (
            activeTarget
            && activeTarget.node_id === target.node_id
            && activeTarget.widget_name === target.widget_name
            && activeTarget.graph_id === target.graph_id
        ) {
            clearTargetIndication();
        }
        return;
    }
    if (target.graph_id === currentGraphId) {
        activeTarget = target;
        app.canvas?.setDirty?.(true, true);
    }
}

function handleReconnecting() {
    cancelAllHelloWaiters();
    helloInFlight = null;
    clearBrowserCapability();
    clearTargetIndication();
    closeAllPairDialogs();
}

function handleReconnected() {
    void performBrowserHello();
}

function handleStatus() {
    if (!browserCapability && !helloInFlight && api.clientId) {
        void performBrowserHello();
    }
}

api.addEventListener(
    BROWSER_CAPABILITY_EVENT,
    handleBrowserCapability,
);
api.addEventListener(PAIR_REQUEST_EVENT, handlePairRequest);
api.addEventListener(PAIR_RESOLVED_EVENT, handlePairResolved);
api.addEventListener(SET_TEXT_EVENT, applyTextEvent);
api.addEventListener(TARGET_CHANGED_EVENT, handleTargetChanged);
api.addEventListener("reconnecting", handleReconnecting);
api.addEventListener("reconnected", handleReconnected);
api.addEventListener("status", handleStatus);

app.registerExtension({
    name: EXTENSION_NAME,

    setup() {
        void ensureBrowserCapability();
    },

    beforeRegisterNodeDef(nodeType, nodeData) {
        const typeName = String(
            nodeData?.name
            ?? nodeType?.comfyClass
            ?? "",
        );
        if (typeName) {
            stringInputsByNodeType.set(
                typeName,
                collectStringInputs(nodeData),
            );
        }
    },

    afterConfigureGraph() {
        currentGraphId = createGraphId();
        clearTargetIndication();
    },

    getNodeMenuItems(node) {
        const widgets = eligibleWidgets(node);
        if (widgets.length === 0) {
            return [];
        }
        return [
            {
                content: "MMH3 Prompt Bridge",
                submenu: {
                    options: [
                        {
                            content: "Set as MMH3 Target",
                            submenu: {
                                options: widgets.map((widget) => ({
                                    content: sameTarget(
                                        node,
                                        widget,
                                        activeTarget,
                                    )
                                        ? "✓ " + widget.name
                                        : widget.name,
                                    callback: () => {
                                        void registerTarget(node, widget);
                                    },
                                })),
                            },
                        },
                    ],
                },
            },
        ];
    },
});
