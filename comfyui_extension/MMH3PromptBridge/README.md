# MMH3 Prompt Bridge v1.2

MMH3 Prompt Bridge delivers text from a paired MMH3 client to one explicitly
selected ordinary STRING or multiline STRING widget in a live ComfyUI
workflow. It does not add a graph node and never queues a workflow.

## Install

1. Stop ComfyUI.
2. Copy the complete MMH3PromptBridge directory into ComfyUI/custom_nodes.
3. Start ComfyUI.
4. Reload the ComfyUI browser frontend.

The installed layout is:

    ComfyUI/custom_nodes/MMH3PromptBridge/__init__.py
    ComfyUI/custom_nodes/MMH3PromptBridge/js/mmh3_bridge.js

No pip package or additional service is required.

## User workflow

1. Enter the ComfyUI URL in a compatible MMH3 client.
2. Start pairing in MMH3.
3. Compare the six-digit code shown by MMH3 with the approval dialog in
   ComfyUI.
4. Select Allow only when the codes match.
5. In ComfyUI, right-click a node and choose:

       MMH3 Prompt Bridge
       -> Set as MMH3 Target
       -> <STRING widget>

6. Send text from MMH3.

The selected widget is updated. ComfyUI generation is not started.

## Pairing protocol

The MMH3 client creates at least 32 random bytes as a private verifier and
sends only SHA-256(verifier) as a base64url challenge.

The bridge creates:

- a random pair ID;
- a six-digit human verification code;
- a 60-second in-memory PENDING pairing;
- a notification for connected ComfyUI frontend sessions.

The code is for visual comparison only and is not an authentication secret.
The first valid Allow or Reject decision wins.

After approval, the MMH3 client submits the pair ID and verifier. The bridge
checks the challenge, atomically saves only the hash of a new client
credential, marks the pairing CONSUMED, and returns the plaintext credential
exactly once. The browser never receives that credential.

Pairing states are:

    PENDING -> APPROVED -> CONSUMED
    PENDING -> REJECTED
    PENDING -> EXPIRED

Pair requests, verification codes, verifiers and decisions are not persisted.

## Browser-session capability

The frontend automatically calls:

    POST /mmh3-bridge/v1/browser/hello

It uses ComfyUI's current api.clientId only to locate the existing WebSocket.
The client ID is not authentication.

The bridge sends a random browser capability only through that exact
WebSocket. The capability:

- contains at least 256 bits of randomness;
- is held only in JavaScript memory;
- is never displayed;
- is not saved to localStorage, sessionStorage, window.name, ComfyUI settings
  or workflow JSON;
- authorizes pair decisions, target registration and ACK;
- becomes unusable when the exact WebSocket is disconnected or replaced.

A repeat hello on the same WebSocket resends the existing capability with the
new hello nonce. A new WebSocket receives a new capability.

The extension does not alter ComfyUI's own api.clientId, window.name or
sessionStorage reconnect behavior.

## Paired-client credential storage

Persistent state is stored only at:

    ComfyUI/custom_nodes/MMH3PromptBridge/data/bridge.json

Schema version 2 stores only:

- client ID;
- plain-text display name;
- SHA-256 credential hash;
- creation time.

Plaintext paired credentials are never stored by the bridge. The file and its
directory use restrictive permissions where supported by the operating
system.

When a Phase 1A.1 schema containing a shared plaintext token is detected, the
bridge atomically replaces it with an empty schema-v2 client list. The old
shared token is not accepted or migrated. Clients must pair again.

Deleting the MMH3PromptBridge custom-node directory removes all
bridge-owned persistent state.

## API

Base prefix:

    /mmh3-bridge/v1

Endpoints:

- GET /status
- POST /browser/hello
- POST /pair/start
- POST /pair/decision
- POST /pair/complete
- POST /register
- POST /send
- POST /ack

There is intentionally no revoke-all endpoint in Phase 1A.2.

Browser-authorized endpoints:

- /pair/decision
- /register
- /ack

Client-authorized endpoint:

- /send

Authorization is sent as a Bearer value in the Authorization header. The
endpoint determines whether the value must be a temporary browser capability
or a persistent paired-client credential.

The unauthenticated status response does not reveal credentials, pairing
challenges, browser capabilities, prompt text, session IDs or target details.

## Exact-socket prompt delivery

Targets are bound to the exact WebSocket object that selected the widget, not
only to a session ID. Before sending prompt text, the bridge proves that the
same object is still the current open ComfyUI socket.

Prompt text is sent directly to that captured socket with the standard
ComfyUI JSON envelope:

    {"type": "mmh3.bridge.set_text", "data": {...}}

If the socket map, exact socket or send_json behavior is unavailable, the
bridge fails closed. It does not fall back to send_sync, broadcast delivery or
another browser session.

The target contains node ID, widget name, node type and an ephemeral graph ID.
It is never written into workflow JSON, ComfyUI settings or bridge.json.
Reloading the frontend or restarting ComfyUI requires target selection again.

## Limits and resource use

- JSON request limit: 256 KiB
- UTF-8 text limit: 128 KiB
- ACK timeout: 3 seconds
- Pairing lifetime: 60 seconds
- Recent request ID rejection: 60 seconds, bounded to 1024 entries
- Browser, pairing and rate-limit maps have fixed maximum sizes
- JSON-only POST bodies

The frontend uses only short-lived one-shot timers: approximately five seconds
while waiting for browser hello and up to approximately 60 seconds while a
pairing dialog is open. There is no repeating idle timer, heartbeat, idle
polling, cleanup thread, background worker or process. Expired state is cleaned
lazily when bridge requests occur. The bridge adds no database or additional
WebSocket server.

## Local and remote deployment

Local examples:

    http://127.0.0.1:8188
    http://localhost:8188

Remote production use should use HTTPS:

    https://remote-comfy.example.com

The bridge does not terminate TLS. A reverse proxy must:

- preserve Authorization headers without logging them;
- forward ComfyUI WebSocket upgrades;
- keep HTTP and WebSocket authentication consistent;
- route both /mmh3-bridge/v1 and /api/mmh3-bridge/v1;
- avoid wildcard CORS and unauthenticated bypasses;
- enforce suitable public rate limits.

The automatic protocol proves control of a live ComfyUI WebSocket. Existing
ComfyUI APIs cannot prove that a connection belongs to a human-operated
official frontend rather than a headless WebSocket client. A remotely exposed
ComfyUI UI and WebSocket must therefore have an independent access-control
boundary. An unauthenticated public ComfyUI WebSocket is not a supported
secure deployment.

Provider authentication can also prevent an external desktop client from
reaching the bridge endpoints. Such proxy/provider behavior must be tested
for each hosted environment.

## Security notes

- Prompt text, verifiers, capabilities and credentials are not logged.
- Pair IDs and all credentials use cryptographic randomness.
- Pair verifier, browser capability and credential hash comparisons use
  constant-time comparison where applicable.
- Pair decisions and completions are protected by per-pair asyncio locks.
- Credential-file writes are serialized and atomic.
- Duplicate completion cannot issue a second credential.
- Prompt events are never broadcast.
- No /prompt API or automatic queue operation is used.
- No graph receiver node is created.

Bearer credentials are replayable if stolen. HTTPS and protected proxy logs
are required for remote use.

## v1.2 compatibility notes

Exact-socket security depends on the current internal ComfyUI
PromptServer.instance.sockets map and aiohttp WebSocket send_json behavior.
The dependency is isolated in small Python helpers and fails closed when the
required behavior is unavailable.

The frontend requires:

- api.clientId;
- custom WebSocket event listeners;
- getNodeMenuItems;
- HTML dialog support;
- secure browser crypto APIs.

Real ComfyUI browser tests are required for each supported frontend/server
combination and for each remote reverse-proxy environment.
