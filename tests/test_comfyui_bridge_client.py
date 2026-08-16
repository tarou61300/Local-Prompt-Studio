from __future__ import annotations

import base64
from collections import deque
from io import BytesIO
import hashlib
import json
import logging
from pathlib import Path
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request

import pytest

from core.comfyui_bridge import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    MAX_TEXT_BYTES,
    ComfyUIBridgeClient,
    ComfyUIBridgeError,
    ComfyUIBridgeService,
    JsonResponse,
    PairedClient,
    PairingSession,
    UrllibJsonTransport,
    _NoRedirectHandler,
    normalize_comfyui_base_url,
)
from core.comfyui_credentials import (
    CredentialStoreError,
    StoredComfyUICredential,
)
from core.config_manager import AppConfig, ConfigManager


BASE_URL = "http://127.0.0.1:8188"
TEST_CREDENTIAL = "mmh3c1.client.test-credential"
TEST_CLIENT_ID = "client-id"


def status_payload(*, version="1.2"):
    return {
        "ok": True,
        "status": "ready",
        "name": "MMH3 Prompt Bridge",
        "version": version,
        "security": {},
        "deployment_modes": ["local", "remote_https"],
        "limits": {
            "max_request_bytes": MAX_REQUEST_BYTES,
            "max_text_bytes": MAX_TEXT_BYTES,
            "ack_timeout_seconds": 3.0,
            "pairing_expires_seconds": 60,
        },
        "exact_socket_delivery_available": True,
        "persistence_available": True,
        "target_registered": False,
        "target_session_connected": False,
    }


def bridge_error_payload(code):
    return {
        "ok": False,
        "status": code,
        "error": {"code": code, "message": "remote detail must not be exposed"},
    }


class FakeTransport:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.calls = []

    def request_json(self, method, url, **kwargs):
        call = {"method": method, "url": url, **kwargs}
        self.calls.append(call)
        if not self.responses:
            raise AssertionError("unexpected network request")
        response = self.responses.popleft()
        if callable(response):
            response = response(call)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeCredentialStore:
    def __init__(self, *, base_url=BASE_URL, credential=TEST_CREDENTIAL):
        self.base_url = base_url
        self.credential = credential
        self.saved = []
        self.delete_calls = 0
        self.load_calls = 0
        self.fail_save = False
        self.fail_delete = False
        self.load_error = None

    @property
    def exists(self):
        return self.credential is not None

    def save(self, base_url, client_id, credential):
        if self.fail_save:
            raise CredentialStoreError(
                "credential_persistence_failed",
                "safe persistence error",
            )
        self.saved.append((base_url, client_id, credential))
        self.base_url = base_url
        self.credential = credential

    def load(self, base_url):
        self.load_calls += 1
        if self.load_error is not None:
            raise self.load_error
        if self.credential is None:
            raise CredentialStoreError("credential_unavailable", "safe missing error")
        if base_url != self.base_url:
            raise CredentialStoreError("credential_url_mismatch", "safe mismatch error")
        return StoredComfyUICredential(
            base_url=base_url,
            client_id=TEST_CLIENT_ID,
            client_credential=self.credential,
        )

    def delete(self):
        self.delete_calls += 1
        if self.fail_delete:
            raise CredentialStoreError("credential_unavailable", "safe delete error")
        self.credential = None


class FakeHttpResponse:
    def __init__(self, payload, *, status=200):
        self.status = status
        self.raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")

    def read(self, limit=-1):
        return self.raw if limit < 0 else self.raw[:limit]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeOpener:
    def __init__(self, outcome):
        self.outcome = outcome
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:8188", "http://127.0.0.1:8188"),
        ("http://127.42.1.9:8188/", "http://127.42.1.9:8188"),
        ("HTTP://LOCALHOST:8188", "http://127.0.0.1:8188"),
        ("http://[::1]:8188", "http://[::1]:8188"),
        ("https://Example.COM", "https://example.com"),
        ("https://example.com:8443/", "https://example.com:8443"),
        ("http://127.0.0.1:80", "http://127.0.0.1"),
        ("https://example.com:443", "https://example.com"),
    ],
)
def test_url_normalization_accepts_safe_origins(value, expected):
    assert normalize_comfyui_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://192.168.1.20:8188",
        "http://10.0.0.2:8188",
        "http://public.example.com",
        "https://user@example.com",
        "https://user:password@example.com",
        "https://example.com?token=value",
        "https://example.com#fragment",
        "https://example.com/comfy",
        "https://example.com:0",
        "https://example.com:",
        "https://example.com:70000",
        "https://example.com?",
        "https://example.com#",
        "http://[::1%25zone]:8188",
        r"https:\\example.com",
        "https://example.com\nheader",
        "file:///tmp/comfy",
        "ftp://example.com",
        "",
    ],
)
def test_url_normalization_rejects_unsafe_values(value):
    with pytest.raises(ComfyUIBridgeError) as caught:
        normalize_comfyui_base_url(value)
    assert caught.value.code == "remote_http_not_allowed"
    if value:
        assert value not in str(caught.value)


def test_local_requests_bypass_proxy_and_remote_https_keeps_proxy_behavior():
    local_transport = FakeTransport(JsonResponse(200, status_payload()))
    ComfyUIBridgeClient("http://localhost:8188", transport=local_transport).status()
    assert local_transport.calls[0]["bypass_proxy"] is True
    assert local_transport.calls[0]["url"].startswith("http://127.0.0.1:8188/")

    remote_transport = FakeTransport(JsonResponse(200, status_payload()))
    ComfyUIBridgeClient("https://example.com", transport=remote_transport).status()
    assert remote_transport.calls[0]["bypass_proxy"] is False


def test_urllib_local_opener_has_empty_proxy_map_and_tls_verification_enabled(monkeypatch):
    opener_handlers = []
    tls_contexts = []
    original_create_context = ssl.create_default_context

    def record_context(*args, **kwargs):
        context = original_create_context(*args, **kwargs)
        tls_contexts.append(context)
        return context

    def record_opener(*handlers):
        opener_handlers.extend(handlers)
        return object()

    monkeypatch.setattr(ssl, "create_default_context", record_context)
    monkeypatch.setattr(urllib.request, "build_opener", record_opener)
    UrllibJsonTransport._opener(bypass_proxy=True)
    proxy_handlers = [
        handler for handler in opener_handlers if isinstance(handler, urllib.request.ProxyHandler)
    ]
    https_handlers = [
        handler for handler in opener_handlers if isinstance(handler, urllib.request.HTTPSHandler)
    ]
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {}
    assert len(https_handlers) == 1
    assert tls_contexts[0].verify_mode == ssl.CERT_REQUIRED
    assert tls_contexts[0].check_hostname is True


def test_urllib_transport_sends_utf8_json(monkeypatch):
    opener = FakeOpener(FakeHttpResponse({"ok": True, "status": "success"}))
    monkeypatch.setattr(
        UrllibJsonTransport,
        "_opener",
        staticmethod(lambda *, bypass_proxy: opener),
    )
    transport = UrllibJsonTransport()
    response = transport.request_json(
        "POST",
        f"{BASE_URL}/test",
        payload={"text": "日本語テスト"},
        timeout=1.0,
        bypass_proxy=True,
    )
    request, _timeout = opener.requests[0]
    assert response.status == 200
    assert "日本語テスト" in request.data.decode("utf-8")
    assert request.get_header("Content-type") == "application/json"


def test_urllib_transport_rejects_oversized_and_malformed_responses(monkeypatch):
    oversized = FakeOpener(FakeHttpResponse(b"x" * (MAX_RESPONSE_BYTES + 1)))
    monkeypatch.setattr(
        UrllibJsonTransport,
        "_opener",
        staticmethod(lambda *, bypass_proxy: oversized),
    )
    with pytest.raises(ComfyUIBridgeError) as too_large:
        UrllibJsonTransport().request_json(
            "GET", f"{BASE_URL}/test", timeout=1.0, bypass_proxy=True
        )
    assert too_large.value.code == "malformed_response"

    malformed = FakeOpener(FakeHttpResponse(b"not-json"))
    monkeypatch.setattr(
        UrllibJsonTransport,
        "_opener",
        staticmethod(lambda *, bypass_proxy: malformed),
    )
    with pytest.raises(ComfyUIBridgeError) as invalid_json:
        UrllibJsonTransport().request_json(
            "GET", f"{BASE_URL}/test", timeout=1.0, bypass_proxy=True
        )
    assert invalid_json.value.code == "malformed_response"
    assert "not-json" not in str(invalid_json.value)
    assert invalid_json.value.__cause__ is None
    assert invalid_json.value.__suppress_context__ is True


def test_urllib_transport_maps_timeout(monkeypatch):
    opener = FakeOpener(urllib.error.URLError(socket.timeout("private timeout detail")))
    monkeypatch.setattr(
        UrllibJsonTransport,
        "_opener",
        staticmethod(lambda *, bypass_proxy: opener),
    )
    with pytest.raises(ComfyUIBridgeError) as caught:
        UrllibJsonTransport().request_json(
            "GET", f"{BASE_URL}/test", timeout=1.0, bypass_proxy=True
        )
    assert caught.value.code == "timeout"
    assert "private timeout detail" not in str(caught.value)


def test_urllib_transport_maps_tls_failure_without_disabling_verification(monkeypatch):
    opener = FakeOpener(ssl.SSLCertVerificationError("private certificate detail"))
    monkeypatch.setattr(
        UrllibJsonTransport,
        "_opener",
        staticmethod(lambda *, bypass_proxy: opener),
    )
    with pytest.raises(ComfyUIBridgeError) as caught:
        UrllibJsonTransport().request_json(
            "GET", "https://example.com/test", timeout=1.0, bypass_proxy=False
        )
    assert caught.value.code == "bridge_unavailable"
    assert "private certificate detail" not in str(caught.value)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_all_redirects_are_rejected_without_forwarding_authorization(monkeypatch, status):
    redirect = urllib.error.HTTPError(
        f"{BASE_URL}/send",
        status,
        "redirect",
        {"Location": "https://other.example.com/collect"},
        BytesIO(b"redirect body"),
    )
    opener = FakeOpener(redirect)
    monkeypatch.setattr(
        UrllibJsonTransport,
        "_opener",
        staticmethod(lambda *, bypass_proxy: opener),
    )
    with pytest.raises(ComfyUIBridgeError) as caught:
        UrllibJsonTransport().request_json(
            "POST",
            f"{BASE_URL}/send",
            payload={"text": "safe"},
            headers={"Authorization": f"Bearer {TEST_CREDENTIAL}"},
            timeout=1.0,
            bypass_proxy=True,
        )
    assert caught.value.code == "redirect_rejected"
    assert len(opener.requests) == 1
    assert _NoRedirectHandler().redirect_request(None, None, status, "", {}, "https://x") is None
    assert TEST_CREDENTIAL not in str(caught.value)


def test_status_accepts_only_bridge_version_1_2():
    accepted = ComfyUIBridgeClient(
        BASE_URL,
        transport=FakeTransport(JsonResponse(200, status_payload())),
    ).status()
    assert accepted.version == "1.2"
    assert accepted.max_text_bytes == MAX_TEXT_BYTES

    incompatible = ComfyUIBridgeClient(
        BASE_URL,
        transport=FakeTransport(JsonResponse(200, status_payload(version="1.3"))),
    )
    with pytest.raises(ComfyUIBridgeError) as caught:
        incompatible.status()
    assert caught.value.code == "unsupported_bridge_version"


def test_pair_start_uses_exact_verifier_challenge_encoding(monkeypatch):
    verifier = bytes(range(32))
    monkeypatch.setattr("core.comfyui_bridge.secrets.token_bytes", lambda size: verifier)
    transport = FakeTransport(
        JsonResponse(
            201,
            {
                "ok": True,
                "status": "pending",
                "pair_id": "pair-test-id",
                "verification_code": "123456",
                "expires_in": 60,
            },
        )
    )
    session = ComfyUIBridgeClient(BASE_URL, transport=transport).start_pairing()
    sent = transport.calls[0]["payload"]
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier).digest()
    ).rstrip(b"=").decode("ascii")
    assert sent == {
        "challenge": expected_challenge,
        "client_name": "Local Prompt Studio",
    }
    assert "=" not in sent["challenge"]
    assert session.verifier == verifier
    assert session.verification_code == "123456"
    assert verifier.hex() not in repr(session)
    assert "pair-test-id" not in repr(session)
    assert "123456" not in repr(session)


def test_service_pair_start_cancellation_after_status_skips_pair_start_request():
    cancel_event = threading.Event()

    def status_then_cancel(_call):
        cancel_event.set()
        return JsonResponse(200, status_payload())

    transport = FakeTransport(status_then_cancel)
    service = ComfyUIBridgeService(
        BASE_URL,
        credential_store=FakeCredentialStore(credential=None),
        transport=transport,
    )
    with pytest.raises(ComfyUIBridgeError) as caught:
        service.start_pairing(cancel_event=cancel_event)
    assert caught.value.code == "pairing_cancelled"
    assert len(transport.calls) == 1
    assert transport.calls[0]["url"].endswith("/mmh3-bridge/v1/status")


def pairing_session(*, deadline=None):
    return PairingSession(
        base_url=BASE_URL,
        pair_id="pair-id",
        verification_code="654321",
        verifier=b"v" * 32,
        deadline=deadline if deadline is not None else time.monotonic() + 60,
    )


def test_pairing_pending_then_paired_stops_and_returns_secret_safe_result():
    transport = FakeTransport(
        JsonResponse(
            202,
            {"ok": True, "status": "pending", "pair_id": "pair-id", "retry_after": 1},
        ),
        JsonResponse(
            200,
            {
                "ok": True,
                "status": "paired",
                "pair_id": "pair-id",
                "client_id": TEST_CLIENT_ID,
                "client_credential": TEST_CREDENTIAL,
            },
        ),
    )
    session = pairing_session()
    paired = ComfyUIBridgeClient(BASE_URL, transport=transport).wait_for_pairing(
        session,
        threading.Event(),
        poll_interval=0,
    )
    assert len(transport.calls) == 2
    assert paired.client_credential == TEST_CREDENTIAL
    assert TEST_CREDENTIAL not in repr(paired)
    assert TEST_CLIENT_ID not in repr(paired)
    assert session.verifier == b""
    assert session.active is False


def test_pairing_rejected_and_expired_stop_immediately():
    rejected_transport = FakeTransport(
        JsonResponse(403, bridge_error_payload("pairing_rejected"))
    )
    with pytest.raises(ComfyUIBridgeError) as rejected:
        ComfyUIBridgeClient(BASE_URL, transport=rejected_transport).wait_for_pairing(
            pairing_session(), threading.Event(), poll_interval=0
        )
    assert rejected.value.code == "pairing_rejected"
    assert len(rejected_transport.calls) == 1

    server_expired_transport = FakeTransport(
        JsonResponse(410, bridge_error_payload("pairing_expired"))
    )
    with pytest.raises(ComfyUIBridgeError) as server_expired:
        ComfyUIBridgeClient(BASE_URL, transport=server_expired_transport).wait_for_pairing(
            pairing_session(), threading.Event(), poll_interval=0
        )
    assert server_expired.value.code == "pairing_expired"
    assert len(server_expired_transport.calls) == 1

    expired_transport = FakeTransport()
    with pytest.raises(ComfyUIBridgeError) as expired:
        ComfyUIBridgeClient(BASE_URL, transport=expired_transport).wait_for_pairing(
            pairing_session(deadline=time.monotonic() - 1),
            threading.Event(),
        )
    assert expired.value.code == "pairing_expired"
    assert expired_transport.calls == []


def test_pairing_cancellation_stops_polling_and_discards_verifier():
    cancel_event = threading.Event()

    def pending_and_cancel(_call):
        cancel_event.set()
        return JsonResponse(
            202,
            {"ok": True, "status": "pending", "pair_id": "pair-id", "retry_after": 1},
        )

    transport = FakeTransport(pending_and_cancel)
    session = pairing_session()
    with pytest.raises(ComfyUIBridgeError) as caught:
        ComfyUIBridgeClient(BASE_URL, transport=transport).wait_for_pairing(
            session,
            cancel_event,
        )
    assert caught.value.code == "pairing_cancelled"
    assert len(transport.calls) == 1
    assert session.verifier == b""
    assert session.active is False

    initially_cancelled = threading.Event()
    initially_cancelled.set()
    no_network = FakeTransport()
    with pytest.raises(ComfyUIBridgeError) as immediate:
        ComfyUIBridgeClient(BASE_URL, transport=no_network).wait_for_pairing(
            pairing_session(), initially_cancelled
        )
    assert immediate.value.code == "pairing_cancelled"
    assert no_network.calls == []


def test_cancel_during_paired_response_does_not_persist_or_poll_again():
    cancel_event = threading.Event()

    def paired_after_cancel(_call):
        cancel_event.set()
        return JsonResponse(
            200,
            {
                "ok": True,
                "status": "paired",
                "pair_id": "pair-id",
                "client_id": TEST_CLIENT_ID,
                "client_credential": TEST_CREDENTIAL,
            },
        )

    transport = FakeTransport(paired_after_cancel)
    store = FakeCredentialStore(credential=None)
    service = ComfyUIBridgeService(BASE_URL, credential_store=store, transport=transport)
    session = pairing_session()
    with pytest.raises(ComfyUIBridgeError) as caught:
        service.wait_for_pairing(session, cancel_event)
    assert caught.value.code == "pairing_cancelled"
    assert store.saved == []
    assert len(transport.calls) == 1
    assert session.verifier == b""
    assert session.active is False


def test_service_rechecks_cancellation_immediately_before_persistence(monkeypatch):
    cancel_event = threading.Event()
    paired = PairedClient(
        client_id=TEST_CLIENT_ID,
        client_credential=TEST_CREDENTIAL,
    )
    store = FakeCredentialStore(credential=None)
    service = ComfyUIBridgeService(BASE_URL, credential_store=store, transport=FakeTransport())

    def paired_then_cancel(*args, **kwargs):
        cancel_event.set()
        return paired

    monkeypatch.setattr(service.client, "wait_for_pairing", paired_then_cancel)
    with pytest.raises(ComfyUIBridgeError) as caught:
        service.wait_for_pairing(pairing_session(), cancel_event)
    assert caught.value.code == "pairing_cancelled"
    assert store.saved == []
    assert paired.client_credential == ""


def test_service_saves_paired_credential_exactly_once():
    transport = FakeTransport(
        JsonResponse(
            200,
            {
                "ok": True,
                "status": "paired",
                "pair_id": "pair-id",
                "client_id": TEST_CLIENT_ID,
                "client_credential": TEST_CREDENTIAL,
            },
        )
    )
    store = FakeCredentialStore(credential=None)
    service = ComfyUIBridgeService(BASE_URL, credential_store=store, transport=transport)
    service.wait_for_pairing(pairing_session(), threading.Event(), poll_interval=0)
    assert store.saved == [(BASE_URL, TEST_CLIENT_ID, TEST_CREDENTIAL)]


def test_pairing_persistence_failure_fails_closed():
    transport = FakeTransport(
        JsonResponse(
            200,
            {
                "ok": True,
                "status": "paired",
                "pair_id": "pair-id",
                "client_id": TEST_CLIENT_ID,
                "client_credential": TEST_CREDENTIAL,
            },
        )
    )
    store = FakeCredentialStore(credential=None)
    store.fail_save = True
    service = ComfyUIBridgeService(BASE_URL, credential_store=store, transport=transport)
    with pytest.raises(ComfyUIBridgeError) as caught:
        service.wait_for_pairing(pairing_session(), threading.Event(), poll_interval=0)
    assert caught.value.code == "credential_persistence_failed"
    assert TEST_CREDENTIAL not in str(caught.value)
    assert store.saved == []


def successful_send(call):
    request_id = call["payload"]["request_id"]
    return JsonResponse(
        200,
        {"ok": True, "status": "success", "request_id": request_id, "target": {}},
    )


def test_send_uses_stored_authorization_utf8_and_fresh_request_ids():
    transport = FakeTransport(successful_send, successful_send)
    store = FakeCredentialStore()
    service = ComfyUIBridgeService(BASE_URL, credential_store=store, transport=transport)
    first = service.send("日本語プロンプト")
    second = service.send("")
    assert first.request_id != second.request_id
    assert transport.calls[0]["payload"]["text"] == "日本語プロンプト"
    assert transport.calls[1]["payload"]["text"] == ""
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {TEST_CREDENTIAL}"
    assert transport.calls[0]["request_body_limit"] == MAX_REQUEST_BYTES


def test_send_text_and_serialized_request_size_limits(monkeypatch):
    client = ComfyUIBridgeClient(BASE_URL, transport=FakeTransport())
    with pytest.raises(ComfyUIBridgeError) as text_error:
        client.send("あ" * (MAX_TEXT_BYTES // 3 + 1), credential=TEST_CREDENTIAL)
    assert text_error.value.code == "text_too_large"
    assert client.transport.calls == []

    opener = FakeOpener(FakeHttpResponse({"ok": True}))
    monkeypatch.setattr(
        UrllibJsonTransport,
        "_opener",
        staticmethod(lambda *, bypass_proxy: opener),
    )
    serialized_client = ComfyUIBridgeClient(BASE_URL, transport=UrllibJsonTransport())
    with pytest.raises(ComfyUIBridgeError) as request_error:
        serialized_client.send("\\" * MAX_TEXT_BYTES, credential=TEST_CREDENTIAL)
    assert request_error.value.code == "request_too_large"
    assert opener.requests == []


def test_send_requires_matching_response_request_id():
    transport = FakeTransport(
        JsonResponse(
            200,
            {"ok": True, "status": "success", "request_id": "different-request"},
        )
    )
    with pytest.raises(ComfyUIBridgeError) as caught:
        ComfyUIBridgeClient(BASE_URL, transport=transport).send(
            "text", credential=TEST_CREDENTIAL
        )
    assert caught.value.code == "malformed_response"


def test_send_never_retries_after_timeout():
    transport = FakeTransport(ComfyUIBridgeError("timeout"), successful_send)
    with pytest.raises(ComfyUIBridgeError) as caught:
        ComfyUIBridgeClient(BASE_URL, transport=transport).send(
            "text", credential=TEST_CREDENTIAL
        )
    assert caught.value.code == "timeout"
    assert len(transport.calls) == 1


def test_unauthorized_send_invalidates_local_credential_only_for_real_bridge_code():
    store = FakeCredentialStore()
    unauthorized = FakeTransport(
        JsonResponse(401, bridge_error_payload("unauthorized_client"))
    )
    service = ComfyUIBridgeService(BASE_URL, credential_store=store, transport=unauthorized)
    with pytest.raises(ComfyUIBridgeError) as caught:
        service.send("text")
    assert caught.value.code == "unauthorized_client"
    assert store.delete_calls == 1
    assert store.credential is None

    valid_store = FakeCredentialStore()
    network_failure = FakeTransport(ComfyUIBridgeError("bridge_unavailable"))
    service = ComfyUIBridgeService(
        BASE_URL,
        credential_store=valid_store,
        transport=network_failure,
    )
    with pytest.raises(ComfyUIBridgeError):
        service.send("text")
    assert valid_store.delete_calls == 0
    assert valid_store.credential == TEST_CREDENTIAL

    server_error_store = FakeCredentialStore()
    server_error = FakeTransport(
        JsonResponse(500, bridge_error_payload("unauthorized_client"))
    )
    service = ComfyUIBridgeService(
        BASE_URL,
        credential_store=server_error_store,
        transport=server_error,
    )
    with pytest.raises(ComfyUIBridgeError) as server_error_result:
        service.send("text")
    assert server_error_result.value.code == "unauthorized_client"
    assert server_error_store.delete_calls == 0
    assert server_error_store.credential == TEST_CREDENTIAL

    malformed_store = FakeCredentialStore()
    malformed = FakeTransport(JsonResponse(401, {"unexpected": "response"}))
    service = ComfyUIBridgeService(
        BASE_URL,
        credential_store=malformed_store,
        transport=malformed,
    )
    with pytest.raises(ComfyUIBridgeError) as malformed_result:
        service.send("text")
    assert malformed_result.value.code == "malformed_response"
    assert malformed_store.delete_calls == 0
    assert malformed_store.credential == TEST_CREDENTIAL


def test_unauthorized_send_stays_invalid_in_process_when_file_delete_fails():
    store = FakeCredentialStore()
    store.fail_delete = True
    transport = FakeTransport(
        JsonResponse(401, bridge_error_payload("unauthorized_client")),
        successful_send,
    )
    service = ComfyUIBridgeService(BASE_URL, credential_store=store, transport=transport)
    with pytest.raises(ComfyUIBridgeError) as unauthorized:
        service.send("first")
    assert unauthorized.value.code == "unauthorized_client"
    assert store.delete_calls == 1
    assert service.has_credential_file is False

    with pytest.raises(ComfyUIBridgeError) as unavailable:
        service.send("second")
    assert unavailable.value.code == "credential_unavailable"
    assert len(transport.calls) == 1


def test_url_mismatch_prevents_network_request():
    store = FakeCredentialStore(base_url="https://other.example.com")
    transport = FakeTransport()
    service = ComfyUIBridgeService(BASE_URL, credential_store=store, transport=transport)
    with pytest.raises(ComfyUIBridgeError) as caught:
        service.send("must not leave process")
    assert caught.value.code == "credential_url_mismatch"
    assert transport.calls == []


def test_prompt_remote_detail_and_secrets_are_absent_from_logs_and_errors(caplog):
    prompt = "private-prompt-marker"
    transport = FakeTransport(JsonResponse(409, bridge_error_payload("no_target")))
    service = ComfyUIBridgeService(
        BASE_URL,
        credential_store=FakeCredentialStore(),
        transport=transport,
    )
    with caplog.at_level(logging.INFO):
        with pytest.raises(ComfyUIBridgeError) as caught:
            service.send(prompt)
    combined = caplog.text + str(caught.value) + repr(caught.value)
    assert caught.value.code == "no_target"
    assert prompt not in combined
    assert TEST_CREDENTIAL not in combined
    assert "remote detail must not be exposed" not in combined
    assert BASE_URL not in combined


def test_remote_url_and_existing_secrets_are_absent_from_dataclass_repr():
    remote_url = "https://secret-host.example.com:8443"
    session = PairingSession(
        base_url=remote_url,
        pair_id="private-pair-id",
        verification_code="123456",
        verifier=b"private-verifier",
        deadline=1.0,
    )
    stored = StoredComfyUICredential(
        base_url=remote_url,
        client_id="private-client-id",
        client_credential="private-client-credential",
    )
    config = AppConfig(comfyui_url=remote_url)
    representations = (repr(session), repr(stored), repr(config))
    for representation in representations:
        assert remote_url not in representation
    assert "private-pair-id" not in representations[0]
    assert "123456" not in representations[0]
    assert "private-verifier" not in representations[0]
    assert "private-client-id" not in representations[1]
    assert "private-client-credential" not in representations[1]


def test_construction_and_config_load_perform_zero_network_calls(tmp_path, monkeypatch):
    network_calls = []

    def forbidden_network(*args, **kwargs):
        network_calls.append((args, kwargs))
        raise AssertionError("network must not be used during construction")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden_network)
    monkeypatch.setattr(
        "core.comfyui_credentials.WindowsDpapiProtector",
        lambda: (_ for _ in ()).throw(AssertionError("DPAPI must remain lazy")),
    )
    transport = FakeTransport()
    store = FakeCredentialStore()
    client = ComfyUIBridgeClient(BASE_URL, transport=transport)
    service = ComfyUIBridgeService(BASE_URL, credential_store=store, transport=transport)
    lazy_service = ComfyUIBridgeService(BASE_URL, data_dir=tmp_path, transport=transport)
    manager = ConfigManager(tmp_path)
    manager.save(AppConfig())
    config = manager.load()
    assert client.base_url == BASE_URL
    assert service.base_url == BASE_URL
    assert lazy_service.base_url == BASE_URL
    assert lazy_service.has_credential_file is False
    assert config.comfyui_url == BASE_URL
    assert network_calls == []
    assert transport.calls == []


def test_core_module_has_no_qt_worker_thread_timer_or_prompt_queue_api():
    source = Path(__file__).resolve().parents[1].joinpath(
        "src", "core", "comfyui_bridge.py"
    ).read_text(encoding="utf-8")
    assert "PySide6" not in source
    assert "QThread" not in source
    assert "QTimer" not in source
    assert "threading.Thread" not in source
    assert '"/prompt"' not in source
    assert "queuePrompt" not in source
