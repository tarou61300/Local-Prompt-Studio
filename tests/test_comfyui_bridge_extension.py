from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import secrets
import shutil
import sys
import types
from collections import deque
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_INIT = (
    PROJECT_ROOT
    / "comfyui_extension"
    / "MMH3PromptBridge"
    / "__init__.py"
)
BRIDGE_JS = (
    PROJECT_ROOT
    / "comfyui_extension"
    / "MMH3PromptBridge"
    / "js"
    / "mmh3_bridge.js"
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
    def __init__(self, *, closed=False, fail_send=False):
        self.closed = closed
        self.fail_send = fail_send
        self.envelopes = []

    async def send_json(self, envelope):
        if self.fail_send:
            raise ConnectionError("socket send failed")
        self.envelopes.append(envelope)


class SuspendingSocket(FakeSocket):
    def __init__(self, event_type, *, fail_after_release=False):
        super().__init__()
        self.event_type = event_type
        self.fail_after_release = fail_after_release
        self.send_started = asyncio.Event()
        self.release_send = asyncio.Event()

    async def send_json(self, envelope):
        if envelope.get("type") == self.event_type:
            self.send_started.set()
            await self.release_send.wait()
            if self.fail_after_release:
                raise ConnectionError("controlled socket send failure")
        await super().send_json(envelope)


class IncompatibleSocket:
    closed = False


class FakePromptServerInstance:
    def __init__(self):
        self.routes = FakeRoutes()
        self.sockets = {}
        self.sent_sync = []

    def send_sync(self, event, data, sid=None):
        self.sent_sync.append((event, data, sid))


class FakeContent:
    def __init__(self, body, read_hook=None):
        self.body = body
        self.offset = 0
        self.read_hook = read_hook

    async def read(self, size):
        if self.read_hook is not None:
            read_hook = self.read_hook
            self.read_hook = None
            read_hook()
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
        bearer=None,
        remote="127.0.0.1",
        read_hook=None,
    ):
        if body is None:
            body = json.dumps(payload).encode("utf-8")
        self.headers = {"Content-Type": content_type} if content_type else {}
        if bearer is not None:
            self.headers["Authorization"] = f"Bearer {bearer}"
        self.content_length = len(body)
        self.content = FakeContent(body, read_hook=read_hook)
        self.remote = remote


def load_bridge(monkeypatch, tmp_path, *, initial_config=None):
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
    if initial_config is not None:
        data_dir = package_dir / "data"
        data_dir.mkdir()
        config_path = data_dir / "bridge.json"
        if isinstance(initial_config, str):
            config_path.write_text(initial_config, encoding="utf-8")
        else:
            config_path.write_text(
                json.dumps(initial_config),
                encoding="utf-8",
            )

    module_name = "mmh3_prompt_bridge_under_test_" + tmp_path.name
    spec = importlib.util.spec_from_file_location(
        module_name,
        isolated_init,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module, prompt_server


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    return load_bridge(monkeypatch, tmp_path)


def run(coro):
    return asyncio.run(coro)


def encode_urlsafe(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def browser_request(capability, payload=None, **kwargs):
    return FakeRequest(payload, bearer=capability, **kwargs)


def client_request(credential, payload=None, **kwargs):
    return FakeRequest(payload, bearer=credential, **kwargs)


def register_payload(node_id="7", widget_name="text"):
    return {
        "node_id": node_id,
        "widget_name": widget_name,
        "node_type": "ExampleTextNode",
        "graph_id": "graph-a",
    }


async def establish_browser(
    module,
    server,
    *,
    session_id="session-a",
    socket=None,
    nonce_bytes=None,
):
    socket = socket or FakeSocket()
    nonce_bytes = nonce_bytes or secrets.token_bytes(32)
    server.sockets[session_id] = socket
    nonce = encode_urlsafe(nonce_bytes)
    response = await module.browser_hello_route(
        FakeRequest(
            {
                "session_id": session_id,
                "hello_nonce": nonce,
            }
        )
    )
    assert response.status == 200
    envelope = socket.envelopes[-1]
    assert envelope["type"] == module.BROWSER_CAPABILITY_EVENT
    assert envelope["data"]["hello_nonce"] == nonce
    return socket, envelope["data"]["capability"]


def pair_start_payload(*, client_name="MMH3 Test Client"):
    verifier = secrets.token_bytes(32)
    challenge = hashlib.sha256(verifier).digest()
    return verifier, {
        "challenge": encode_urlsafe(challenge),
        "client_name": client_name,
    }


async def start_pair(module, *, client_name="MMH3 Test Client"):
    verifier, payload = pair_start_payload(client_name=client_name)
    response = await module.pair_start_route(
        FakeRequest(payload)
    )
    assert response.status == 201
    return verifier, response


async def establish_client(
    module,
    capability,
    *,
    client_name="MMH3 Test Client",
):
    verifier, start = await start_pair(
        module,
        client_name=client_name,
    )
    pair_id = start.payload["pair_id"]
    decision = await module.pair_decision_route(
        browser_request(
            capability,
            {"pair_id": pair_id, "decision": "allow"},
        )
    )
    assert decision.status == 200
    complete = await module.pair_complete_route(
        FakeRequest(
            {
                "pair_id": pair_id,
                "verifier": encode_urlsafe(verifier),
            }
        )
    )
    assert complete.status == 200
    return complete.payload["client_credential"], complete


def event_envelopes(socket, event_name):
    return [
        envelope
        for envelope in socket.envelopes
        if envelope.get("type") == event_name
    ]


def test_endpoint_set_has_no_revoke_all(bridge):
    module, server = bridge
    expected = {
        ("GET", f"{module.API_PREFIX}/status"),
        ("POST", f"{module.API_PREFIX}/browser/hello"),
        ("POST", f"{module.API_PREFIX}/pair/start"),
        ("POST", f"{module.API_PREFIX}/pair/decision"),
        ("POST", f"{module.API_PREFIX}/pair/complete"),
        ("POST", f"{module.API_PREFIX}/register"),
        ("POST", f"{module.API_PREFIX}/send"),
        ("POST", f"{module.API_PREFIX}/ack"),
    }
    assert set(server.routes.handlers) == expected
    assert all("revoke" not in path for _method, path in expected)


def test_status_is_non_sensitive(bridge):
    module, _server = bridge
    response = run(module.status_route(FakeRequest({})))
    serialized = json.dumps(response.payload)

    assert response.status == 200
    assert response.payload["security"]["authentication"] == "paired_client"
    assert response.payload["target_registered"] is False
    assert "credential" not in serialized
    assert "capability" in response.payload["security"]["browser_authorization"]
    assert "session_id" not in serialized
    assert "pair_id" not in serialized


def test_phase_1_shared_token_is_removed_on_migration(
    monkeypatch,
    tmp_path,
):
    legacy_token = secrets.token_urlsafe(32)
    module, _server = load_bridge(
        monkeypatch,
        tmp_path,
        initial_config={
            "version": 1,
            "token": legacy_token,
            "created_at": 1,
        },
    )
    stored_text = module.STORE_PATH.read_text(encoding="utf-8")
    stored = json.loads(stored_text)

    assert module.CLIENT_STORE.migrated_phase_1_token is True
    assert stored == {"schema_version": 2, "clients": []}
    assert legacy_token not in stored_text
    assert module.CLIENT_STORE.authenticate(legacy_token) is None


def test_malformed_store_fails_closed(monkeypatch, tmp_path):
    module, _server = load_bridge(
        monkeypatch,
        tmp_path,
        initial_config="{not-json",
    )
    response = run(module.status_route(FakeRequest({})))

    assert module.CLIENT_STORE.available is False
    assert response.payload["persistence_available"] is False


def test_pair_start_creates_bounded_one_time_values_and_notifies_browser(
    bridge,
):
    module, server = bridge

    async def scenario():
        socket, _capability = await establish_browser(module, server)
        verifier, response = await start_pair(module)
        return socket, verifier, response

    socket, verifier, response = run(scenario())
    pair_id = response.payload["pair_id"]
    code = response.payload["verification_code"]
    pairing = module.BRIDGE.pairings[pair_id]

    assert pairing.challenge == hashlib.sha256(verifier).digest()
    assert pairing.state == module.PAIR_PENDING
    assert pairing.notification_ready is True
    assert len(code) == 6 and code.isdigit()
    assert response.payload["expires_in"] == 60
    notifications = event_envelopes(socket, module.PAIR_REQUEST_EVENT)
    assert notifications[-1]["data"]["pair_id"] == pair_id
    assert notifications[-1]["data"]["verification_code"] == code


def test_pair_start_with_no_browser_fails_without_pair_or_identifiers(bridge):
    module, _server = bridge
    _verifier, payload = pair_start_payload()

    response = run(module.pair_start_route(FakeRequest(payload)))
    serialized = json.dumps(response.payload)

    assert response.status == 409
    assert response.payload["status"] == "no_browser_session"
    assert "pair_id" not in serialized
    assert "verification_code" not in serialized
    assert module.BRIDGE.pairings == {}


def test_pair_start_with_only_stale_browser_fails_and_cleans_record(bridge):
    module, server = bridge

    async def scenario():
        _socket, _capability = await establish_browser(module, server)
        server.sockets["session-a"] = FakeSocket()
        _verifier, payload = pair_start_payload()
        return await module.pair_start_route(FakeRequest(payload))

    response = run(scenario())
    assert response.status == 409
    assert response.payload["status"] == "no_browser_session"
    assert module.BRIDGE.browser_sessions == {}
    assert module.BRIDGE.pairings == {}


def test_pair_start_when_all_notification_sends_fail_discards_pair(bridge):
    module, server = bridge

    async def scenario():
        socket, _capability = await establish_browser(module, server)
        socket.fail_send = True
        _verifier, payload = pair_start_payload()
        response = await module.pair_start_route(FakeRequest(payload))
        return socket, response

    socket, response = run(scenario())
    serialized = json.dumps(response.payload)
    assert response.status == 409
    assert response.payload["status"] == "no_browser_session"
    assert "pair_id" not in serialized
    assert "verification_code" not in serialized
    assert event_envelopes(socket, module.PAIR_REQUEST_EVENT) == []
    assert module.BRIDGE.pairings == {}


def test_pair_start_with_mixed_browser_sessions_uses_successful_socket(bridge):
    module, server = bridge

    async def scenario():
        stale, _stale_capability = await establish_browser(
            module,
            server,
            session_id="stale-session",
        )
        failing, _failing_capability = await establish_browser(
            module,
            server,
            session_id="failing-session",
        )
        usable, _usable_capability = await establish_browser(
            module,
            server,
            session_id="usable-session",
        )
        server.sockets["stale-session"] = FakeSocket()
        failing.fail_send = True
        verifier, response = await start_pair(module)
        return stale, failing, usable, verifier, response

    stale, failing, usable, verifier, response = run(scenario())
    pair_id = response.payload["pair_id"]
    assert response.status == 201
    assert event_envelopes(stale, module.PAIR_REQUEST_EVENT) == []
    assert event_envelopes(failing, module.PAIR_REQUEST_EVENT) == []
    notifications = event_envelopes(usable, module.PAIR_REQUEST_EVENT)
    assert len(notifications) == 1
    assert notifications[0]["data"]["pair_id"] == pair_id
    assert module.BRIDGE.pairings[pair_id].notification_ready is True
    assert module.BRIDGE.pairings[pair_id].challenge == hashlib.sha256(
        verifier
    ).digest()
    assert set(module.BRIDGE.browser_sessions) == {"usable-session"}


def test_pair_decision_waits_for_successful_notification_barrier(bridge):
    module, server = bridge

    async def scenario():
        socket = SuspendingSocket(module.PAIR_REQUEST_EVENT)
        _socket, capability = await establish_browser(
            module,
            server,
            socket=socket,
        )
        _verifier, payload = pair_start_payload()
        start_task = asyncio.create_task(
            module.pair_start_route(FakeRequest(payload))
        )
        await socket.send_started.wait()
        pairing = next(iter(module.BRIDGE.pairings.values()))
        decision_task = asyncio.create_task(
            module.pair_decision_route(
                browser_request(
                    capability,
                    {"pair_id": pairing.pair_id, "decision": "allow"},
                )
            )
        )
        await asyncio.sleep(0)
        before_release = (
            pairing.notification_ready,
            pairing.state,
            decision_task.done(),
        )
        socket.release_send.set()
        start = await start_task
        decision = await decision_task
        return pairing, before_release, start, decision

    pairing, before_release, start, decision = run(scenario())
    assert before_release == (False, module.PAIR_PENDING, False)
    assert start.status == 201
    assert pairing.notification_ready is True
    assert decision.status == 200
    assert pairing.state == module.PAIR_APPROVED


def test_pair_complete_waits_for_successful_notification_barrier(bridge):
    module, server = bridge

    async def scenario():
        socket = SuspendingSocket(module.PAIR_REQUEST_EVENT)
        _socket, capability = await establish_browser(
            module,
            server,
            socket=socket,
        )
        verifier, payload = pair_start_payload()
        start_task = asyncio.create_task(
            module.pair_start_route(FakeRequest(payload))
        )
        await socket.send_started.wait()
        pairing = next(iter(module.BRIDGE.pairings.values()))
        complete_payload = {
            "pair_id": pairing.pair_id,
            "verifier": encode_urlsafe(verifier),
        }
        complete_task = asyncio.create_task(
            module.pair_complete_route(FakeRequest(complete_payload))
        )
        await asyncio.sleep(0)
        complete_was_waiting = not complete_task.done()
        socket.release_send.set()
        start = await start_task
        pending_complete = await complete_task
        decision = await module.pair_decision_route(
            browser_request(
                capability,
                {"pair_id": pairing.pair_id, "decision": "allow"},
            )
        )
        paired_complete = await module.pair_complete_route(
            FakeRequest(complete_payload)
        )
        return (
            complete_was_waiting,
            start,
            pending_complete,
            decision,
            paired_complete,
        )

    waiting, start, pending, decision, paired = run(scenario())
    assert waiting is True
    assert start.status == 201
    assert pending.status == 202
    assert "client_credential" not in pending.payload
    assert decision.status == 200
    assert paired.status == 200
    assert "client_credential" in paired.payload


def test_failed_notification_releases_waiters_without_actionable_pair(bridge):
    module, server = bridge

    async def scenario():
        socket = SuspendingSocket(
            module.PAIR_REQUEST_EVENT,
            fail_after_release=True,
        )
        _socket, capability = await establish_browser(
            module,
            server,
            socket=socket,
        )
        verifier, payload = pair_start_payload()
        start_task = asyncio.create_task(
            module.pair_start_route(FakeRequest(payload))
        )
        await socket.send_started.wait()
        pairing = next(iter(module.BRIDGE.pairings.values()))
        decision_task = asyncio.create_task(
            module.pair_decision_route(
                browser_request(
                    capability,
                    {"pair_id": pairing.pair_id, "decision": "allow"},
                )
            )
        )
        complete_task = asyncio.create_task(
            module.pair_complete_route(
                FakeRequest(
                    {
                        "pair_id": pairing.pair_id,
                        "verifier": encode_urlsafe(verifier),
                    }
                )
            )
        )
        await asyncio.sleep(0)
        waiters_blocked = not decision_task.done() and not complete_task.done()
        socket.release_send.set()
        start, decision, complete = await asyncio.gather(
            start_task,
            decision_task,
            complete_task,
        )
        return pairing, waiters_blocked, start, decision, complete

    pairing, blocked, start, decision, complete = run(scenario())
    assert blocked is True
    assert start.status == 409
    assert start.payload["status"] == "no_browser_session"
    assert decision.status in {401, 404}
    assert complete.status == 404
    assert "client_credential" not in complete.payload
    assert pairing.notification_ready is False
    assert pairing.state == module.PAIR_EXPIRED
    assert module.BRIDGE.pairings == {}
    assert module.CLIENT_STORE.clients == {}


def test_notification_not_ready_directly_blocks_decision_and_complete(bridge):
    module, server = bridge

    async def scenario():
        _socket, capability = await establish_browser(module, server)
        verifier = secrets.token_bytes(32)
        pairing = await module.BRIDGE.create_pairing(
            challenge=hashlib.sha256(verifier).digest(),
            client_name="Barrier Test",
        )
        assert pairing is not None
        decision = await module.pair_decision_route(
            browser_request(
                capability,
                {"pair_id": pairing.pair_id, "decision": "allow"},
            )
        )
        complete = await module.pair_complete_route(
            FakeRequest(
                {
                    "pair_id": pairing.pair_id,
                    "verifier": encode_urlsafe(verifier),
                }
            )
        )
        await module.BRIDGE.discard_pairing(pairing)
        return pairing, decision, complete

    pairing, decision, complete = run(scenario())
    assert decision.status == 404
    assert complete.status == 404
    assert "client_credential" not in complete.payload
    assert pairing.notification_ready is False
    assert pairing.state == module.PAIR_EXPIRED
    assert module.CLIENT_STORE.clients == {}


def test_client_name_is_length_bounded(bridge):
    module, _server = bridge
    verifier = secrets.token_bytes(32)
    challenge = encode_urlsafe(hashlib.sha256(verifier).digest())
    response = run(
        module.pair_start_route(
            FakeRequest(
                {
                    "challenge": challenge,
                    "client_name": "x" * (module.MAX_CLIENT_NAME_LENGTH + 1),
                }
            )
        )
    )

    assert response.status == 400
    assert response.payload["status"] == "invalid_request"


def test_pair_start_rejects_invalid_challenge(bridge):
    module, _server = bridge
    response = run(
        module.pair_start_route(
            FakeRequest(
                {
                    "challenge": encode_urlsafe(b"short"),
                    "client_name": "Client",
                }
            )
        )
    )

    assert response.status == 400
    assert response.payload["status"] == "invalid_request"


def test_complete_while_pending_returns_202(bridge):
    module, server = bridge

    async def scenario():
        await establish_browser(module, server)
        verifier, start = await start_pair(module)
        return await module.pair_complete_route(
            FakeRequest(
                {
                    "pair_id": start.payload["pair_id"],
                    "verifier": encode_urlsafe(verifier),
                }
            )
        )

    response = run(scenario())
    assert response.status == 202
    assert response.payload["status"] == module.PAIR_PENDING


def test_wrong_verifier_does_not_reveal_pair_state(bridge):
    module, server = bridge

    async def scenario():
        await establish_browser(module, server)
        _verifier, start = await start_pair(module)
        return await module.pair_complete_route(
            FakeRequest(
                {
                    "pair_id": start.payload["pair_id"],
                    "verifier": encode_urlsafe(secrets.token_bytes(32)),
                }
            )
        )

    response = run(scenario())
    assert response.status == 404
    assert response.payload["status"] == "pairing_unavailable"


def test_rejected_pair_cannot_complete(bridge):
    module, server = bridge

    async def scenario():
        _socket, capability = await establish_browser(module, server)
        verifier, start = await start_pair(module)
        pair_id = start.payload["pair_id"]
        decision = await module.pair_decision_route(
            browser_request(
                capability,
                {"pair_id": pair_id, "decision": "reject"},
            )
        )
        complete = await module.pair_complete_route(
            FakeRequest(
                {
                    "pair_id": pair_id,
                    "verifier": encode_urlsafe(verifier),
                }
            )
        )
        return decision, complete

    decision, complete = run(scenario())
    assert decision.payload["status"] == module.PAIR_REJECTED
    assert complete.status == 403
    assert complete.payload["status"] == "pairing_rejected"


def test_expired_pair_cannot_complete(bridge):
    module, server = bridge

    async def scenario():
        await establish_browser(module, server)
        verifier, start = await start_pair(module)
        pairing = module.BRIDGE.pairings[start.payload["pair_id"]]
        pairing.expires_at = 0
        return await module.pair_complete_route(
            FakeRequest(
                {
                    "pair_id": pairing.pair_id,
                    "verifier": encode_urlsafe(verifier),
                }
            )
        )

    response = run(scenario())
    assert response.status == 410
    assert response.payload["status"] == "pairing_expired"


def test_approved_pair_persists_only_credential_hash(bridge):
    module, server = bridge

    async def scenario():
        _socket, capability = await establish_browser(module, server)
        return await establish_client(
            module,
            capability,
            client_name="Desktop Client",
        )

    credential, complete = run(scenario())
    stored_text = module.STORE_PATH.read_text(encoding="utf-8")
    stored = json.loads(stored_text)
    client = stored["clients"][0]

    assert complete.payload["status"] == "paired"
    assert credential.startswith("mmh3c1.")
    assert credential not in stored_text
    assert client["client_name"] == "Desktop Client"
    assert client["credential_hash"] == hashlib.sha256(
        credential.encode("utf-8")
    ).hexdigest()
    assert "token" not in stored_text


def test_consumed_pair_cannot_issue_second_credential(bridge):
    module, server = bridge

    async def scenario():
        _socket, capability = await establish_browser(module, server)
        verifier, start = await start_pair(module)
        pair_id = start.payload["pair_id"]
        await module.pair_decision_route(
            browser_request(
                capability,
                {"pair_id": pair_id, "decision": "allow"},
            )
        )
        payload = {
            "pair_id": pair_id,
            "verifier": encode_urlsafe(verifier),
        }
        first = await module.pair_complete_route(FakeRequest(payload))
        second = await module.pair_complete_route(FakeRequest(payload))
        return first, second

    first, second = run(scenario())
    assert first.status == 200
    assert second.status == 409
    assert second.payload["status"] == "pairing_consumed"
    assert len(module.CLIENT_STORE.clients) == 1


def test_concurrent_pair_decisions_have_one_winner(bridge):
    module, server = bridge

    async def scenario():
        _socket, capability = await establish_browser(module, server)
        _verifier, start = await start_pair(module)
        pair_id = start.payload["pair_id"]
        return await asyncio.gather(
            module.pair_decision_route(
                browser_request(
                    capability,
                    {"pair_id": pair_id, "decision": "allow"},
                )
            ),
            module.pair_decision_route(
                browser_request(
                    capability,
                    {"pair_id": pair_id, "decision": "reject"},
                )
            ),
        )

    responses = run(scenario())
    assert sorted(response.status for response in responses) == [200, 409]
    pairing = next(iter(module.BRIDGE.pairings.values()))
    assert pairing.state in {module.PAIR_APPROVED, module.PAIR_REJECTED}


def test_concurrent_pair_completion_issues_at_most_one_credential(
    bridge,
):
    module, server = bridge

    async def scenario():
        _socket, capability = await establish_browser(module, server)
        verifier, start = await start_pair(module)
        pair_id = start.payload["pair_id"]
        await module.pair_decision_route(
            browser_request(
                capability,
                {"pair_id": pair_id, "decision": "allow"},
            )
        )
        payload = {
            "pair_id": pair_id,
            "verifier": encode_urlsafe(verifier),
        }
        return await asyncio.gather(
            module.pair_complete_route(FakeRequest(payload)),
            module.pair_complete_route(FakeRequest(payload)),
        )

    responses = run(scenario())
    assert sorted(response.status for response in responses) == [200, 409]
    issued = [
        response.payload.get("client_credential")
        for response in responses
        if response.status == 200
    ]
    assert len(issued) == 1
    assert len(module.CLIENT_STORE.clients) == 1


def test_persistence_failure_returns_no_credential_and_does_not_consume(
    bridge,
    monkeypatch,
):
    module, server = bridge

    def fail_persistence(_record):
        raise OSError("simulated failure")

    monkeypatch.setattr(
        module.CLIENT_STORE,
        "add_client",
        fail_persistence,
    )

    async def scenario():
        _socket, capability = await establish_browser(module, server)
        verifier, start = await start_pair(module)
        pair_id = start.payload["pair_id"]
        await module.pair_decision_route(
            browser_request(
                capability,
                {"pair_id": pair_id, "decision": "allow"},
            )
        )
        response = await module.pair_complete_route(
            FakeRequest(
                {
                    "pair_id": pair_id,
                    "verifier": encode_urlsafe(verifier),
                }
            )
        )
        return pair_id, response

    pair_id, response = run(scenario())
    assert response.status == 503
    assert "client_credential" not in response.payload
    assert module.BRIDGE.pairings[pair_id].state == module.PAIR_APPROVED


def test_browser_never_receives_client_credential(bridge):
    module, server = bridge

    async def scenario():
        socket, capability = await establish_browser(module, server)
        credential, _complete = await establish_client(
            module,
            capability,
        )
        return socket, credential

    socket, credential = run(scenario())
    serialized = json.dumps(socket.envelopes)
    assert credential not in serialized
    assert "client_credential" not in serialized


def test_same_socket_rehello_preserves_capability_and_uses_new_nonce(
    bridge,
):
    module, server = bridge

    async def scenario():
        socket, first = await establish_browser(module, server)
        _same_socket, second = await establish_browser(
            module,
            server,
            socket=socket,
            nonce_bytes=b"n" * 32,
        )
        return socket, first, second

    socket, first, second = run(scenario())
    capability_events = event_envelopes(
        socket,
        module.BROWSER_CAPABILITY_EVENT,
    )
    assert first == second
    assert len(capability_events) == 2
    assert capability_events[0]["data"]["hello_nonce"] != (
        capability_events[1]["data"]["hello_nonce"]
    )
    assert capability_events[1]["data"]["capability"] == first


def test_new_socket_invalidates_old_capability(bridge):
    module, server = bridge

    async def scenario():
        _old_socket, old_capability = await establish_browser(
            module,
            server,
        )
        new_socket = FakeSocket()
        _socket, new_capability = await establish_browser(
            module,
            server,
            socket=new_socket,
        )
        old_response = await module.register_route(
            browser_request(old_capability, register_payload())
        )
        new_response = await module.register_route(
            browser_request(new_capability, register_payload())
        )
        return old_capability, new_capability, old_response, new_response

    old_capability, new_capability, old_response, new_response = run(
        scenario()
    )
    assert old_capability != new_capability
    assert old_response.status == 401
    assert new_response.status == 200


def test_browser_capability_is_revalidated_after_body_read(bridge):
    module, server = bridge

    async def scenario():
        _socket, capability = await establish_browser(module, server)
        _verifier, start = await start_pair(module)
        pair_id = start.payload["pair_id"]

        def replace_socket():
            server.sockets["session-a"] = FakeSocket()

        response = await module.pair_decision_route(
            browser_request(
                capability,
                {"pair_id": pair_id, "decision": "allow"},
                read_hook=replace_socket,
            )
        )
        pairing = await module.BRIDGE.get_pairing(pair_id)
        return response, pairing

    response, pairing = run(scenario())
    assert response.status == 401
    assert response.payload["error"]["code"] == "unauthorized_browser"
    assert pairing is not None
    assert pairing.state == module.PAIR_PENDING


def test_pair_decision_revalidates_capability_after_async_lookup(
    bridge,
    monkeypatch,
):
    module, server = bridge

    async def scenario():
        _socket, capability = await establish_browser(module, server)
        _verifier, start = await start_pair(module)
        pair_id = start.payload["pair_id"]
        original_get_pairing = module.BRIDGE.get_pairing

        async def replace_socket_after_lookup(requested_pair_id):
            pairing = await original_get_pairing(requested_pair_id)
            server.sockets["session-a"] = FakeSocket()
            return pairing

        monkeypatch.setattr(
            module.BRIDGE,
            "get_pairing",
            replace_socket_after_lookup,
        )
        response = await module.pair_decision_route(
            browser_request(
                capability,
                {"pair_id": pair_id, "decision": "allow"},
            )
        )
        return response, module.BRIDGE.pairings[pair_id]

    response, pairing = run(scenario())
    assert response.status == 401
    assert response.payload["error"]["code"] == "unauthorized_browser"
    assert pairing.state == module.PAIR_PENDING


def test_rehello_capability_is_sent_only_to_exact_socket(bridge):
    module, server = bridge

    async def scenario():
        socket_a, _capability_a = await establish_browser(
            module,
            server,
            session_id="session-a",
        )
        socket_b, _capability_b = await establish_browser(
            module,
            server,
            session_id="session-b",
        )
        before_b = len(
            event_envelopes(
                socket_b,
                module.BROWSER_CAPABILITY_EVENT,
            )
        )
        await establish_browser(
            module,
            server,
            session_id="session-a",
            socket=socket_a,
            nonce_bytes=b"z" * 32,
        )
        after_b = len(
            event_envelopes(
                socket_b,
                module.BROWSER_CAPABILITY_EVENT,
            )
        )
        return before_b, after_b

    before_b, after_b = run(scenario())
    assert before_b == after_b


def test_browser_operations_require_capability(bridge):
    module, _server = bridge
    decision = run(
        module.pair_decision_route(
            FakeRequest({"pair_id": "x", "decision": "allow"})
        )
    )
    register = run(
        module.register_route(FakeRequest(register_payload()))
    )
    ack = run(
        module.ack_route(
            FakeRequest(
                {
                    "delivery_id": "x",
                    "status": "success",
                }
            )
        )
    )

    assert decision.status == 401
    assert register.status == 401
    assert ack.status == 401


def test_register_derives_session_from_capability_not_payload(bridge):
    module, server = bridge

    async def scenario():
        socket, capability = await establish_browser(
            module,
            server,
            session_id="session-a",
        )
        payload = register_payload()
        payload["session_id"] = "session-attacker"
        response = await module.register_route(
            browser_request(capability, payload)
        )
        return socket, response

    socket, response = run(scenario())
    assert response.status == 200
    assert module.BRIDGE.active_target.session_id == "session-a"
    assert module.BRIDGE.active_target.socket is socket


def test_send_requires_paired_client_credential(bridge):
    module, _server = bridge
    missing = run(
        module.send_route(
            FakeRequest({"text": "test", "request_id": "request-1"})
        )
    )
    legacy = run(
        module.send_route(
            client_request(
                secrets.token_urlsafe(32),
                {"text": "test", "request_id": "request-2"},
            )
        )
    )

    assert missing.status == 401
    assert legacy.status == 401
    assert missing.payload["status"] == "unauthorized_client"


def test_exact_socket_prompt_delivery_and_matching_ack(bridge):
    module, server = bridge

    async def scenario():
        socket, capability = await establish_browser(module, server)
        credential, _complete = await establish_client(
            module,
            capability,
        )
        await module.register_route(
            browser_request(capability, register_payload())
        )
        send_task = asyncio.create_task(
            module.send_route(
                client_request(
                    credential,
                    {
                        "text": "MMH3 exact socket test",
                        "request_id": "exact-1",
                    },
                )
            )
        )
        await asyncio.sleep(0)
        envelope = event_envelopes(
            socket,
            module.SET_TEXT_EVENT,
        )[-1]
        ack = await module.ack_route(
            browser_request(
                capability,
                {
                    "delivery_id": envelope["data"]["delivery_id"],
                    "status": "success",
                },
            )
        )
        send = await send_task
        return socket, envelope, ack, send

    socket, envelope, ack, send = run(scenario())
    assert envelope == {
        "type": module.SET_TEXT_EVENT,
        "data": {
            **envelope["data"],
            "text": "MMH3 exact socket test",
        },
    }
    assert ack.status == 200
    assert send.status == 200
    assert server.sent_sync == []
    assert len(event_envelopes(socket, module.SET_TEXT_EVENT)) == 1


def test_unrelated_browser_capability_cannot_ack(bridge):
    module, server = bridge

    async def scenario():
        socket_a, capability_a = await establish_browser(
            module,
            server,
            session_id="session-a",
        )
        _socket_b, capability_b = await establish_browser(
            module,
            server,
            session_id="session-b",
        )
        credential, _complete = await establish_client(
            module,
            capability_a,
        )
        await module.register_route(
            browser_request(capability_a, register_payload())
        )
        send_task = asyncio.create_task(
            module.send_route(
                client_request(
                    credential,
                    {"text": "text", "request_id": "ack-session"},
                )
            )
        )
        await asyncio.sleep(0)
        delivery_id = event_envelopes(
            socket_a,
            module.SET_TEXT_EVENT,
        )[-1]["data"]["delivery_id"]
        unrelated = await module.ack_route(
            browser_request(
                capability_b,
                {
                    "delivery_id": delivery_id,
                    "status": "success",
                },
            )
        )
        matching = await module.ack_route(
            browser_request(
                capability_a,
                {
                    "delivery_id": delivery_id,
                    "status": "success",
                },
            )
        )
        send = await send_task
        return unrelated, matching, send

    unrelated, matching, send = run(scenario())
    assert unrelated.status == 409
    assert matching.status == 200
    assert send.status == 200


def test_replaced_target_socket_fails_closed_without_fallback(bridge):
    module, server = bridge

    async def scenario():
        old_socket, capability = await establish_browser(module, server)
        credential, _complete = await establish_client(
            module,
            capability,
        )
        await module.register_route(
            browser_request(capability, register_payload())
        )
        new_socket = FakeSocket()
        server.sockets["session-a"] = new_socket
        response = await module.send_route(
            client_request(
                credential,
                {
                    "text": "must not leak",
                    "request_id": "stale-1",
                },
            )
        )
        return old_socket, new_socket, response

    old_socket, new_socket, response = run(scenario())
    assert response.status == 410
    assert response.payload["status"] == "stale_target"
    assert event_envelopes(old_socket, module.SET_TEXT_EVENT) == []
    assert event_envelopes(new_socket, module.SET_TEXT_EVENT) == []
    assert server.sent_sync == []


def test_missing_socket_api_fails_closed_for_hello(bridge):
    module, server = bridge
    server.sockets = object()
    response = run(
        module.browser_hello_route(
            FakeRequest(
                {
                    "session_id": "session-a",
                    "hello_nonce": encode_urlsafe(b"h" * 32),
                }
            )
        )
    )

    assert response.status == 503
    assert response.payload["status"] == "compatibility_unavailable"
    assert server.sent_sync == []


def test_incompatible_socket_object_fails_closed(bridge):
    module, server = bridge
    server.sockets["session-a"] = IncompatibleSocket()
    response = run(
        module.browser_hello_route(
            FakeRequest(
                {
                    "session_id": "session-a",
                    "hello_nonce": encode_urlsafe(b"h" * 32),
                }
            )
        )
    )

    assert response.status == 503
    assert response.payload["status"] == "compatibility_unavailable"


def test_missing_socket_api_prevents_prompt_delivery(bridge):
    module, server = bridge

    async def scenario():
        _socket, capability = await establish_browser(module, server)
        credential, _complete = await establish_client(
            module,
            capability,
        )
        await module.register_route(
            browser_request(capability, register_payload())
        )
        server.sockets = object()
        return await module.send_route(
            client_request(
                credential,
                {"text": "no delivery", "request_id": "compat-1"},
            )
        )

    response = run(scenario())
    assert response.status == 503
    assert response.payload["status"] == "compatibility_unavailable"
    assert server.sent_sync == []


def test_send_with_no_target(bridge):
    module, server = bridge

    async def scenario():
        _socket, capability = await establish_browser(module, server)
        credential, _complete = await establish_client(
            module,
            capability,
        )
        return await module.send_route(
            client_request(
                credential,
                {"text": "text", "request_id": "no-target"},
            )
        )

    response = run(scenario())
    assert response.status == 409
    assert response.payload["status"] == "no_target"


def test_send_cancellation_cleans_pending_ack_and_restores_capacity(bridge):
    module, server = bridge

    async def scenario():
        socket = SuspendingSocket(module.SET_TEXT_EVENT)
        _socket, capability = await establish_browser(
            module,
            server,
            socket=socket,
        )
        credential, _complete = await establish_client(
            module,
            capability,
        )
        await module.register_route(
            browser_request(capability, register_payload())
        )
        send_task = asyncio.create_task(
            module.send_route(
                client_request(
                    credential,
                    {"text": "cancelled", "request_id": "cancel-send"},
                )
            )
        )
        await socket.send_started.wait()
        pending_during_send = len(module.BRIDGE.pending_acks)
        send_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await send_task
        pending_after_cancel = len(module.BRIDGE.pending_acks)

        socket.event_type = None
        retry_task = asyncio.create_task(
            module.send_route(
                client_request(
                    credential,
                    {"text": "retry", "request_id": "cancel-retry"},
                )
            )
        )
        await asyncio.sleep(0)
        envelope = event_envelopes(
            socket,
            module.SET_TEXT_EVENT,
        )[-1]
        ack = await module.ack_route(
            browser_request(
                capability,
                {
                    "delivery_id": envelope["data"]["delivery_id"],
                    "status": "success",
                },
            )
        )
        retry = await retry_task
        return pending_during_send, pending_after_cancel, ack, retry

    during, after, ack, retry = run(scenario())
    assert module.MAX_PENDING_ACKS == 128
    assert during == 1
    assert after == 0
    assert ack.status == 200
    assert retry.status == 200
    assert module.BRIDGE.pending_acks == {}


def test_pending_ack_limit_rejects_without_send_and_recovers(bridge):
    module, server = bridge

    async def scenario():
        socket, capability = await establish_browser(module, server)
        credential, _complete = await establish_client(
            module,
            capability,
        )
        await module.register_route(
            browser_request(capability, register_payload())
        )
        target = module.BRIDGE.active_target
        occupied = []
        assert target is not None
        try:
            for index in range(module.MAX_PENDING_ACKS):
                delivery_id = f"occupied-{index}"
                pending = module.BRIDGE.create_pending_ack(
                    delivery_id,
                    target,
                )
                assert pending is not None
                occupied.append((delivery_id, pending))

            before = len(event_envelopes(socket, module.SET_TEXT_EVENT))
            rejected = await module.send_route(
                client_request(
                    credential,
                    {"text": "must not send", "request_id": "busy"},
                )
            )
            after_rejection = len(
                event_envelopes(socket, module.SET_TEXT_EVENT)
            )

            first_id, first_pending = occupied[0]
            module.BRIDGE.finish_pending_ack(first_id, first_pending)
            first_pending.future.cancel()
            send_task = asyncio.create_task(
                module.send_route(
                    client_request(
                        credential,
                        {"text": "capacity restored", "request_id": "free"},
                    )
                )
            )
            await asyncio.sleep(0)
            envelope = event_envelopes(
                socket,
                module.SET_TEXT_EVENT,
            )[-1]
            ack = await module.ack_route(
                browser_request(
                    capability,
                    {
                        "delivery_id": envelope["data"]["delivery_id"],
                        "status": "success",
                    },
                )
            )
            accepted = await send_task
            return before, after_rejection, rejected, ack, accepted
        finally:
            for delivery_id, pending in occupied:
                module.BRIDGE.finish_pending_ack(delivery_id, pending)
                if not pending.future.done():
                    pending.future.cancel()

    before, after_rejection, rejected, ack, accepted = run(scenario())
    assert rejected.status == 429
    assert rejected.payload["status"] == "bridge_busy"
    assert after_rejection == before
    assert ack.status == 200
    assert accepted.status == 200
    assert module.BRIDGE.pending_acks == {}


def test_pending_ack_timeout_restores_capacity(bridge):
    module, server = bridge

    async def scenario():
        socket, capability = await establish_browser(module, server)
        credential, _complete = await establish_client(
            module,
            capability,
        )
        await module.register_route(
            browser_request(capability, register_payload())
        )
        target = module.BRIDGE.active_target
        occupied = []
        assert target is not None
        try:
            for index in range(module.MAX_PENDING_ACKS - 1):
                delivery_id = f"timeout-occupied-{index}"
                pending = module.BRIDGE.create_pending_ack(
                    delivery_id,
                    target,
                )
                assert pending is not None
                occupied.append((delivery_id, pending))

            module.BRIDGE.ack_timeout_seconds = 0.01
            timeout_task = asyncio.create_task(
                module.send_route(
                    client_request(
                        credential,
                        {"text": "timeout", "request_id": "timeout-full"},
                    )
                )
            )
            await asyncio.sleep(0)
            at_capacity = len(module.BRIDGE.pending_acks)
            timed_out = await timeout_task
            after_timeout = len(module.BRIDGE.pending_acks)

            send_task = asyncio.create_task(
                module.send_route(
                    client_request(
                        credential,
                        {"text": "after timeout", "request_id": "timeout-free"},
                    )
                )
            )
            await asyncio.sleep(0)
            envelope = event_envelopes(
                socket,
                module.SET_TEXT_EVENT,
            )[-1]
            ack = await module.ack_route(
                browser_request(
                    capability,
                    {
                        "delivery_id": envelope["data"]["delivery_id"],
                        "status": "success",
                    },
                )
            )
            accepted = await send_task
            return at_capacity, after_timeout, timed_out, ack, accepted
        finally:
            for delivery_id, pending in occupied:
                module.BRIDGE.finish_pending_ack(delivery_id, pending)
                if not pending.future.done():
                    pending.future.cancel()

    at_capacity, after_timeout, timed_out, ack, accepted = run(scenario())
    assert at_capacity == module.MAX_PENDING_ACKS
    assert timed_out.status == 504
    assert after_timeout == module.MAX_PENDING_ACKS - 1
    assert ack.status == 200
    assert accepted.status == 200
    assert module.BRIDGE.pending_acks == {}


def test_send_ack_timeout_cleans_pending_state(bridge):
    module, server = bridge

    async def scenario():
        _socket, capability = await establish_browser(module, server)
        credential, _complete = await establish_client(
            module,
            capability,
        )
        await module.register_route(
            browser_request(capability, register_payload())
        )
        module.BRIDGE.ack_timeout_seconds = 0.01
        return await module.send_route(
            client_request(
                credential,
                {"text": "text", "request_id": "timeout"},
            )
        )

    response = run(scenario())
    assert response.status == 504
    assert response.payload["status"] == "ack_timeout"
    assert module.BRIDGE.pending_acks == {}


def test_duplicate_request_ids_are_scoped_per_client(bridge):
    module, _server = bridge
    assert module.BRIDGE.claim_request_id("client-a", "same") is True
    assert module.BRIDGE.claim_request_id("client-a", "same") is False
    assert module.BRIDGE.claim_request_id("client-b", "same") is True


def test_basic_rate_limit_remains_bounded(bridge):
    module, _server = bridge
    now = 100.0
    assert module.BRIDGE.allow_request(
        "test-client",
        max_requests=2,
        window_seconds=10,
        now=now,
    )
    assert module.BRIDGE.allow_request(
        "test-client",
        max_requests=2,
        window_seconds=10,
        now=now + 1,
    )
    assert not module.BRIDGE.allow_request(
        "test-client",
        max_requests=2,
        window_seconds=10,
        now=now + 2,
    )
    module.BRIDGE.rate_limit_clients["bounded"] = deque()
    assert len(module.BRIDGE.rate_limit_clients) <= (
        module.MAX_RATE_LIMIT_CLIENTS
    )


def test_request_size_limit(bridge):
    module, _server = bridge
    response = run(
        module.pair_start_route(
            FakeRequest(body=b"x" * (module.MAX_REQUEST_BYTES + 1))
        )
    )
    assert response.status == 413
    assert response.payload["status"] == "request_too_large"


def test_invalid_json(bridge):
    module, _server = bridge
    response = run(
        module.pair_start_route(FakeRequest(body=b"{not-json"))
    )
    assert response.status == 400
    assert response.payload["status"] == "invalid_json"


def test_invalid_content_type(bridge):
    module, _server = bridge
    response = run(
        module.pair_start_route(
            FakeRequest(
                body=b"{}",
                content_type="text/plain",
            )
        )
    )
    assert response.status == 415
    assert response.payload["status"] == "unsupported_media_type"


def test_status_does_not_reveal_target_identity(bridge):
    module, server = bridge

    async def scenario():
        _socket, capability = await establish_browser(module, server)
        payload = register_payload(
            node_id="private-node-id",
            widget_name="private-widget-name",
        )
        await module.register_route(
            browser_request(capability, payload)
        )
        return await module.status_route(FakeRequest({}))

    response = run(scenario())
    serialized = json.dumps(response.payload)
    assert response.payload["target_registered"] is True
    assert response.payload["target_session_connected"] is True
    assert "private-node-id" not in serialized
    assert "private-widget-name" not in serialized


def test_secrets_prompt_and_verification_code_are_not_logged(
    bridge,
    capsys,
):
    module, server = bridge
    capsys.readouterr()

    async def scenario():
        socket, capability = await establish_browser(module, server)
        credential, complete = await establish_client(
            module,
            capability,
        )
        prompt = "MMH3 confidential prompt marker"
        response = await module.send_route(
            client_request(
                credential,
                {"text": prompt, "request_id": "log-test"},
            )
        )
        pair_code = next(
            envelope["data"]["verification_code"]
            for envelope in socket.envelopes
            if envelope["type"] == module.PAIR_REQUEST_EVENT
        )
        return capability, credential, prompt, pair_code, complete, response

    capability, credential, prompt, pair_code, _complete, response = run(
        scenario()
    )
    output = capsys.readouterr()
    combined = output.out + output.err
    assert response.status == 409
    assert capability not in combined
    assert credential not in combined
    assert prompt not in combined
    assert pair_code not in combined


def test_frontend_does_not_touch_comfyui_session_storage():
    source = BRIDGE_JS.read_text(encoding="utf-8")
    assert "sessionStorage" not in source
    assert "localStorage" not in source
    assert "window.name" not in source
    assert "api.clientId" in source


def test_pairing_dialog_uses_safe_text_rendering():
    source = BRIDGE_JS.read_text(encoding="utf-8")
    assert "element.textContent" in source
    assert "request.client_name" in source
    assert ".innerHTML" not in source


def test_frontend_reconnect_clears_ephemeral_state_and_rehellos():
    source = BRIDGE_JS.read_text(encoding="utf-8")
    reconnecting_start = source.index("function handleReconnecting()")
    reconnecting_end = source.index(
        "function handleReconnected()",
        reconnecting_start,
    )
    reconnecting = source[reconnecting_start:reconnecting_end]
    reconnected_start = reconnecting_end
    reconnected_end = source.index(
        "function handleStatus()",
        reconnected_start,
    )
    reconnected = source[reconnected_start:reconnected_end]

    assert "clearBrowserCapability()" in reconnecting
    assert "clearTargetIndication()" in reconnecting
    assert "closeAllPairDialogs()" in reconnecting
    assert "performBrowserHello()" in reconnected


def test_frontend_acknowledges_missing_ephemeral_target_as_stale():
    source = BRIDGE_JS.read_text(encoding="utf-8")

    assert 'if (!activeTarget) {' in source
    assert 'status = "stale_session";' in source


def test_frontend_has_no_manual_token_actions():
    source = BRIDGE_JS.read_text(encoding="utf-8")
    assert "Set Pairing Token" not in source
    assert "Replace Pairing Token" not in source
    assert "Clear Pairing Token" not in source
    assert "pairingToken" not in source


def test_bridge_source_has_no_prompt_queue_or_send_sync_fallback():
    python_source = BRIDGE_INIT.read_text(encoding="utf-8")
    javascript_source = BRIDGE_JS.read_text(encoding="utf-8")
    assert "send_sync" not in python_source
    assert "/prompt" not in python_source
    assert "/prompt" not in javascript_source
    assert "queuePrompt" not in javascript_source
