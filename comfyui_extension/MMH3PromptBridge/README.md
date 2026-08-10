# MMH3 Prompt Bridge - Phase 1A.1

MMH3 Prompt Bridge sends text to one explicitly selected STRING widget in the
workflow currently open in a ComfyUI browser tab. It never queues or executes
the workflow.

The same versioned protocol supports local and remote ComfyUI deployments.
All state owned by the bridge is kept inside the `MMH3PromptBridge` extension
folder.

## Security model

The bridge generates a cryptographically secure Bearer token on first startup.
The token contains at least 256 bits of randomness and is stored at:

```text
<ComfyUI>/custom_nodes/MMH3PromptBridge/data/bridge.json
```

The complete token is not printed in normal startup output and is never
returned by the unauthenticated status endpoint. Prompt text and tokens are not
logged.

These endpoints require `Authorization: Bearer <bridge-token>`:

- `POST /mmh3-bridge/v1/register`
- `POST /mmh3-bridge/v1/send`
- `POST /mmh3-bridge/v1/ack`

`GET /mmh3-bridge/v1/status` is unauthenticated, but returns only protocol,
limit, authentication, and boolean target/connection status. It does not
return the token, prompt text, browser session ID, node ID, widget name, graph
ID, or other target details.

Wildcard CORS is not enabled. Browser extension requests remain same-origin
with ComfyUI. The MMH3 desktop application does not need CORS.

## Installation

1. Stop ComfyUI.
2. Copy the complete `MMH3PromptBridge` folder into ComfyUI's `custom_nodes`
   directory so the resulting layout is:

   ```text
   <ComfyUI>/custom_nodes/MMH3PromptBridge/__init__.py
   <ComfyUI>/custom_nodes/MMH3PromptBridge/js/mmh3_bridge.js
   ```

   For Windows portable ComfyUI, the usual destination is:

   ```text
   <ComfyUI_windows_portable>/ComfyUI/custom_nodes/MMH3PromptBridge/
   ```

3. Start ComfyUI. The startup notice confirms that Bearer authentication is
   enabled and shows the bridge-local token file path, but not the token.
4. Reload the ComfyUI browser page.

No additional pip packages are required. The bridge uses Python standard
library modules, `aiohttp`, and frontend APIs already supplied by ComfyUI.

## Copying and entering the pairing token

There is intentionally no unauthenticated HTTP endpoint that reveals the
token. A browser-only token retrieval endpoint cannot reliably distinguish a
real ComfyUI user from an arbitrary remote HTTP/WebSocket client, so this
prototype uses deliberate filesystem access instead.

On a local Windows ComfyUI host, copy the token to the clipboard without
printing it with:

```powershell
$bridgeConfig = Get-Content -LiteralPath "<ComfyUI>\custom_nodes\MMH3PromptBridge\data\bridge.json" -Raw | ConvertFrom-Json
$bridgeConfig.token | Set-Clipboard
```

For a remote host, retrieve the same file through the provider's authenticated
file manager, console, or SSH connection. Treat it as a password and transfer
it only over a trusted encrypted channel.

In ComfyUI:

1. Right-click any node.
2. Choose **MMH3 Prompt Bridge -> Set Pairing Token**.
3. Paste the token and confirm.

The frontend keeps the token in JavaScript memory only. It is not written to
browser local storage or ComfyUI user settings. Reloading or closing the tab
requires pairing again.

## Selecting a target

1. Pair the browser tab as described above.
2. Open a workflow in ComfyUI.
3. Right-click a node with an editable STRING input widget.
4. Choose **MMH3 Prompt Bridge -> Set as MMH3 Target -> `<widget name>`**.

The selected widget label is marked with `MMH3` where practical. Target
selection never changes its current text.

Only one target is active per ComfyUI server. The last browser tab that
explicitly selects a target replaces the previous target. Selection records
the browser session ID, node ID, widget name, node type, graph ID, and
registration time in server memory only. It is not persisted to disk and
disappears when ComfyUI restarts.

Numeric, boolean, combo, image, and converted-to-input widgets are not offered.

## Deployment modes

### Local

Example base URL:

```text
http://127.0.0.1:8188
```

HTTP is acceptable for a loopback-only ComfyUI. Bearer authentication remains
required. Do not bind ComfyUI to an untrusted network merely to use the bridge.

### Remote / cloud

Example base URL:

```text
https://remote-comfy.example.com
```

Remote mode should use HTTPS by default. MMH3 Prompt Bridge does not implement
TLS itself. TLS may be terminated by the cloud provider or a reverse proxy.

The proxy must:

- preserve the `Authorization` header;
- forward ComfyUI WebSocket upgrades and keep sessions stable;
- route `/mmh3-bridge/v1/*` for desktop clients;
- route `/api/mmh3-bridge/v1/*` for the ComfyUI frontend;
- avoid wildcard CORS and unauthenticated public bypass routes;
- apply request body and timeout limits compatible with the bridge.

The implementation does not assume a hostname, port, GPU provider, or TLS
termination product.

## Status check

Local example:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8188/mmh3-bridge/v1/status"
```

Remote example:

```powershell
Invoke-RestMethod -Uri "https://remote-comfy.example.com/mmh3-bridge/v1/status"
```

## Manual authenticated send test

After pairing the tab and selecting a target:

```powershell
$comfyBaseUrl = "http://127.0.0.1:8188"
$bridgeConfig = Get-Content -LiteralPath "<ComfyUI>\custom_nodes\MMH3PromptBridge\data\bridge.json" -Raw | ConvertFrom-Json
$headers = @{ Authorization = "Bearer $($bridgeConfig.token)" }
$body = @{
    text = "MMH3 Bridge Test"
    request_id = [guid]::NewGuid().ToString()
} | ConvertTo-Json -Compress

Invoke-RestMethod `
    -Uri "$comfyBaseUrl/mmh3-bridge/v1/send" `
    -Method Post `
    -Headers $headers `
    -ContentType "application/json" `
    -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

For a remote test, change only `$comfyBaseUrl` to the HTTPS URL. A successful
response has `ok: true` and `status: success`, returned only after the selected
browser tab confirms that it updated the widget.

## API and request safety

- Namespace: `/mmh3-bridge/v1/`
- JSON request limit: 256 KiB
- UTF-8 text limit: 128 KiB
- ACK timeout: 3 seconds
- Recent duplicate `request_id` rejection: 60 seconds, bounded to 1024 IDs
- Basic protected-request rate limit: 120 requests per 10 seconds per direct
  peer, bounded to 512 peers
- JSON-only POST requests
- Targeted browser-session WebSocket delivery; no broadcast
- No `/prompt` calls and no automatic queue execution

Behind a reverse proxy, multiple clients may share the proxy's direct-peer
rate-limit bucket. Do not trust or inject arbitrary forwarded client IP headers
without a separately reviewed proxy policy.

## Uninstalling

1. Stop ComfyUI.
2. Delete `<ComfyUI>/custom_nodes/MMH3PromptBridge/`.
3. Start ComfyUI again and reload the browser page.

Deleting this folder removes the bridge token and all bridge-owned persistent
data. Deleting the MMH3 Prompt Builder application folder does not remove this
separately installed ComfyUI extension.

## Phase 1A.1 limitations

- A recent frontend with `getNodeMenuItems` and
  `app.extensionManager.dialog.prompt` is required.
- The token-entry dialog behavior must be verified against the exact frontend
  version in use.
- Reverse-proxy header and WebSocket behavior requires deployment-specific
  testing.
- No target persistence across ComfyUI restarts.
- Custom text widgets must declare a ComfyUI `STRING` input or expose a
  recognized text-widget type and string value.
