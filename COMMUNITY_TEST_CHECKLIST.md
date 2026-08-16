# Local Prompt Studio Community Test Checklist

Thank you for testing this pre-release. It is intended to validate the optional
ComfyUI integration across real Windows ComfyUI installations.

Local Prompt Studio continues to work without ComfyUI. Sending text does not queue
or start a ComfyUI workflow.

## Environment to report

- Windows version
- ComfyUI installation type: portable / desktop / manual / other
- ComfyUI version, if known
- Browser and version, if known
- Local or remote ComfyUI
- Local Prompt Studio backend used: CPU or Vulkan

## Tests

- [ ] `MMH3PromptBridge` loads without a ComfyUI startup error.
- [ ] Test Connection detects MMH3 Prompt Bridge v1.2.
- [ ] Pair with ComfyUI shows a six-digit code in both applications.
- [ ] The codes match and Allow succeeds.
- [ ] Local Prompt Studio shows Paired.
- [ ] The MMH3 Prompt Bridge target menu appears on a STRING/multiline STRING node.
- [ ] A target widget can be selected.
- [ ] Send to ComfyUI changes only the selected target text.
- [ ] Manually edited Local Prompt Studio output is sent exactly as edited.
- [ ] No ComfyUI generation starts automatically and no workflow is queued.
- [ ] Restarting Local Prompt Studio retains pairing.
- [ ] Browser reload or ComfyUI restart behavior is recorded, including whether target reselection is needed.
- [ ] Any displayed error message is recorded without private prompt content.

## Never post or attach

- Client credentials or tokens
- Authorization headers
- The contents of `LocalPromptStudio/data/comfyui_credentials.dat`
- The contents of `ComfyUI/custom_nodes/MMH3PromptBridge/data/bridge.json`

Ordinary application logs may be shared only after checking that they contain no
private prompt, personal information, model path, credential, token, or header value.
