from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
import logging
from pathlib import Path
import re
import secrets
import socket
import ssl
import threading
import time
from typing import Any, Protocol
import urllib.error
import urllib.parse
import urllib.request

from .comfyui_credentials import ComfyUICredentialStore, CredentialStoreError


BRIDGE_API_PREFIX = "/mmh3-bridge/v1"
SUPPORTED_BRIDGE_VERSION = "1.2"
MAX_RESPONSE_BYTES = 256 * 1024
MAX_REQUEST_BYTES = 256 * 1024
MAX_TEXT_BYTES = 128 * 1024
STATUS_TIMEOUT_SECONDS = 5.0
PAIR_REQUEST_TIMEOUT_SECONDS = 5.0
SEND_TIMEOUT_SECONDS = 10.0
DEFAULT_PAIR_POLL_SECONDS = 1.0
MAX_PAIR_EXPIRES_SECONDS = 300
DEFAULT_CLIENT_NAME = "MMH3 Prompt Builder"
SAFE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
LOGGER = logging.getLogger(__name__)


_ERROR_MESSAGES = {
    "bridge_unavailable": "ComfyUI Bridge is unavailable.",
    "unsupported_bridge_version": "The ComfyUI Bridge version is not supported.",
    "no_browser_session": "No usable ComfyUI browser session is connected.",
    "pairing_pending": "ComfyUI pairing is still pending.",
    "pairing_rejected": "ComfyUI pairing was rejected.",
    "pairing_expired": "ComfyUI pairing expired.",
    "pairing_cancelled": "ComfyUI pairing was cancelled.",
    "pairing_capacity_reached": "ComfyUI Bridge has too many pending pairings.",
    "credential_persistence_failed": "The ComfyUI credential could not be saved securely.",
    "credential_unavailable": "No usable ComfyUI credential is available.",
    "credential_url_mismatch": "The ComfyUI credential belongs to a different server.",
    "unauthorized_client": "The ComfyUI credential is no longer authorized; pair again.",
    "no_target": "No ComfyUI target widget is selected.",
    "target_not_found": "The selected ComfyUI target node no longer exists.",
    "stale_target": "The selected ComfyUI target is no longer connected.",
    "stale_session": "The selected ComfyUI browser session is stale.",
    "widget_not_found": "The selected ComfyUI widget no longer exists.",
    "invalid_widget": "The selected ComfyUI widget is not STRING-compatible.",
    "bridge_busy": "ComfyUI Bridge is busy; try again later.",
    "ack_timeout": "The ComfyUI browser did not acknowledge the update in time.",
    "compatibility_unavailable": "The required ComfyUI browser delivery API is unavailable.",
    "text_too_large": "The text is too large for ComfyUI Bridge.",
    "request_too_large": "The JSON request is too large for ComfyUI Bridge.",
    "duplicate_request_id": "The ComfyUI request identifier was already used.",
    "rate_limited": "ComfyUI Bridge is rate-limiting requests.",
    "timeout": "The ComfyUI Bridge request timed out.",
    "malformed_response": "ComfyUI Bridge returned an invalid response.",
    "remote_http_not_allowed": "Remote ComfyUI connections require HTTPS.",
    "redirect_rejected": "ComfyUI Bridge redirects are not allowed.",
}


class ComfyUIBridgeError(RuntimeError):
    """One safe, machine-readable error type for all desktop bridge operations."""

    def __init__(
        self,
        code: str,
        user_message: str | None = None,
        *,
        http_status: int | None = None,
        retryable: bool = False,
    ) -> None:
        safe_code = code if SAFE_CODE_PATTERN.fullmatch(code) else "bridge_error"
        safe_message = user_message or _ERROR_MESSAGES.get(
            safe_code,
            "ComfyUI Bridge returned an error.",
        )
        super().__init__(safe_message)
        self.code = safe_code
        self.user_message = safe_message
        self.http_status = http_status
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class BridgeStatus:
    version: str
    exact_socket_delivery_available: bool
    persistence_available: bool
    target_registered: bool
    target_session_connected: bool
    max_request_bytes: int
    max_text_bytes: int
    ack_timeout_seconds: float
    pairing_expires_seconds: int


@dataclass(slots=True)
class PairingSession:
    base_url: str = field(repr=False)
    pair_id: str = field(repr=False)
    verification_code: str = field(repr=False)
    verifier: bytes = field(repr=False)
    deadline: float
    active: bool = field(default=True, repr=False)


@dataclass(slots=True)
class PairedClient:
    client_id: str = field(repr=False)
    client_credential: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SendResult:
    status: str
    request_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class JsonResponse:
    status: int
    payload: dict[str, Any]


class JsonTransport(Protocol):
    def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float,
        bypass_proxy: bool,
        request_body_limit: int = MAX_REQUEST_BYTES,
    ) -> JsonResponse: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _bridge_error(
    code: str,
    *,
    http_status: int | None = None,
) -> ComfyUIBridgeError:
    return ComfyUIBridgeError(
        code,
        _ERROR_MESSAGES.get(code),
        http_status=http_status,
        retryable=code
        in {
            "bridge_unavailable",
            "timeout",
            "rate_limited",
            "bridge_busy",
            "ack_timeout",
        },
    )


def normalize_comfyui_base_url(value: str) -> str:
    """Return a canonical origin, allowing plaintext HTTP only on loopback."""
    if not isinstance(value, str):
        raise _bridge_error("remote_http_not_allowed")
    candidate = value.strip()
    if (
        not candidate
        or "\\" in candidate
        or "?" in candidate
        or "#" in candidate
        or any(ord(character) < 32 or ord(character) == 127 for character in candidate)
        or any(character.isspace() for character in candidate)
    ):
        raise _bridge_error("remote_http_not_allowed")
    try:
        parsed = urllib.parse.urlsplit(candidate)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        raise _bridge_error("remote_http_not_allowed") from None

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise _bridge_error("remote_http_not_allowed")
    if (
        not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port == 0
        or parsed.netloc.endswith(":")
    ):
        raise _bridge_error("remote_http_not_allowed")

    normalized_host = hostname.lower()
    if "%" in normalized_host:
        raise _bridge_error("remote_http_not_allowed")
    if normalized_host == "localhost":
        normalized_host = "127.0.0.1"

    address: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        address = None
        try:
            normalized_host = normalized_host.encode("idna").decode("ascii").lower()
        except (UnicodeError, ValueError):
            raise _bridge_error("remote_http_not_allowed") from None
        if (
            not normalized_host
            or len(normalized_host) > 253
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or re.fullmatch(r"[a-z0-9-]+", label) is None
                for label in normalized_host.rstrip(".").split(".")
            )
        ):
            raise _bridge_error("remote_http_not_allowed")

    loopback = address is not None and address.is_loopback
    if scheme == "http" and not loopback:
        raise _bridge_error("remote_http_not_allowed")

    if address is not None and address.version == 6:
        rendered_host = f"[{address.compressed}]"
    elif address is not None:
        rendered_host = address.compressed
    else:
        rendered_host = normalized_host
    default_port = 80 if scheme == "http" else 443
    port_suffix = "" if port is None or port == default_port else f":{port}"
    return f"{scheme}://{rendered_host}{port_suffix}"


def _is_loopback_origin(base_url: str) -> bool:
    hostname = urllib.parse.urlsplit(base_url).hostname
    if hostname is None:
        return False
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


class UrllibJsonTransport:
    """Small bounded JSON transport with normal TLS and no redirects."""

    def __init__(self, *, response_limit: int = MAX_RESPONSE_BYTES) -> None:
        self.response_limit = max(1, int(response_limit))

    @staticmethod
    def _opener(*, bypass_proxy: bool) -> urllib.request.OpenerDirector:
        proxy_handler = (
            urllib.request.ProxyHandler({})
            if bypass_proxy
            else urllib.request.ProxyHandler()
        )
        https_handler = urllib.request.HTTPSHandler(context=ssl.create_default_context())
        return urllib.request.build_opener(
            proxy_handler,
            _NoRedirectHandler(),
            https_handler,
        )

    def _read_bounded(self, stream: Any) -> bytes:
        raw = stream.read(self.response_limit + 1)
        if len(raw) > self.response_limit:
            raise _bridge_error("malformed_response")
        return raw

    @staticmethod
    def _decode_json(raw: bytes, *, http_status: int) -> dict[str, Any]:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ComfyUIBridgeError(
                "malformed_response",
                http_status=http_status,
            ) from None
        if not isinstance(payload, dict):
            raise ComfyUIBridgeError(
                "malformed_response",
                http_status=http_status,
            )
        return payload

    def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float,
        bypass_proxy: bool,
        request_body_limit: int = MAX_REQUEST_BYTES,
    ) -> JsonResponse:
        body = None
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        if payload is not None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(body) > request_body_limit:
                raise _bridge_error("request_too_large")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers=request_headers,
        )
        opener = self._opener(bypass_proxy=bypass_proxy)
        try:
            try:
                with opener.open(request, timeout=timeout) as response:
                    status = int(response.status)
                    raw = self._read_bounded(response)
            except urllib.error.HTTPError as exc:
                if 300 <= exc.code < 400:
                    raise ComfyUIBridgeError(
                        "redirect_rejected",
                        http_status=exc.code,
                    ) from None
                status = int(exc.code)
                try:
                    raw = self._read_bounded(exc)
                finally:
                    exc.close()
        except ComfyUIBridgeError:
            raise
        except (TimeoutError, socket.timeout):
            raise _bridge_error("timeout") from None
        except ssl.SSLError:
            raise _bridge_error("bridge_unavailable") from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise _bridge_error("timeout") from None
            raise _bridge_error("bridge_unavailable") from None
        except (ConnectionError, OSError):
            raise _bridge_error("bridge_unavailable") from None
        return JsonResponse(status=status, payload=self._decode_json(raw, http_status=status))


def _positive_int(value: Any, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise _bridge_error("malformed_response")
    return value


def _non_negative_number(value: Any, *, maximum: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= float(value) <= maximum
    ):
        raise _bridge_error("malformed_response")
    return float(value)


def _required_string(payload: dict[str, Any], name: str, *, max_length: int) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise _bridge_error("malformed_response")
    return value


def _error_from_response(response: JsonResponse) -> ComfyUIBridgeError:
    payload = response.payload
    status_code = payload.get("status")
    error_payload = payload.get("error")
    error_code = error_payload.get("code") if isinstance(error_payload, dict) else None
    if (
        payload.get("ok") is not False
        or not isinstance(status_code, str)
        or status_code != error_code
        or SAFE_CODE_PATTERN.fullmatch(status_code) is None
    ):
        return ComfyUIBridgeError(
            "malformed_response",
            http_status=response.status,
        )
    return _bridge_error(status_code, http_status=response.status)


def _require_success(response: JsonResponse, expected_statuses: set[int]) -> dict[str, Any]:
    if response.status not in expected_statuses or response.payload.get("ok") is not True:
        if response.payload.get("ok") is False:
            raise _error_from_response(response)
        raise ComfyUIBridgeError(
            "malformed_response",
            http_status=response.status,
        )
    return response.payload


def _urlsafe_unpadded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class ComfyUIBridgeClient:
    """Synchronous, Qt-independent client for the verified Bridge v1 protocol."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: JsonTransport | None = None,
    ) -> None:
        self.base_url = normalize_comfyui_base_url(base_url)
        self._bypass_proxy = _is_loopback_origin(self.base_url)
        self.transport = transport or UrllibJsonTransport()

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float,
        request_body_limit: int = MAX_REQUEST_BYTES,
    ) -> JsonResponse:
        return self.transport.request_json(
            method,
            f"{self.base_url}{BRIDGE_API_PREFIX}{endpoint}",
            payload=payload,
            headers=headers,
            timeout=timeout,
            bypass_proxy=self._bypass_proxy,
            request_body_limit=request_body_limit,
        )

    def status(self) -> BridgeStatus:
        response = self._request(
            "GET",
            "/status",
            timeout=STATUS_TIMEOUT_SECONDS,
        )
        payload = _require_success(response, {200})
        version = _required_string(payload, "version", max_length=32)
        if version != SUPPORTED_BRIDGE_VERSION:
            raise _bridge_error("unsupported_bridge_version")
        if payload.get("status") != "ready":
            raise _bridge_error("malformed_response")
        limits = payload.get("limits")
        if not isinstance(limits, dict):
            raise _bridge_error("malformed_response")
        boolean_names = (
            "exact_socket_delivery_available",
            "persistence_available",
            "target_registered",
            "target_session_connected",
        )
        if any(not isinstance(payload.get(name), bool) for name in boolean_names):
            raise _bridge_error("malformed_response")
        return BridgeStatus(
            version=version,
            exact_socket_delivery_available=payload["exact_socket_delivery_available"],
            persistence_available=payload["persistence_available"],
            target_registered=payload["target_registered"],
            target_session_connected=payload["target_session_connected"],
            max_request_bytes=_positive_int(
                limits.get("max_request_bytes"),
                maximum=16 * 1024 * 1024,
            ),
            max_text_bytes=_positive_int(
                limits.get("max_text_bytes"),
                maximum=16 * 1024 * 1024,
            ),
            ack_timeout_seconds=_non_negative_number(
                limits.get("ack_timeout_seconds"),
                maximum=120.0,
            ),
            pairing_expires_seconds=_positive_int(
                limits.get("pairing_expires_seconds"),
                maximum=MAX_PAIR_EXPIRES_SECONDS,
            ),
        )

    def start_pairing(self, *, client_name: str = DEFAULT_CLIENT_NAME) -> PairingSession:
        if not isinstance(client_name, str) or not client_name.strip() or len(client_name) > 80:
            raise ComfyUIBridgeError("malformed_response", "The ComfyUI client name is invalid.")
        verifier = secrets.token_bytes(32)
        challenge = hashlib.sha256(verifier).digest()
        started_at = time.monotonic()
        response = self._request(
            "POST",
            "/pair/start",
            payload={
                "challenge": _urlsafe_unpadded(challenge),
                "client_name": client_name.strip(),
            },
            timeout=PAIR_REQUEST_TIMEOUT_SECONDS,
        )
        payload = _require_success(response, {201})
        if payload.get("status") != "pending":
            raise _bridge_error("malformed_response")
        pair_id = _required_string(payload, "pair_id", max_length=256)
        verification_code = _required_string(payload, "verification_code", max_length=16)
        if re.fullmatch(r"\d{6}", verification_code) is None:
            raise _bridge_error("malformed_response")
        expires_in = _positive_int(
            payload.get("expires_in"),
            maximum=MAX_PAIR_EXPIRES_SECONDS,
        )
        return PairingSession(
            base_url=self.base_url,
            pair_id=pair_id,
            verification_code=verification_code,
            verifier=verifier,
            deadline=started_at + expires_in,
        )

    def wait_for_pairing(
        self,
        session: PairingSession,
        cancel_event: threading.Event,
        *,
        poll_interval: float = DEFAULT_PAIR_POLL_SECONDS,
    ) -> PairedClient:
        if session.base_url != self.base_url or not session.active or not session.verifier:
            session.verifier = b""
            session.active = False
            raise _bridge_error("credential_url_mismatch")
        interval = max(0.0, float(poll_interval))
        try:
            while True:
                if cancel_event.is_set():
                    raise _bridge_error("pairing_cancelled")
                remaining = session.deadline - time.monotonic()
                if remaining <= 0:
                    raise _bridge_error("pairing_expired")
                response = self._request(
                    "POST",
                    "/pair/complete",
                    payload={
                        "pair_id": session.pair_id,
                        "verifier": _urlsafe_unpadded(session.verifier),
                    },
                    timeout=min(PAIR_REQUEST_TIMEOUT_SECONDS, max(0.1, remaining)),
                )
                if cancel_event.is_set():
                    raise _bridge_error("pairing_cancelled")
                payload = _require_success(response, {200, 202})
                pairing_status = payload.get("status")
                if response.status == 202 and pairing_status == "pending":
                    wait_seconds = min(interval, max(0.0, session.deadline - time.monotonic()))
                    if cancel_event.wait(wait_seconds):
                        raise _bridge_error("pairing_cancelled")
                    continue
                if response.status != 200 or pairing_status != "paired":
                    raise _bridge_error("malformed_response")
                client_id = _required_string(payload, "client_id", max_length=256)
                credential = _required_string(payload, "client_credential", max_length=512)
                return PairedClient(
                    client_id=client_id,
                    client_credential=credential,
                )
        finally:
            session.verifier = b""
            session.active = False

    def send(self, text: str, *, credential: str) -> SendResult:
        """Send once; an empty string deliberately clears the selected STRING widget."""
        if not isinstance(text, str):
            raise _bridge_error("malformed_response")
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise _bridge_error("text_too_large")
        if not isinstance(credential, str) or not credential or len(credential) > 512:
            raise _bridge_error("credential_unavailable")
        request_id = secrets.token_urlsafe(24)
        response = self._request(
            "POST",
            "/send",
            payload={"request_id": request_id, "text": text},
            headers={"Authorization": f"Bearer {credential}"},
            timeout=SEND_TIMEOUT_SECONDS,
            request_body_limit=MAX_REQUEST_BYTES,
        )
        payload = _require_success(response, {200})
        if payload.get("status") != "success" or payload.get("request_id") != request_id:
            raise _bridge_error("malformed_response")
        return SendResult(status="success", request_id=request_id)


class ComfyUIBridgeService:
    """URL-bound service boundary used later by short-lived Qt workers."""

    def __init__(
        self,
        base_url: str,
        data_dir: Path | str | None = None,
        *,
        credential_store: ComfyUICredentialStore | None = None,
        transport: JsonTransport | None = None,
    ) -> None:
        self.client = ComfyUIBridgeClient(base_url, transport=transport)
        if credential_store is None:
            if data_dir is None:
                raise ValueError("data_dir or credential_store is required")
            credential_store = ComfyUICredentialStore(data_dir)
        self.credential_store = credential_store
        self._credential_invalid = False

    @property
    def base_url(self) -> str:
        return self.client.base_url

    @property
    def has_credential_file(self) -> bool:
        return not self._credential_invalid and self.credential_store.exists

    def test_connection(self) -> BridgeStatus:
        return self.client.status()

    def start_pairing(self, *, client_name: str = DEFAULT_CLIENT_NAME) -> PairingSession:
        try:
            self.client.status()
            session = self.client.start_pairing(client_name=client_name)
            LOGGER.info("ComfyUI pair started")
            return session
        except ComfyUIBridgeError as exc:
            LOGGER.info("ComfyUI pair failed code=%s", exc.code)
            raise

    def wait_for_pairing(
        self,
        session: PairingSession,
        cancel_event: threading.Event,
        *,
        poll_interval: float = DEFAULT_PAIR_POLL_SECONDS,
    ) -> PairedClient:
        try:
            paired = self.client.wait_for_pairing(
                session,
                cancel_event,
                poll_interval=poll_interval,
            )
            if cancel_event.is_set():
                paired.client_credential = ""
                raise _bridge_error("pairing_cancelled")
            try:
                self.credential_store.save(
                    self.base_url,
                    paired.client_id,
                    paired.client_credential,
                )
            except CredentialStoreError:
                paired.client_credential = ""
                raise _bridge_error("credential_persistence_failed") from None
            self._credential_invalid = False
            LOGGER.info("ComfyUI pair succeeded")
            return paired
        except ComfyUIBridgeError as exc:
            LOGGER.info("ComfyUI pair failed code=%s", exc.code)
            raise

    def send(self, text: str) -> SendResult:
        if self._credential_invalid:
            raise _bridge_error("credential_unavailable")
        try:
            stored = self.credential_store.load(self.base_url)
        except CredentialStoreError as exc:
            code = (
                "credential_url_mismatch"
                if exc.code == "credential_url_mismatch"
                else "credential_unavailable"
            )
            error = _bridge_error(code)
            LOGGER.info("ComfyUI send failed code=%s", error.code)
            raise error from None
        try:
            result = self.client.send(text, credential=stored.client_credential)
        except ComfyUIBridgeError as exc:
            if exc.code == "unauthorized_client" and exc.http_status == 401:
                self._credential_invalid = True
                try:
                    self.credential_store.delete()
                except CredentialStoreError:
                    pass
            LOGGER.info("ComfyUI send failed code=%s", exc.code)
            raise
        LOGGER.info("ComfyUI send succeeded")
        return result

    def invalidate_credentials(self) -> None:
        self._credential_invalid = True
        try:
            self.credential_store.delete()
        except CredentialStoreError:
            raise _bridge_error("credential_unavailable") from None
