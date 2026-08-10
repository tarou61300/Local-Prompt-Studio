from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import sys
import types
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_INIT = (
    PROJECT_ROOT
    / "comfyui_extension"
    / "MMH3PromptBridge"
    / "__init__.py"
)


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status


class FakeWeb:
    Request = object
    Response = FakeResponse

    @staticmethod
    def json_response(payload, status=200):
        return FakeResponse(payload, status)


class FakeRoutes:
    def __init__(self):
        self.handlers = {}

    def _decorator(self, method, path):
        def register(handler):
            self.handlers[(method, path)] = handler
            return handler

        return register

    def get(self, path):
        return self._decorator("GET", path)

    def post(self, path):
        return self._decorator("POST", path)


class FakeSocket:
    def __init__(self, *, closed=False):
        self.closed = closed


class FakePromptServerInstance:
    def __init__(self):
        self.routes = FakeRoutes()
        self.sockets = {}
        self.sent = []

    def send_sync(self, event, data, sid=None):
        self.sent.append((event, data, sid))


class FakeContent:
    def __init__(self, body):
        self.body = body
        self.offset = 0

    async def read(self, size):
        chunk = self.body[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class FakeRequest:
    def __init__(
        self,
        payload=None,
        *,
        body=None,
        content_type="application/json",
        authorization=None,
        remote="127.0.0.1",
    ):
        if body is None:
            body = json.dumps(payload).encode("utf-8")
        self.headers = {"Content-Type": content_type} if content_type else {}
        if authorization is not None:
            self.headers["Authorization"] = authorization
        self.content_length = len(body)
        self.content = FakeContent(body)
        self.remote = remote


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    prompt_server = FakePromptServerInstance()

    aiohttp_module = types.ModuleType("aiohttp")
    aiohttp_module.web = FakeWeb
    server_module = types.ModuleType("server")
    server_module.PromptServer = type(
        "PromptServer",
        (),
        {"instance": prompt_server},
    )
    monkeypatch.setitem(sys.modules, "aiohttp", aiohttp_module)
    monkeypatch.setitem(sys.modules, "server", server_module)

    package_dir = tmp_path / "MMH3PromptBridge"
    package_dir.mkdir()
    isolated_init = package_dir / "__init__.py"
    shutil.copy2(BRIDGE_INIT, isolated_init)

    module_name = f"mmh3_prompt_bridge_under_test_{tmp_path.name}"
    spec = importlib.util.spec_from_file_location(module_name, isolated_init)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module, prompt_server


def run(coro):
    return asyncio.run(coro)


def authenticated_request(module, payload=None, **kwargs):
    return FakeRequest(
        payload,
        authorization=f"Bearer {module.BRIDGE_TOKEN}",
        **kwargs,
    )


def register_payload(session_id="session-a", node_id="7", widget_name="text"):
    return {
        "session_id": session_id,
        "node_id": node_id,
        "widget_name": widget_name,
        "node_type": "ExampleTextNode",
        "graph_id": "graph-a",
    }


def test_status_with_no_target(bridge):
    module, _server = bridge
    response = run(module.status_route(FakeRequest({})))

    assert response.status == 200
    assert response.payload["ok"] is True
    assert response.payload["target_registered"] is False
    assert response.payload["target_session_connected"] is False


def test_token_is_created_with_at_least_256_bits(bridge):
    module, _server = bridge

    assert module.TOKEN_PATH.exists()
    assert module.TOKEN_PATH.parent == module.EXTENSION_DIR / "data"
    assert module._token_is_valid(module.BRIDGE_TOKEN) is True
    payload = json.loads(module.TOKEN_PATH.read_text(encoding="utf-8"))
    assert payload["version"] == module.TOKEN_FILE_VERSION
    assert payload["token"] == module.BRIDGE_TOKEN


def test_token_persists_across_bridge_restart(bridge):
    module, _server = bridge

    restarted_token = module.TokenStore(module.TOKEN_PATH).load_or_create()

    assert restarted_token == module.BRIDGE_TOKEN


def test_malformed_token_file_is_recovered(bridge):
    module, _server = bridge
    original_token = module.BRIDGE_TOKEN
    module.TOKEN_PATH.write_text('{"version":1,"token":"invalid"}', encoding="utf-8")

    recovered_token = module.TokenStore(module.TOKEN_PATH).load_or_create()
    persisted_token = module.TokenStore(module.TOKEN_PATH).load_or_create()

    assert recovered_token != original_token
    assert module._token_is_valid(recovered_token) is True
    assert persisted_token == recovered_token


def test_token_and_prompt_are_not_logged(bridge, capsys):
    module, _server = bridge
    startup_output = capsys.readouterr()
    assert module.BRIDGE_TOKEN not in startup_output.out
    assert module.BRIDGE_TOKEN not in startup_output.err

    prompt = "MMH3 confidential prompt marker"
    response = run(
        module.send_route(
            authenticated_request(
                module,
                {"text": prompt, "request_id": "log-test-request"},
            )
        )
    )
    route_output = capsys.readouterr()

    assert response.status == 409
    assert prompt not in route_output.out
    assert prompt not in route_output.err


def test_send_without_authorization_header(bridge):
    module, _server = bridge

    response = run(
        module.send_route(
            FakeRequest({"text": "MMH3 Bridge Test", "request_id": "auth-1"})
        )
    )

    assert response.status == 401
    assert response.payload["status"] == "unauthorized"


def test_send_with_invalid_token(bridge):
    module, _server = bridge

    response = run(
        module.send_route(
            FakeRequest(
                {"text": "MMH3 Bridge Test", "request_id": "auth-2"},
                authorization="Bearer invalid-token",
            )
        )
    )

    assert response.status == 401
    assert response.payload["status"] == "unauthorized"


def test_send_with_valid_token_passes_authentication(bridge):
    module, _server = bridge

    response = run(
        module.send_route(
            authenticated_request(
                module,
                {"text": "MMH3 Bridge Test", "request_id": "auth-3"},
            )
        )
    )

    assert response.status == 409
    assert response.payload["status"] == "no_target"


def test_register_and_ack_are_also_protected(bridge):
    module, server = bridge
    server.sockets["session-a"] = FakeSocket()

    register_response = run(
        module.register_route(FakeRequest(register_payload()))
    )
    ack_response = run(
        module.ack_route(
            FakeRequest(
                {
                    "request_id": "auth-ack",
                    "session_id": "session-a",
                    "status": "success",
                }
            )
        )
    )

    assert register_response.status == 401
    assert ack_response.status == 401


def test_register_target(bridge):
    module, server = bridge
    server.sockets["session-a"] = FakeSocket()

    response = run(
        module.register_route(authenticated_request(module, register_payload()))
    )

    assert response.status == 200
    assert response.payload["status"] == "registered"
    assert module.BRIDGE.active_target.widget_name == "text"
    assert response.payload["target"].get("session_id") is None


def test_unauthenticated_status_does_not_leak_token_or_target_details(bridge):
    module, server = bridge
    server.sockets["session-a"] = FakeSocket()
    private_target = register_payload(
        node_id="private-node-id",
        widget_name="private-widget-name",
    )
    run(
        module.register_route(authenticated_request(module, private_target))
    )

    response = run(module.status_route(FakeRequest({})))
    serialized = json.dumps(response.payload)

    assert response.status == 200
    assert response.payload["target_registered"] is True
    assert response.payload["target_session_connected"] is True
    assert "target" not in response.payload
    assert module.BRIDGE_TOKEN not in serialized
    assert "private-node-id" not in serialized
    assert "private-widget-name" not in serialized


def test_register_replaces_the_single_active_target(bridge):
    module, server = bridge
    server.sockets["session-a"] = FakeSocket()
    server.sockets["session-b"] = FakeSocket()

    first = run(
        module.register_route(authenticated_request(module, register_payload()))
    )
    second = run(
        module.register_route(
            authenticated_request(
                module,
                register_payload(
                    session_id="session-b",
                    node_id="9",
                    widget_name="prompt",
                )
            )
        )
    )

    assert first.payload["replaced_existing_target"] is False
    assert second.payload["replaced_existing_target"] is True
    assert module.BRIDGE.active_target.session_id == "session-b"
    assert module.BRIDGE.active_target.node_id == "9"
    assert server.sent[-2][2] == "session-a"
    assert server.sent[-1][2] == "session-b"


def test_send_with_no_target(bridge):
    module, _server = bridge
    response = run(
        module.send_route(
            authenticated_request(
                module,
                {"text": "MMH3 Bridge Test", "request_id": "request-1"},
            )
        )
    )

    assert response.status == 409
    assert response.payload["status"] == "no_target"


def test_send_rejects_stale_session(bridge):
    module, server = bridge
    server.sockets["session-a"] = FakeSocket()
    run(module.register_route(authenticated_request(module, register_payload())))
    del server.sockets["session-a"]

    response = run(
        module.send_route(
            authenticated_request(
                module,
                {"text": "MMH3 Bridge Test", "request_id": "request-2"},
            )
        )
    )

    assert response.status == 410
    assert response.payload["status"] == "stale_session"
    assert module.BRIDGE.active_target is None


def test_send_returns_success_only_after_matching_ack(bridge):
    module, server = bridge
    server.sockets["session-a"] = FakeSocket()
    run(module.register_route(authenticated_request(module, register_payload())))

    async def scenario():
        send_task = asyncio.create_task(
            module.send_route(
                authenticated_request(
                    module,
                    {"text": "MMH3 Bridge Test", "request_id": "request-3"},
                )
            )
        )
        await asyncio.sleep(0)
        event, event_payload, sid = server.sent[-1]
        assert event == module.SET_TEXT_EVENT
        assert sid == "session-a"
        assert event_payload["text"] == "MMH3 Bridge Test"

        ack_response = await module.ack_route(
            authenticated_request(
                module,
                {
                    "request_id": "request-3",
                    "session_id": "session-a",
                    "status": "success",
                }
            )
        )
        send_response = await send_task
        return ack_response, send_response

    ack_response, send_response = run(scenario())
    assert ack_response.status == 200
    assert ack_response.payload["status"] == "acknowledged"
    assert send_response.status == 200
    assert send_response.payload["status"] == "success"


def test_unrelated_session_cannot_acknowledge_send(bridge):
    module, server = bridge
    server.sockets["session-a"] = FakeSocket()
    server.sockets["session-b"] = FakeSocket()
    run(module.register_route(authenticated_request(module, register_payload())))

    async def scenario():
        send_task = asyncio.create_task(
            module.send_route(
                authenticated_request(
                    module,
                    {"text": "MMH3 Bridge Test", "request_id": "request-session"},
                )
            )
        )
        await asyncio.sleep(0)
        unrelated_ack = await module.ack_route(
            authenticated_request(
                module,
                {
                    "request_id": "request-session",
                    "session_id": "session-b",
                    "status": "success",
                },
            )
        )
        matching_ack = await module.ack_route(
            authenticated_request(
                module,
                {
                    "request_id": "request-session",
                    "session_id": "session-a",
                    "status": "success",
                },
            )
        )
        send_response = await send_task
        return unrelated_ack, matching_ack, send_response

    unrelated_ack, matching_ack, send_response = run(scenario())
    assert unrelated_ack.status == 409
    assert unrelated_ack.payload["status"] == "stale_session"
    assert matching_ack.status == 200
    assert send_response.status == 200


def test_send_ack_timeout(bridge):
    module, server = bridge
    server.sockets["session-a"] = FakeSocket()
    run(module.register_route(authenticated_request(module, register_payload())))
    module.BRIDGE.ack_timeout_seconds = 0.01

    response = run(
        module.send_route(
            authenticated_request(
                module,
                {"text": "MMH3 Bridge Test", "request_id": "request-4"},
            )
        )
    )

    assert response.status == 504
    assert response.payload["status"] == "ack_timeout"
    assert module.BRIDGE.pending_acks == {}


def test_duplicate_request_id_is_rejected_for_bounded_period(bridge):
    module, _server = bridge
    payload = {"text": "MMH3 Bridge Test", "request_id": "duplicate-request"}

    first = run(module.send_route(authenticated_request(module, payload)))
    second = run(module.send_route(authenticated_request(module, payload)))

    assert first.status == 409
    assert first.payload["status"] == "no_target"
    assert second.status == 409
    assert second.payload["status"] == "duplicate_request_id"
    assert len(module.BRIDGE.recent_request_ids) == 1


def test_basic_rate_limit(bridge):
    module, _server = bridge
    module.BRIDGE.rate_limit_max_requests = 2

    first = run(
        module.send_route(
            authenticated_request(
                module,
                {"text": "one", "request_id": "rate-1"},
            )
        )
    )
    second = run(
        module.send_route(
            authenticated_request(
                module,
                {"text": "two", "request_id": "rate-2"},
            )
        )
    )
    third = run(
        module.send_route(
            authenticated_request(
                module,
                {"text": "three", "request_id": "rate-3"},
            )
        )
    )

    assert first.payload["status"] == "no_target"
    assert second.payload["status"] == "no_target"
    assert third.status == 429
    assert third.payload["status"] == "rate_limited"


def test_request_size_limit(bridge):
    module, _server = bridge
    body = b"x" * (module.MAX_REQUEST_BYTES + 1)

    response = run(
        module.send_route(authenticated_request(module, body=body))
    )

    assert response.status == 413
    assert response.payload["status"] == "request_too_large"


def test_invalid_json(bridge):
    module, _server = bridge
    response = run(
        module.send_route(authenticated_request(module, body=b"{not-json"))
    )

    assert response.status == 400
    assert response.payload["status"] == "invalid_json"


def test_invalid_content_type(bridge):
    module, _server = bridge
    response = run(
        module.send_route(
            authenticated_request(
                module,
                body=b'{"text":"MMH3 Bridge Test","request_id":"request-5"}',
                content_type="text/plain",
            )
        )
    )

    assert response.status == 415
    assert response.payload["status"] == "unsupported_media_type"
