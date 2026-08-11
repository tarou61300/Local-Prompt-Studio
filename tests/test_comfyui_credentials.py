from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from core import comfyui_credentials as credentials
from core.comfyui_credentials import (
    CREDENTIAL_FILE_NAME,
    MAX_PROTECTED_FILE_BYTES,
    ComfyUICredentialStore,
    CredentialStoreError,
    WindowsDpapiProtector,
)


BASE_URL = "http://127.0.0.1:8188"
CLIENT_ID = "test-client-id"
CLIENT_CREDENTIAL = "mmh3c1.test-client-id.test-secret-value"


class FakeProtector:
    def __init__(self) -> None:
        self.protect_calls = 0
        self.unprotect_calls = 0

    def protect(self, data: bytes) -> bytes:
        self.protect_calls += 1
        return b"FAKE-DPAPI\0" + data[::-1]

    def unprotect(self, data: bytes) -> bytes:
        self.unprotect_calls += 1
        if not data.startswith(b"FAKE-DPAPI\0"):
            raise ValueError("fake protected payload is invalid")
        return data.removeprefix(b"FAKE-DPAPI\0")[::-1]


class FailingProtector:
    def protect(self, data: bytes) -> bytes:
        raise RuntimeError("protection failed")

    def unprotect(self, data: bytes) -> bytes:
        raise RuntimeError("decryption failed")


class ScopedProtector:
    def __init__(self, scope: bytes) -> None:
        self.scope = scope

    def protect(self, data: bytes) -> bytes:
        return self.scope + b"\0" + data[::-1]

    def unprotect(self, data: bytes) -> bytes:
        prefix = self.scope + b"\0"
        if not data.startswith(prefix):
            raise RuntimeError("different user scope")
        return data.removeprefix(prefix)[::-1]


def test_store_construction_has_no_protection_or_file_side_effect(tmp_path):
    protector = FakeProtector()
    store = ComfyUICredentialStore(tmp_path, protector=protector)
    assert store.path == tmp_path / CREDENTIAL_FILE_NAME
    assert store.exists is False
    assert protector.protect_calls == 0
    assert protector.unprotect_calls == 0
    assert not store.path.exists()


def test_fake_protector_atomic_round_trip_and_no_plaintext_on_disk(tmp_path):
    protector = FakeProtector()
    store = ComfyUICredentialStore(tmp_path, protector=protector)
    store.save(BASE_URL, CLIENT_ID, CLIENT_CREDENTIAL)

    on_disk = store.path.read_bytes()
    assert CLIENT_CREDENTIAL.encode("utf-8") not in on_disk
    assert CLIENT_ID.encode("utf-8") not in on_disk
    assert list(tmp_path.glob(".comfyui-credentials-*.tmp")) == []

    loaded = store.load(BASE_URL)
    assert loaded.base_url == BASE_URL
    assert loaded.client_id == CLIENT_ID
    assert loaded.client_credential == CLIENT_CREDENTIAL
    assert CLIENT_CREDENTIAL not in repr(loaded)
    assert CLIENT_ID not in repr(loaded)


def test_credential_url_binding_fails_closed(tmp_path):
    store = ComfyUICredentialStore(tmp_path, protector=FakeProtector())
    store.save(BASE_URL, CLIENT_ID, CLIENT_CREDENTIAL)
    with pytest.raises(CredentialStoreError) as caught:
        store.load("https://remote.example.com")
    assert caught.value.code == "credential_url_mismatch"
    assert CLIENT_CREDENTIAL not in str(caught.value)


def test_corrupt_file_fails_closed(tmp_path):
    store = ComfyUICredentialStore(tmp_path, protector=FakeProtector())
    tmp_path.mkdir(exist_ok=True)
    store.path.write_bytes(b"not-protected")
    with pytest.raises(CredentialStoreError) as caught:
        store.load(BASE_URL)
    assert caught.value.code == "credential_unavailable"
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_oversized_protected_file_is_rejected_before_decryption(tmp_path):
    protector = FakeProtector()
    store = ComfyUICredentialStore(tmp_path, protector=protector)
    store.path.write_bytes(b"x" * (MAX_PROTECTED_FILE_BYTES + 1))
    with pytest.raises(CredentialStoreError) as caught:
        store.load(BASE_URL)
    assert caught.value.code == "credential_unavailable"
    assert protector.unprotect_calls == 0


def test_invalid_schema_fails_closed(tmp_path):
    protector = FakeProtector()
    store = ComfyUICredentialStore(tmp_path, protector=protector)
    tmp_path.mkdir(exist_ok=True)
    invalid = json.dumps(
        {
            "schema_version": 99,
            "base_url": BASE_URL,
            "client_id": CLIENT_ID,
            "client_credential": CLIENT_CREDENTIAL,
        }
    ).encode("utf-8")
    store.path.write_bytes(protector.protect(invalid))
    with pytest.raises(CredentialStoreError) as caught:
        store.load(BASE_URL)
    assert caught.value.code == "credential_unavailable"


def test_decryption_and_protection_fail_closed_without_plaintext_fallback(tmp_path):
    store = ComfyUICredentialStore(tmp_path, protector=FailingProtector())
    with pytest.raises(CredentialStoreError) as save_error:
        store.save(BASE_URL, CLIENT_ID, CLIENT_CREDENTIAL)
    assert save_error.value.code == "credential_persistence_failed"
    assert not store.path.exists()

    tmp_path.mkdir(exist_ok=True)
    store.path.write_bytes(b"encrypted-marker")
    with pytest.raises(CredentialStoreError) as load_error:
        store.load(BASE_URL)
    assert load_error.value.code == "credential_unavailable"
    assert CLIENT_CREDENTIAL not in str(load_error.value)


def test_local_validity_helper_accepts_only_current_url_usable_credential(tmp_path):
    store = ComfyUICredentialStore(tmp_path, protector=FakeProtector())
    assert store.has_valid_credential(BASE_URL) is False

    store.save(BASE_URL, CLIENT_ID, CLIENT_CREDENTIAL)
    assert store.has_valid_credential(BASE_URL) is True
    assert store.has_valid_credential("https://remote.example.com") is False

    store.path.write_bytes(b"truncated-protected-value")
    assert store.has_valid_credential(BASE_URL) is False

    invalid_schema = json.dumps(
        {
            "schema_version": 999,
            "base_url": BASE_URL,
            "client_id": CLIENT_ID,
            "client_credential": CLIENT_CREDENTIAL,
        }
    ).encode("utf-8")
    store.path.write_bytes(store._data_protector().protect(invalid_schema))
    assert store.has_valid_credential(BASE_URL) is False


def test_local_validity_helper_fails_closed_for_decrypt_failure(tmp_path):
    writer = ComfyUICredentialStore(tmp_path, protector=FakeProtector())
    writer.save(BASE_URL, CLIENT_ID, CLIENT_CREDENTIAL)
    reader = ComfyUICredentialStore(tmp_path, protector=FailingProtector())
    assert reader.has_valid_credential(BASE_URL) is False


def test_local_validity_helper_rejects_another_user_scope(tmp_path):
    writer = ComfyUICredentialStore(
        tmp_path,
        protector=ScopedProtector(b"user-a"),
    )
    writer.save(BASE_URL, CLIENT_ID, CLIENT_CREDENTIAL)
    reader = ComfyUICredentialStore(
        tmp_path,
        protector=ScopedProtector(b"user-b"),
    )
    assert reader.has_valid_credential(BASE_URL) is False


def test_local_validity_helper_clears_loaded_secret_object(monkeypatch, tmp_path):
    store = ComfyUICredentialStore(tmp_path, protector=FakeProtector())
    store.save(BASE_URL, CLIENT_ID, CLIENT_CREDENTIAL)
    loaded = store.load(BASE_URL)
    monkeypatch.setattr(store, "load", lambda normalized_url: loaded)
    assert store.has_valid_credential(BASE_URL) is True
    assert loaded.base_url == ""
    assert loaded.client_id == ""
    assert loaded.client_credential == ""


def test_delete_is_idempotent(tmp_path):
    store = ComfyUICredentialStore(tmp_path, protector=FakeProtector())
    store.save(BASE_URL, CLIENT_ID, CLIENT_CREDENTIAL)
    store.delete()
    store.delete()
    assert store.exists is False


def test_real_dpapi_fails_closed_when_platform_is_not_windows(monkeypatch):
    monkeypatch.setattr(credentials.sys, "platform", "linux")
    with pytest.raises(CredentialStoreError) as caught:
        WindowsDpapiProtector()
    assert caught.value.code == "credential_unavailable"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI test")
def test_real_windows_dpapi_round_trip(tmp_path):
    protector = WindowsDpapiProtector()
    plaintext = b"MMH3 DPAPI round-trip test value"
    try:
        protected = protector.protect(plaintext)
    except CredentialStoreError as exc:
        pytest.skip(f"DPAPI is unavailable in this test user context: {exc.code}")
    assert protected
    assert plaintext not in protected
    assert protector.unprotect(protected) == plaintext

    store = ComfyUICredentialStore(tmp_path, protector=protector)
    store.save(BASE_URL, CLIENT_ID, CLIENT_CREDENTIAL)
    assert CLIENT_CREDENTIAL.encode("utf-8") not in store.path.read_bytes()
    assert store.load(BASE_URL).client_credential == CLIENT_CREDENTIAL
