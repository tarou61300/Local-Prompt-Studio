from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiohttp import web
from server import PromptServer


NODE_CLASS_MAPPINGS: dict[str, Any] = {}
NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {}
WEB_DIRECTORY = "./js"

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]

API_NAME = "MMH3 Prompt Bridge"
API_VERSION = "1.2"
API_PREFIX = "/mmh3-bridge/v1"

BROWSER_CAPABILITY_EVENT = "mmh3.bridge.browser_capability"
PAIR_REQUEST_EVENT = "mmh3.bridge.pair_request"
PAIR_RESOLVED_EVENT = "mmh3.bridge.pair_resolved"
SET_TEXT_EVENT = "mmh3.bridge.set_text"
TARGET_CHANGED_EVENT = "mmh3.bridge.target_changed"

MAX_REQUEST_BYTES = 256 * 1024
MAX_TEXT_BYTES = 128 * 1024
ACK_TIMEOUT_SECONDS = 3.0
MAX_REQUEST_ID_LENGTH = 128
MAX_IDENTIFIER_LENGTH = 256
MAX_CLIENT_NAME_LENGTH = 80
MAX_ACK_DETAIL_LENGTH = 512

SECRET_BYTES = 32
CLIENT_ID_BYTES = 16
STORE_SCHEMA_VERSION = 2
PAIR_TTL_SECONDS = 60.0
PAIR_TERMINAL_RETENTION_SECONDS = 300.0
MAX_PENDING_PAIRINGS = 16
MAX_PAIRING_RECORDS = 128
MAX_BROWSER_SESSIONS = 512
MAX_PAIRED_CLIENTS = 128
MAX_PENDING_ACKS = 128

RECENT_REQUEST_ID_TTL_SECONDS = 60.0
MAX_RECENT_REQUEST_IDS = 1024
RATE_LIMIT_WINDOW_SECONDS = 10.0
RATE_LIMIT_MAX_REQUESTS = 120
PAIR_START_RATE_WINDOW_SECONDS = 60.0
PAIR_START_RATE_MAX_REQUESTS = 5
BROWSER_HELLO_RATE_WINDOW_SECONDS = 60.0
BROWSER_HELLO_RATE_MAX_REQUESTS = 20
MAX_RATE_LIMIT_CLIENTS = 512

EXTENSION_DIR = Path(__file__).resolve().parent
DATA_DIR = EXTENSION_DIR / "data"
STORE_PATH = DATA_DIR / "bridge.json"

PAIR_PENDING = "pending"
PAIR_APPROVED = "approved"
PAIR_REJECTED = "rejected"
PAIR_EXPIRED = "expired"
PAIR_CONSUMED = "consumed"
PAIR_TERMINAL_STATES = {PAIR_REJECTED, PAIR_EXPIRED, PAIR_CONSUMED}

ACK_STATUSES = {
    "success",
    "target_not_found",
    "widget_not_found",
    "invalid_widget",
    "stale_session",
    "internal_error",
}


@dataclass(frozen=True)
class ClientRecord:
    client_id: str
    client_name: str
    credential_hash: str
    created_at: float

    def stored_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "client_name": self.client_name,
            "credential_hash": self.credential_hash,
            "created_at": self.created_at,
        }


class ClientStore:
    """Atomic schema-v2 store containing hashes of paired credentials only."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.clients: dict[str, ClientRecord] = {}
        self.available = True
        self.migrated_phase_1_token = False
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            self.available = False
            return

        if isinstance(payload, dict) and payload.get("version") == 1:
            try:
                self._write_records({})
            except OSError:
                self.available = False
                return
            self.migrated_phase_1_token = True
            return

        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != STORE_SCHEMA_VERSION
        ):
            self.available = False
            return
        stored_clients = payload.get("clients")
        if (
            not isinstance(stored_clients, list)
            or len(stored_clients) > MAX_PAIRED_CLIENTS
        ):
            self.available = False
            return

        loaded: dict[str, ClientRecord] = {}
        try:
            for item in stored_clients:
                if not isinstance(item, dict):
                    raise ValueError("invalid client record")
                client_id = item["client_id"]
                client_name = item["client_name"]
                credential_hash = item["credential_hash"]
                created_at = item["created_at"]
                if (
                    not isinstance(client_id, str)
                    or not client_id
                    or len(client_id) > MAX_IDENTIFIER_LENGTH
                    or not isinstance(client_name, str)
                    or len(client_name) > MAX_CLIENT_NAME_LENGTH
                    or not isinstance(credential_hash, str)
                    or len(credential_hash) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in credential_hash
                    )
                    or not isinstance(created_at, (int, float))
                    or client_id in loaded
                ):
                    raise ValueError("invalid client record")
                loaded[client_id] = ClientRecord(
                    client_id=client_id,
                    client_name=client_name,
                    credential_hash=credential_hash,
                    created_at=float(created_at),
                )
        except (KeyError, ValueError):
            self.available = False
            return
        self.clients = loaded
        self._restrict_permissions()

    def add_client(self, record: ClientRecord) -> None:
        if not self.available:
            raise OSError("The bridge credential store is unavailable.")
        if record.client_id in self.clients:
            raise OSError("A duplicate client identifier was generated.")
        if len(self.clients) >= MAX_PAIRED_CLIENTS:
            raise OSError("The paired-client limit has been reached.")
        updated = dict(self.clients)
        updated[record.client_id] = record
        self._write_records(updated)
        self.clients = updated

    def authenticate(self, credential: str) -> ClientRecord | None:
        if not self.available or len(credential) > 512:
            return None
        parts = credential.split(".")
        if len(parts) != 3 or parts[0] != "mmh3c1":
            return None
        record = self.clients.get(parts[1])
        if record is None:
            return None
        supplied_hash = hashlib.sha256(credential.encode("utf-8")).hexdigest()
        if hmac.compare_digest(supplied_hash, record.credential_hash):
            return record
        return None

    def _write_records(self, records: dict[str, ClientRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        temporary = self.path.with_name(
            f".{self.path.name}.{secrets.token_hex(8)}.tmp"
        )
        payload = {
            "schema_version": STORE_SCHEMA_VERSION,
            "clients": [
                records[client_id].stored_dict()
                for client_id in sorted(records)
            ],
        }
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
                newline="\n",
            ) as stream:
                descriptor = None
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self._restrict_permissions()
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _restrict_permissions(self) -> None:
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


@dataclass
class BrowserSession:
    session_id: str
    socket: Any
    capability: str
    capability_hash: bytes
    issued_at: float


@dataclass
class Pairing:
    pair_id: str
    challenge: bytes
    verification_code: str
    client_name: str
    created_at: float
    expires_at: float
    state: str = PAIR_PENDING
    terminal_at: float | None = None
    notification_ready: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


@dataclass(frozen=True)
class Target:
    session_id: str
    socket: Any
    node_id: str
    widget_name: str
    node_type: str
    graph_id: str
    registered_at: float

    def public_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "widget_name": self.widget_name,
            "node_type": self.node_type,
            "graph_id": self.graph_id,
            "registered_at": self.registered_at,
        }


@dataclass
class PendingAck:
    target: Target
    future: asyncio.Future[dict[str, str]]


class BridgeState:
    """Bounded in-memory protocol state plus the small client-hash store."""

    def __init__(
        self,
        client_store: ClientStore,
        *,
        ack_timeout_seconds: float = ACK_TIMEOUT_SECONDS,
    ) -> None:
        self.client_store = client_store
        self.client_store_lock = asyncio.Lock()
        self.pairings_lock = asyncio.Lock()
        self.pairings: OrderedDict[str, Pairing] = OrderedDict()
        self.browser_sessions: OrderedDict[str, BrowserSession] = OrderedDict()
        self.browser_capability_index: dict[bytes, BrowserSession] = {}
        self.active_target: Target | None = None
        self.pending_acks: dict[str, PendingAck] = {}
        self.ack_timeout_seconds = ack_timeout_seconds
        self.recent_request_ids: OrderedDict[
            tuple[str, str], float
        ] = OrderedDict()
        self.rate_limit_clients: OrderedDict[str, deque[float]] = OrderedDict()
        self.recent_request_id_ttl_seconds = (
            RECENT_REQUEST_ID_TTL_SECONDS
        )

    async def create_pairing(
        self,
        *,
        challenge: bytes,
        client_name: str,
    ) -> Pairing | None:
        async with self.pairings_lock:
            now = time.monotonic()
            self._cleanup_pairings_locked(now)
            active_count = sum(
                pairing.state in {PAIR_PENDING, PAIR_APPROVED}
                for pairing in self.pairings.values()
            )
            if active_count >= MAX_PENDING_PAIRINGS:
                return None
            pair_id = secrets.token_urlsafe(SECRET_BYTES)
            while pair_id in self.pairings:
                pair_id = secrets.token_urlsafe(SECRET_BYTES)
            active_codes = {
                pairing.verification_code
                for pairing in self.pairings.values()
                if pairing.state in {PAIR_PENDING, PAIR_APPROVED}
            }
            verification_code = f"{secrets.randbelow(1_000_000):06d}"
            while verification_code in active_codes:
                verification_code = f"{secrets.randbelow(1_000_000):06d}"
            pairing = Pairing(
                pair_id=pair_id,
                challenge=challenge,
                verification_code=verification_code,
                client_name=client_name,
                created_at=now,
                expires_at=now + PAIR_TTL_SECONDS,
            )
            self.pairings[pair_id] = pairing
            self._trim_pairings_locked()
            return pairing

    async def get_pairing(self, pair_id: str) -> Pairing | None:
        async with self.pairings_lock:
            self._cleanup_pairings_locked(time.monotonic())
            pairing = self.pairings.get(pair_id)
            if pairing is not None:
                self.pairings.move_to_end(pair_id)
            return pairing

    async def pending_pairings(self) -> list[Pairing]:
        async with self.pairings_lock:
            self._cleanup_pairings_locked(time.monotonic())
            return [
                pairing
                for pairing in self.pairings.values()
                if pairing.state == PAIR_PENDING
                and pairing.notification_ready
            ]

    async def discard_pairing(self, pairing: Pairing) -> None:
        """Expire and remove a pairing that reached no approval browser."""
        async with pairing.lock:
            pairing.state = PAIR_EXPIRED
            pairing.terminal_at = time.monotonic()
            await self.remove_pairing_record(pairing)

    async def remove_pairing_record(self, pairing: Pairing) -> None:
        async with self.pairings_lock:
            if self.pairings.get(pairing.pair_id) is pairing:
                self.pairings.pop(pairing.pair_id, None)

    async def pairing_is_current(self, pairing: Pairing) -> bool:
        async with self.pairings_lock:
            return self.pairings.get(pairing.pair_id) is pairing

    def expire_pairing_if_needed(
        self,
        pairing: Pairing,
        *,
        now: float | None = None,
    ) -> None:
        timestamp = time.monotonic() if now is None else now
        if (
            pairing.state in {PAIR_PENDING, PAIR_APPROVED}
            and timestamp >= pairing.expires_at
        ):
            pairing.state = PAIR_EXPIRED
            pairing.terminal_at = timestamp

    def _cleanup_pairings_locked(self, now: float) -> None:
        for pairing in self.pairings.values():
            # A decision or completion owns the per-pair lock. Do not alter
            # its state while that atomic transition is in progress.
            if not pairing.lock.locked():
                self.expire_pairing_if_needed(pairing, now=now)
        expired_keys = [
            pair_id
            for pair_id, pairing in self.pairings.items()
            if pairing.state in PAIR_TERMINAL_STATES
            and pairing.terminal_at is not None
            and now - pairing.terminal_at
            >= PAIR_TERMINAL_RETENTION_SECONDS
        ]
        for pair_id in expired_keys:
            self.pairings.pop(pair_id, None)
        self._trim_pairings_locked()

    def _trim_pairings_locked(self) -> None:
        while len(self.pairings) > MAX_PAIRING_RECORDS:
            removable = next(
                (
                    pair_id
                    for pair_id, pairing in self.pairings.items()
                    if pairing.state in PAIR_TERMINAL_STATES
                ),
                None,
            )
            if removable is None:
                break
            self.pairings.pop(removable, None)

    def issue_browser_capability(
        self,
        session_id: str,
        socket: Any,
    ) -> BrowserSession:
        existing = self.browser_sessions.get(session_id)
        if existing is not None and existing.socket is socket:
            self.browser_sessions.move_to_end(session_id)
            return existing
        if existing is not None:
            self._remove_browser_session(existing)
        capability = secrets.token_urlsafe(SECRET_BYTES)
        capability_hash = hashlib.sha256(
            capability.encode("ascii")
        ).digest()
        record = BrowserSession(
            session_id=session_id,
            socket=socket,
            capability=capability,
            capability_hash=capability_hash,
            issued_at=time.monotonic(),
        )
        self.browser_sessions[session_id] = record
        self.browser_capability_index[capability_hash] = record
        if (
            self.active_target is not None
            and self.active_target.session_id == session_id
            and self.active_target.socket is not socket
        ):
            self.active_target = None
        while len(self.browser_sessions) > MAX_BROWSER_SESSIONS:
            _old_id, old_record = self.browser_sessions.popitem(last=False)
            self.browser_capability_index.pop(
                old_record.capability_hash,
                None,
            )
            if (
                self.active_target is not None
                and self.active_target.socket is old_record.socket
            ):
                self.active_target = None
        return record

    def browser_for_capability(
        self,
        capability: str,
    ) -> BrowserSession | None:
        if len(capability) > 256:
            return None
        capability_hash = hashlib.sha256(
            capability.encode("utf-8")
        ).digest()
        record = self.browser_capability_index.get(capability_hash)
        if (
            record is None
            or not hmac.compare_digest(capability, record.capability)
        ):
            return None
        _socket, problem = _resolve_exact_socket(
            record.session_id,
            record.socket,
        )
        if problem is not None:
            self._remove_browser_session(record)
            return None
        self.browser_sessions.move_to_end(record.session_id)
        return record

    def invalidate_browser_session(self, record: BrowserSession) -> None:
        if self.browser_sessions.get(record.session_id) is record:
            self._remove_browser_session(record)

    def _remove_browser_session(self, record: BrowserSession) -> None:
        if self.browser_sessions.get(record.session_id) is record:
            self.browser_sessions.pop(record.session_id, None)
        self.browser_capability_index.pop(record.capability_hash, None)
        if (
            self.active_target is not None
            and self.active_target.socket is record.socket
        ):
            self.active_target = None

    def register_target(
        self,
        browser: BrowserSession,
        *,
        node_id: str,
        widget_name: str,
        node_type: str,
        graph_id: str,
    ) -> tuple[Target | None, Target]:
        previous = self.active_target
        current = Target(
            session_id=browser.session_id,
            socket=browser.socket,
            node_id=node_id,
            widget_name=widget_name,
            node_type=node_type,
            graph_id=graph_id,
            registered_at=time.time(),
        )
        self.active_target = current
        return previous, current

    def clear_target_if(self, target: Target) -> None:
        if self.active_target == target:
            self.active_target = None

    def create_pending_ack(
        self,
        delivery_id: str,
        target: Target,
    ) -> PendingAck | None:
        if len(self.pending_acks) >= MAX_PENDING_ACKS:
            return None
        future: asyncio.Future[dict[str, str]] = (
            asyncio.get_running_loop().create_future()
        )
        pending = PendingAck(
            target=target,
            future=future,
        )
        self.pending_acks[delivery_id] = pending
        return pending

    def finish_pending_ack(
        self,
        delivery_id: str,
        expected: PendingAck | None = None,
    ) -> None:
        current = self.pending_acks.get(delivery_id)
        if current is not None and (expected is None or current is expected):
            self.pending_acks.pop(delivery_id, None)

    def resolve_ack(
        self,
        *,
        delivery_id: str,
        browser: BrowserSession,
        status: str,
        detail: str,
    ) -> str:
        pending = self.pending_acks.get(delivery_id)
        if pending is None:
            return "unknown_request"
        if (
            pending.target.session_id != browser.session_id
            or pending.target.socket is not browser.socket
        ):
            return "session_mismatch"
        if pending.future.done():
            return "already_acknowledged"
        pending.future.set_result({"status": status, "detail": detail})
        return "accepted"

    def claim_request_id(
        self,
        client_id: str,
        request_id: str,
        *,
        now: float | None = None,
    ) -> bool:
        timestamp = time.monotonic() if now is None else now
        cutoff = timestamp - self.recent_request_id_ttl_seconds
        while self.recent_request_ids:
            oldest_key, oldest_timestamp = next(
                iter(self.recent_request_ids.items())
            )
            if oldest_timestamp >= cutoff:
                break
            self.recent_request_ids.pop(oldest_key, None)
        key = (client_id, request_id)
        if key in self.recent_request_ids:
            return False
        self.recent_request_ids[key] = timestamp
        self.recent_request_ids.move_to_end(key)
        while len(self.recent_request_ids) > MAX_RECENT_REQUEST_IDS:
            self.recent_request_ids.popitem(last=False)
        return True

    def allow_request(
        self,
        bucket: str,
        *,
        max_requests: int,
        window_seconds: float,
        now: float | None = None,
    ) -> bool:
        timestamp = time.monotonic() if now is None else now
        cutoff = timestamp - window_seconds
        requests = self.rate_limit_clients.get(bucket)
        if requests is None:
            requests = deque()
            self.rate_limit_clients[bucket] = requests
        else:
            self.rate_limit_clients.move_to_end(bucket)
        while requests and requests[0] < cutoff:
            requests.popleft()
        if len(requests) >= max_requests:
            return False
        requests.append(timestamp)
        while len(self.rate_limit_clients) > MAX_RATE_LIMIT_CLIENTS:
            self.rate_limit_clients.popitem(last=False)
        return True


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        http_status: int,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


CLIENT_STORE = ClientStore(STORE_PATH)
BRIDGE = BridgeState(CLIENT_STORE)

if CLIENT_STORE.available:
    migration_note = (
        " Phase 1A.1 shared-token data was removed."
        if CLIENT_STORE.migrated_phase_1_token
        else ""
    )
    print(
        f"[{API_NAME}] Paired-client authentication is ready."
        f"{migration_note}"
    )
else:
    print(
        f"[{API_NAME}] Credential persistence is unavailable; "
        "pairing completion will fail closed."
    )


def _json_ok(
    payload: dict[str, Any],
    *,
    status: int = 200,
) -> web.Response:
    return web.json_response({"ok": True, **payload}, status=status)


def _json_error(
    code: str,
    message: str,
    *,
    status: int,
    request_id: str | None = None,
) -> web.Response:
    payload: dict[str, Any] = {
        "ok": False,
        "status": code,
        "error": {"code": code, "message": message},
    }
    if request_id is not None:
        payload["request_id"] = request_id
    return web.json_response(payload, status=status)


def _client_key(request: web.Request) -> str:
    remote = getattr(request, "remote", None)
    return str(remote) if remote else "unknown"


def _rate_limit_error(
    request: web.Request,
    category: str,
    *,
    max_requests: int = RATE_LIMIT_MAX_REQUESTS,
    window_seconds: float = RATE_LIMIT_WINDOW_SECONDS,
) -> web.Response | None:
    bucket = f"{category}:{_client_key(request)}"
    if BRIDGE.allow_request(
        bucket,
        max_requests=max_requests,
        window_seconds=window_seconds,
    ):
        return None
    return _json_error(
        "rate_limited",
        "Too many bridge requests. Try again shortly.",
        status=429,
    )


def _extract_bearer(request: web.Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    scheme, separator, credential = authorization.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not credential
        or len(credential) > 512
    ):
        return None
    return credential


def _authorize_browser(
    request: web.Request,
) -> tuple[BrowserSession | None, web.Response | None]:
    limited = _rate_limit_error(request, "browser-auth")
    if limited is not None:
        return None, limited
    capability = _extract_bearer(request)
    browser = (
        BRIDGE.browser_for_capability(capability)
        if capability is not None
        else None
    )
    if browser is None:
        return None, _json_error(
            "unauthorized_browser",
            "A current browser-session capability is required.",
            status=401,
        )
    return browser, None


def _revalidate_browser(browser: BrowserSession) -> web.Response | None:
    """Reject a capability if its browser socket was replaced mid-request."""
    current = BRIDGE.browser_for_capability(browser.capability)
    if current is not browser:
        return _json_error(
            "unauthorized_browser",
            "Browser capability is no longer valid.",
            status=401,
        )
    return None


def _authorize_client(
    request: web.Request,
) -> tuple[ClientRecord | None, web.Response | None]:
    limited = _rate_limit_error(request, "client-auth")
    if limited is not None:
        return None, limited
    credential = _extract_bearer(request)
    client = (
        CLIENT_STORE.authenticate(credential)
        if credential is not None
        else None
    )
    if client is None:
        return None, _json_error(
            "unauthorized_client",
            "A valid paired-client credential is required.",
            status=401,
        )
    return client, None


def _socket_collection() -> tuple[Any | None, str | None]:
    instance = getattr(PromptServer, "instance", None)
    sockets = getattr(instance, "sockets", None)
    if sockets is None or not callable(getattr(sockets, "get", None)):
        return None, "compatibility_unavailable"
    return sockets, None


def _resolve_exact_socket(
    session_id: str,
    expected_socket: Any | None = None,
) -> tuple[Any | None, str | None]:
    sockets, problem = _socket_collection()
    if problem is not None:
        return None, problem
    current = sockets.get(session_id)
    if current is None or bool(getattr(current, "closed", False)):
        return None, "stale_session"
    if expected_socket is not None and current is not expected_socket:
        return None, "stale_session"
    if not callable(getattr(current, "send_json", None)):
        return None, "compatibility_unavailable"
    return current, None


async def _send_exact_socket_json(
    session_id: str,
    expected_socket: Any,
    event_name: str,
    data: dict[str, Any],
) -> tuple[bool, str | None]:
    """Send one normal ComfyUI JSON envelope to one proven socket only."""
    socket, problem = _resolve_exact_socket(
        session_id,
        expected_socket,
    )
    if problem is not None:
        return False, problem
    try:
        sender = socket.send_json
        await sender({"type": event_name, "data": data})
    except Exception:
        return False, "delivery_failed"
    return True, None


def _exact_socket_error(
    problem: str,
    *,
    request_id: str | None = None,
) -> web.Response:
    if problem == "compatibility_unavailable":
        return _json_error(
            "compatibility_unavailable",
            "This ComfyUI version does not expose the required "
            "exact-session socket behavior.",
            status=503,
            request_id=request_id,
        )
    if problem == "delivery_failed":
        return _json_error(
            "delivery_failed",
            "The selected browser session could not receive the bridge event.",
            status=502,
            request_id=request_id,
        )
    return _json_error(
        "stale_target",
        "The selected browser target is no longer the current "
        "connected session.",
        status=410,
        request_id=request_id,
    )


async def _read_json_object(request: web.Request) -> dict[str, Any]:
    content_type = request.headers.get("Content-Type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise ApiError(
            "unsupported_media_type",
            "Content-Type must be application/json.",
            415,
        )
    content_length = request.content_length
    if (
        content_length is not None
        and content_length > MAX_REQUEST_BYTES
    ):
        raise ApiError(
            "request_too_large",
            f"JSON request body must not exceed {MAX_REQUEST_BYTES} bytes.",
            413,
        )
    chunks: list[bytes] = []
    bytes_read = 0
    while True:
        chunk = await request.content.read(
            min(64 * 1024, MAX_REQUEST_BYTES + 1 - bytes_read)
        )
        if not chunk:
            break
        chunks.append(chunk)
        bytes_read += len(chunk)
        if bytes_read > MAX_REQUEST_BYTES:
            raise ApiError(
                "request_too_large",
                f"JSON request body must not exceed "
                f"{MAX_REQUEST_BYTES} bytes.",
                413,
            )
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(
            "invalid_json",
            "Request body is not valid UTF-8 JSON.",
            400,
        ) from exc
    if not isinstance(payload, dict):
        raise ApiError(
            "invalid_json",
            "The JSON root must be an object.",
            400,
        )
    return payload


async def _payload_or_error(
    request: web.Request,
) -> tuple[dict[str, Any] | None, web.Response | None]:
    try:
        return await _read_json_object(request), None
    except ApiError as exc:
        return None, _json_error(
            exc.code,
            exc.message,
            status=exc.http_status,
        )


def _required_string(
    payload: dict[str, Any],
    name: str,
    *,
    max_length: int = MAX_IDENTIFIER_LENGTH,
    allow_empty: bool = False,
) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise ApiError(
            "invalid_request",
            f"{name} must be a string.",
            400,
        )
    if not allow_empty and not value.strip():
        raise ApiError(
            "invalid_request",
            f"{name} must not be empty.",
            400,
        )
    if len(value) > max_length:
        raise ApiError(
            "invalid_request",
            f"{name} must not exceed {max_length} characters.",
            400,
        )
    return value


def _decode_urlsafe_secret(
    value: str,
    name: str,
    *,
    exact_bytes: int = SECRET_BYTES,
) -> bytes:
    if not value or len(value) > 256:
        raise ApiError(
            "invalid_request",
            f"{name} is not a valid base64url value.",
            400,
        )
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode(
            (value + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise ApiError(
            "invalid_request",
            f"{name} is not a valid base64url value.",
            400,
        ) from exc
    if len(decoded) != exact_bytes:
        raise ApiError(
            "invalid_request",
            f"{name} must encode exactly {exact_bytes} bytes.",
            400,
        )
    return decoded


def _pair_event_payload(pairing: Pairing) -> dict[str, Any]:
    return {
        "pair_id": pairing.pair_id,
        "verification_code": pairing.verification_code,
        "client_name": pairing.client_name,
        "expires_in": max(
            0,
            int(pairing.expires_at - time.monotonic()),
        ),
    }


async def _send_pair_request_to_browser(
    browser: BrowserSession,
    pairing: Pairing,
) -> bool:
    sent, _problem = await _send_exact_socket_json(
        browser.session_id,
        browser.socket,
        PAIR_REQUEST_EVENT,
        _pair_event_payload(pairing),
    )
    if not sent:
        BRIDGE.invalidate_browser_session(browser)
    return sent


def _usable_browser_sessions() -> list[BrowserSession]:
    usable: list[BrowserSession] = []
    for browser in list(BRIDGE.browser_sessions.values()):
        _socket, problem = _resolve_exact_socket(
            browser.session_id,
            browser.socket,
        )
        if problem is not None:
            BRIDGE.invalidate_browser_session(browser)
            continue
        usable.append(browser)
    return usable


async def _broadcast_pair_event(
    event_name: str,
    data: dict[str, Any],
) -> None:
    for browser in list(BRIDGE.browser_sessions.values()):
        sent, _problem = await _send_exact_socket_json(
            browser.session_id,
            browser.socket,
            event_name,
            data,
        )
        if not sent:
            BRIDGE.invalidate_browser_session(browser)


def _target_matches(left: Target, right: Target) -> bool:
    return (
        left.socket is right.socket
        and left.node_id == right.node_id
        and left.widget_name == right.widget_name
        and left.node_type == right.node_type
        and left.graph_id == right.graph_id
    )


routes = PromptServer.instance.routes


@routes.get(f"{API_PREFIX}/status")
async def status_route(_request: web.Request) -> web.Response:
    target = BRIDGE.active_target
    target_connected = False
    if target is not None:
        _socket, problem = _resolve_exact_socket(
            target.session_id,
            target.socket,
        )
        target_connected = problem is None
    _sockets, compatibility_problem = _socket_collection()
    return _json_ok(
        {
            "status": "ready",
            "name": API_NAME,
            "version": API_VERSION,
            "security": {
                "authentication": "paired_client",
                "pairing": "sha256_challenge_verifier",
                "browser_authorization": "socket_bound_capability",
            },
            "deployment_modes": ["local", "remote_https"],
            "limits": {
                "max_request_bytes": MAX_REQUEST_BYTES,
                "max_text_bytes": MAX_TEXT_BYTES,
                "ack_timeout_seconds": BRIDGE.ack_timeout_seconds,
                "pairing_expires_seconds": int(PAIR_TTL_SECONDS),
            },
            "exact_socket_delivery_available": (
                compatibility_problem is None
            ),
            "persistence_available": CLIENT_STORE.available,
            "target_registered": target is not None,
            "target_session_connected": target_connected,
        }
    )


@routes.post(f"{API_PREFIX}/browser/hello")
async def browser_hello_route(request: web.Request) -> web.Response:
    limited = _rate_limit_error(
        request,
        "browser-hello",
        max_requests=BROWSER_HELLO_RATE_MAX_REQUESTS,
        window_seconds=BROWSER_HELLO_RATE_WINDOW_SECONDS,
    )
    if limited is not None:
        return limited
    payload, error = await _payload_or_error(request)
    if error is not None:
        return error
    assert payload is not None
    try:
        session_id = _required_string(payload, "session_id")
        hello_nonce = _required_string(payload, "hello_nonce")
        _decode_urlsafe_secret(hello_nonce, "hello_nonce")
    except ApiError as exc:
        return _json_error(
            exc.code,
            exc.message,
            status=exc.http_status,
        )

    socket, problem = _resolve_exact_socket(session_id)
    if problem is not None:
        return _exact_socket_error(problem)
    browser = BRIDGE.issue_browser_capability(session_id, socket)
    sent, problem = await _send_exact_socket_json(
        session_id,
        socket,
        BROWSER_CAPABILITY_EVENT,
        {
            "hello_nonce": hello_nonce,
            "capability": browser.capability,
        },
    )
    if not sent:
        BRIDGE.invalidate_browser_session(browser)
        return _exact_socket_error(problem or "delivery_failed")
    for pairing in await BRIDGE.pending_pairings():
        if not await _send_pair_request_to_browser(browser, pairing):
            break
    return _json_ok({"status": "capability_sent"})


@routes.post(f"{API_PREFIX}/pair/start")
async def pair_start_route(request: web.Request) -> web.Response:
    limited = _rate_limit_error(
        request,
        "pair-start",
        max_requests=PAIR_START_RATE_MAX_REQUESTS,
        window_seconds=PAIR_START_RATE_WINDOW_SECONDS,
    )
    if limited is not None:
        return limited
    payload, error = await _payload_or_error(request)
    if error is not None:
        return error
    assert payload is not None
    try:
        challenge_value = _required_string(payload, "challenge")
        challenge = _decode_urlsafe_secret(
            challenge_value,
            "challenge",
        )
        client_name = _required_string(
            payload,
            "client_name",
            max_length=MAX_CLIENT_NAME_LENGTH,
        ).strip()
    except ApiError as exc:
        return _json_error(
            exc.code,
            exc.message,
            status=exc.http_status,
        )

    browsers = _usable_browser_sessions()
    if not browsers:
        return _json_error(
            "no_browser_session",
            "No usable ComfyUI browser session is connected.",
            status=409,
        )

    pairing = await BRIDGE.create_pairing(
        challenge=challenge,
        client_name=client_name,
    )
    if pairing is None:
        return _json_error(
            "pairing_capacity_reached",
            "Too many pairing requests are currently pending.",
            status=429,
        )
    async with pairing.lock:
        delivered = False
        for browser in browsers:
            if await _send_pair_request_to_browser(browser, pairing):
                delivered = True
        if delivered:
            pairing.notification_ready = True
        else:
            pairing.state = PAIR_EXPIRED
            pairing.terminal_at = time.monotonic()
            await BRIDGE.remove_pairing_record(pairing)
    if not delivered:
        return _json_error(
            "no_browser_session",
            "No usable ComfyUI browser session is connected.",
            status=409,
        )
    return _json_ok(
        {
            "status": PAIR_PENDING,
            "pair_id": pairing.pair_id,
            "verification_code": pairing.verification_code,
            "expires_in": int(PAIR_TTL_SECONDS),
        },
        status=201,
    )


@routes.post(f"{API_PREFIX}/pair/decision")
async def pair_decision_route(request: web.Request) -> web.Response:
    browser, auth_error = _authorize_browser(request)
    if auth_error is not None:
        return auth_error
    assert browser is not None
    payload, error = await _payload_or_error(request)
    if error is not None:
        return error
    error = _revalidate_browser(browser)
    if error is not None:
        return error
    assert payload is not None
    try:
        pair_id = _required_string(payload, "pair_id")
        decision = _required_string(
            payload,
            "decision",
            max_length=16,
        ).lower()
    except ApiError as exc:
        return _json_error(
            exc.code,
            exc.message,
            status=exc.http_status,
        )
    if decision not in {"allow", "reject"}:
        return _json_error(
            "invalid_request",
            "decision must be allow or reject.",
            status=400,
        )

    pairing = await BRIDGE.get_pairing(pair_id)
    if pairing is None:
        return _json_error(
            "pairing_unavailable",
            "The pairing request is unavailable.",
            status=404,
        )
    async with pairing.lock:
        error = _revalidate_browser(browser)
        if error is not None:
            return error
        if (
            not await BRIDGE.pairing_is_current(pairing)
            or not pairing.notification_ready
        ):
            return _json_error(
                "pairing_unavailable",
                "The pairing request is unavailable.",
                status=404,
            )
        BRIDGE.expire_pairing_if_needed(pairing)
        if pairing.state != PAIR_PENDING:
            return _json_error(
                "pairing_already_resolved",
                "The pairing request has already been resolved.",
                status=409,
            )
        pairing.state = (
            PAIR_APPROVED if decision == "allow" else PAIR_REJECTED
        )
        if pairing.state == PAIR_REJECTED:
            pairing.terminal_at = time.monotonic()
        resolved_state = pairing.state

    await _broadcast_pair_event(
        PAIR_RESOLVED_EVENT,
        {
            "pair_id": pairing.pair_id,
            "status": resolved_state,
        },
    )
    return _json_ok(
        {
            "status": resolved_state,
            "pair_id": pairing.pair_id,
        }
    )


@routes.post(f"{API_PREFIX}/pair/complete")
async def pair_complete_route(request: web.Request) -> web.Response:
    limited = _rate_limit_error(request, "pair-complete")
    if limited is not None:
        return limited
    payload, error = await _payload_or_error(request)
    if error is not None:
        return error
    assert payload is not None
    try:
        pair_id = _required_string(payload, "pair_id")
        verifier_value = _required_string(payload, "verifier")
        verifier = _decode_urlsafe_secret(
            verifier_value,
            "verifier",
        )
    except ApiError as exc:
        return _json_error(
            exc.code,
            exc.message,
            status=exc.http_status,
        )

    pairing = await BRIDGE.get_pairing(pair_id)
    if pairing is None:
        return _json_error(
            "pairing_unavailable",
            "The pairing request is unavailable.",
            status=404,
        )
    supplied_challenge = hashlib.sha256(verifier).digest()

    async with pairing.lock:
        if (
            not await BRIDGE.pairing_is_current(pairing)
            or not pairing.notification_ready
        ):
            return _json_error(
                "pairing_unavailable",
                "The pairing request is unavailable.",
                status=404,
            )
        if not hmac.compare_digest(
            supplied_challenge,
            pairing.challenge,
        ):
            return _json_error(
                "pairing_unavailable",
                "The pairing request is unavailable.",
                status=404,
            )
        BRIDGE.expire_pairing_if_needed(pairing)
        if pairing.state == PAIR_PENDING:
            return _json_ok(
                {
                    "status": PAIR_PENDING,
                    "pair_id": pairing.pair_id,
                    "retry_after": 1,
                },
                status=202,
            )
        if pairing.state == PAIR_REJECTED:
            return _json_error(
                "pairing_rejected",
                "The pairing request was rejected.",
                status=403,
            )
        if pairing.state == PAIR_EXPIRED:
            return _json_error(
                "pairing_expired",
                "The pairing request expired.",
                status=410,
            )
        if pairing.state == PAIR_CONSUMED:
            return _json_error(
                "pairing_consumed",
                "The pairing request was already consumed.",
                status=409,
            )
        if pairing.state != PAIR_APPROVED:
            return _json_error(
                "pairing_unavailable",
                "The pairing request is unavailable.",
                status=409,
            )

        async with BRIDGE.client_store_lock:
            client_id = secrets.token_urlsafe(CLIENT_ID_BYTES)
            while client_id in CLIENT_STORE.clients:
                client_id = secrets.token_urlsafe(CLIENT_ID_BYTES)
            secret = secrets.token_urlsafe(SECRET_BYTES)
            credential = f"mmh3c1.{client_id}.{secret}"
            record = ClientRecord(
                client_id=client_id,
                client_name=pairing.client_name,
                credential_hash=hashlib.sha256(
                    credential.encode("utf-8")
                ).hexdigest(),
                created_at=time.time(),
            )
            try:
                CLIENT_STORE.add_client(record)
            except Exception:
                return _json_error(
                    "credential_persistence_failed",
                    "The paired credential could not be stored securely.",
                    status=503,
                )
        pairing.state = PAIR_CONSUMED
        pairing.terminal_at = time.monotonic()
        return _json_ok(
            {
                "status": "paired",
                "pair_id": pairing.pair_id,
                "client_id": client_id,
                "client_credential": credential,
            }
        )


@routes.post(f"{API_PREFIX}/register")
async def register_route(request: web.Request) -> web.Response:
    browser, auth_error = _authorize_browser(request)
    if auth_error is not None:
        return auth_error
    assert browser is not None
    payload, error = await _payload_or_error(request)
    if error is not None:
        return error
    error = _revalidate_browser(browser)
    if error is not None:
        return error
    assert payload is not None
    try:
        node_id = _required_string(payload, "node_id")
        widget_name = _required_string(payload, "widget_name")
        node_type = _required_string(payload, "node_type")
        graph_id = _required_string(payload, "graph_id")
    except ApiError as exc:
        return _json_error(
            exc.code,
            exc.message,
            status=exc.http_status,
        )

    previous, current = BRIDGE.register_target(
        browser,
        node_id=node_id,
        widget_name=widget_name,
        node_type=node_type,
        graph_id=graph_id,
    )
    if (
        previous is not None
        and not _target_matches(previous, current)
    ):
        await _send_exact_socket_json(
            previous.session_id,
            previous.socket,
            TARGET_CHANGED_EVENT,
            {
                "active": False,
                "target": previous.public_dict(),
            },
        )
    await _send_exact_socket_json(
        current.session_id,
        current.socket,
        TARGET_CHANGED_EVENT,
        {
            "active": True,
            "target": current.public_dict(),
        },
    )
    return _json_ok(
        {
            "status": "registered",
            "target": current.public_dict(),
            "replaced_existing_target": previous is not None,
        }
    )


@routes.post(f"{API_PREFIX}/send")
async def send_route(request: web.Request) -> web.Response:
    client, auth_error = _authorize_client(request)
    if auth_error is not None:
        return auth_error
    assert client is not None
    payload, error = await _payload_or_error(request)
    if error is not None:
        return error
    assert payload is not None
    try:
        request_id = _required_string(
            payload,
            "request_id",
            max_length=MAX_REQUEST_ID_LENGTH,
        )
        text = _required_string(
            payload,
            "text",
            max_length=MAX_TEXT_BYTES,
            allow_empty=True,
        )
    except ApiError as exc:
        return _json_error(
            exc.code,
            exc.message,
            status=exc.http_status,
        )
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        return _json_error(
            "text_too_large",
            f"text must not exceed {MAX_TEXT_BYTES} UTF-8 bytes.",
            status=413,
            request_id=request_id,
        )
    if not BRIDGE.claim_request_id(
        client.client_id,
        request_id,
    ):
        return _json_error(
            "duplicate_request_id",
            "request_id was already used recently by this client.",
            status=409,
            request_id=request_id,
        )

    target = BRIDGE.active_target
    if target is None:
        return _json_error(
            "no_target",
            "No MMH3 target widget is registered.",
            status=409,
            request_id=request_id,
        )
    _socket, problem = _resolve_exact_socket(
        target.session_id,
        target.socket,
    )
    if problem is not None:
        BRIDGE.clear_target_if(target)
        return _exact_socket_error(
            problem,
            request_id=request_id,
        )

    delivery_id = secrets.token_urlsafe(SECRET_BYTES)
    while delivery_id in BRIDGE.pending_acks:
        delivery_id = secrets.token_urlsafe(SECRET_BYTES)
    pending = BRIDGE.create_pending_ack(delivery_id, target)
    if pending is None:
        return _json_error(
            "bridge_busy",
            "The bridge is handling too many pending deliveries.",
            status=429,
            request_id=request_id,
        )
    try:
        sent, problem = await _send_exact_socket_json(
            target.session_id,
            target.socket,
            SET_TEXT_EVENT,
            {
                "delivery_id": delivery_id,
                "request_id": request_id,
                "text": text,
                "target": target.public_dict(),
            },
        )
        if not sent:
            BRIDGE.clear_target_if(target)
            return _exact_socket_error(
                problem or "delivery_failed",
                request_id=request_id,
            )

        try:
            ack = await asyncio.wait_for(
                pending.future,
                timeout=BRIDGE.ack_timeout_seconds,
            )
        except asyncio.TimeoutError:
            _socket, current_problem = _resolve_exact_socket(
                target.session_id,
                target.socket,
            )
            if current_problem is not None:
                BRIDGE.clear_target_if(target)
                return _exact_socket_error(
                    current_problem,
                    request_id=request_id,
                )
            return _json_error(
                "ack_timeout",
                "The target browser did not acknowledge the "
                "widget update in time.",
                status=504,
                request_id=request_id,
            )
    finally:
        BRIDGE.finish_pending_ack(delivery_id, pending)
        if not pending.future.done():
            pending.future.cancel()

    ack_status = ack["status"]
    detail = ack.get("detail", "")
    if ack_status == "success":
        return _json_ok(
            {
                "status": "success",
                "request_id": request_id,
                "target": target.public_dict(),
            }
        )
    if ack_status in {
        "target_not_found",
        "widget_not_found",
        "invalid_widget",
        "stale_session",
    }:
        BRIDGE.clear_target_if(target)
    http_status = {
        "target_not_found": 409,
        "widget_not_found": 409,
        "invalid_widget": 422,
        "stale_session": 410,
        "internal_error": 500,
    }.get(ack_status, 500)
    message = {
        "target_not_found": (
            "The selected target node is not in the current workflow."
        ),
        "widget_not_found": (
            "The selected widget no longer exists on the target node."
        ),
        "invalid_widget": (
            "The selected widget is no longer STRING-compatible."
        ),
        "stale_session": (
            "The browser session no longer matches the registered target."
        ),
        "internal_error": (
            "The browser could not apply the text update."
        ),
    }.get(
        ack_status,
        "The browser returned an unknown acknowledgement status.",
    )
    if detail:
        message = f"{message} {detail}"
    return _json_error(
        ack_status,
        message,
        status=http_status,
        request_id=request_id,
    )


@routes.post(f"{API_PREFIX}/ack")
async def ack_route(request: web.Request) -> web.Response:
    browser, auth_error = _authorize_browser(request)
    if auth_error is not None:
        return auth_error
    assert browser is not None
    payload, error = await _payload_or_error(request)
    if error is not None:
        return error
    error = _revalidate_browser(browser)
    if error is not None:
        return error
    assert payload is not None
    try:
        delivery_id = _required_string(
            payload,
            "delivery_id",
            max_length=MAX_REQUEST_ID_LENGTH,
        )
        ack_status = _required_string(
            payload,
            "status",
            max_length=64,
        )
        detail_value = payload.get("detail", "")
        if not isinstance(detail_value, str):
            raise ApiError(
                "invalid_request",
                "detail must be a string.",
                400,
            )
        detail = detail_value[:MAX_ACK_DETAIL_LENGTH]
    except ApiError as exc:
        return _json_error(
            exc.code,
            exc.message,
            status=exc.http_status,
        )
    if ack_status not in ACK_STATUSES:
        return _json_error(
            "invalid_request",
            "status is not a supported acknowledgement value.",
            status=400,
        )

    result = BRIDGE.resolve_ack(
        delivery_id=delivery_id,
        browser=browser,
        status=ack_status,
        detail=detail,
    )
    if result == "unknown_request":
        return _json_error(
            "unknown_delivery_id",
            "No pending send request has this delivery_id.",
            status=404,
        )
    if result == "session_mismatch":
        return _json_error(
            "stale_session",
            "ACK capability does not match the targeted browser session.",
            status=409,
        )
    if result == "already_acknowledged":
        return _json_error(
            "duplicate_ack",
            "This delivery has already been acknowledged.",
            status=409,
        )
    return _json_ok(
        {
            "status": "acknowledged",
            "delivery_id": delivery_id,
            "widget_status": ack_status,
        }
    )
