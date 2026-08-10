from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
import os
import secrets
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
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
API_VERSION = "1"
API_PREFIX = "/mmh3-bridge/v1"
SET_TEXT_EVENT = "mmh3.bridge.set_text"
TARGET_CHANGED_EVENT = "mmh3.bridge.target_changed"

MAX_REQUEST_BYTES = 256 * 1024
MAX_TEXT_BYTES = 128 * 1024
ACK_TIMEOUT_SECONDS = 3.0
MAX_REQUEST_ID_LENGTH = 128
MAX_IDENTIFIER_LENGTH = 256
MAX_ACK_DETAIL_LENGTH = 512
TOKEN_BYTES = 32
TOKEN_FILE_VERSION = 1
RECENT_REQUEST_ID_TTL_SECONDS = 60.0
MAX_RECENT_REQUEST_IDS = 1024
RATE_LIMIT_WINDOW_SECONDS = 10.0
RATE_LIMIT_MAX_REQUESTS = 120
MAX_RATE_LIMIT_CLIENTS = 512

EXTENSION_DIR = Path(__file__).resolve().parent
DATA_DIR = EXTENSION_DIR / "data"
TOKEN_PATH = DATA_DIR / "bridge.json"

ACK_STATUSES = {
    "success",
    "target_not_found",
    "widget_not_found",
    "invalid_widget",
    "stale_session",
    "internal_error",
}


@dataclass(frozen=True)
class Target:
    session_id: str
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

    def event_dict(self) -> dict[str, Any]:
        return {
            **self.public_dict(),
            "session_id": self.session_id,
        }


@dataclass
class PendingAck:
    session_id: str
    future: asyncio.Future[dict[str, str]]


def _token_is_valid(token: Any) -> bool:
    if not isinstance(token, str) or not token or len(token) > 256:
        return False
    try:
        padding = "=" * (-len(token) % 4)
        raw = base64.b64decode(
            (token + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return False
    return len(raw) >= TOKEN_BYTES


class TokenStore:
    """Load or atomically create the bridge-local Bearer token."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load_or_create(self) -> str:
        token = self._load_valid_token()
        if token is not None:
            self._restrict_permissions()
            return token

        token = secrets.token_urlsafe(TOKEN_BYTES)
        self._write_token(token)
        return token

    def _load_valid_token(self) -> str | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("version") != TOKEN_FILE_VERSION
        ):
            return None
        token = payload.get("token")
        return token if _token_is_valid(token) else None

    def _write_token(self, token: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass

        temporary = self.path.with_name(
            f".{self.path.name}.{secrets.token_hex(8)}.tmp"
        )
        payload = {
            "version": TOKEN_FILE_VERSION,
            "token": token,
            "created_at": time.time(),
        }
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
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


class BridgeState:
    """In-memory Phase 1 state. Nothing in this class writes to disk."""

    def __init__(self, *, ack_timeout_seconds: float = ACK_TIMEOUT_SECONDS) -> None:
        self.active_target: Target | None = None
        self.pending_acks: dict[str, PendingAck] = {}
        self.ack_timeout_seconds = ack_timeout_seconds
        self.recent_request_ids: OrderedDict[str, float] = OrderedDict()
        self.rate_limit_clients: OrderedDict[str, deque[float]] = OrderedDict()
        self.recent_request_id_ttl_seconds = RECENT_REQUEST_ID_TTL_SECONDS
        self.rate_limit_window_seconds = RATE_LIMIT_WINDOW_SECONDS
        self.rate_limit_max_requests = RATE_LIMIT_MAX_REQUESTS

    def register_target(
        self,
        *,
        session_id: str,
        node_id: str,
        widget_name: str,
        node_type: str,
        graph_id: str,
    ) -> tuple[Target | None, Target]:
        previous = self.active_target
        current = Target(
            session_id=session_id,
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
        request_id: str,
        session_id: str,
    ) -> asyncio.Future[dict[str, str]] | None:
        if request_id in self.pending_acks:
            return None
        future: asyncio.Future[dict[str, str]] = (
            asyncio.get_running_loop().create_future()
        )
        self.pending_acks[request_id] = PendingAck(
            session_id=session_id,
            future=future,
        )
        return future

    def finish_pending_ack(self, request_id: str) -> None:
        self.pending_acks.pop(request_id, None)

    def claim_request_id(self, request_id: str, *, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else now
        cutoff = timestamp - self.recent_request_id_ttl_seconds
        while self.recent_request_ids:
            oldest_id, oldest_timestamp = next(iter(self.recent_request_ids.items()))
            if oldest_timestamp >= cutoff:
                break
            self.recent_request_ids.pop(oldest_id, None)

        if request_id in self.recent_request_ids:
            return False
        self.recent_request_ids[request_id] = timestamp
        self.recent_request_ids.move_to_end(request_id)
        while len(self.recent_request_ids) > MAX_RECENT_REQUEST_IDS:
            self.recent_request_ids.popitem(last=False)
        return True

    def allow_request(self, client_key: str, *, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else now
        cutoff = timestamp - self.rate_limit_window_seconds
        requests = self.rate_limit_clients.get(client_key)
        if requests is None:
            requests = deque()
            self.rate_limit_clients[client_key] = requests
        else:
            self.rate_limit_clients.move_to_end(client_key)

        while requests and requests[0] < cutoff:
            requests.popleft()
        if len(requests) >= self.rate_limit_max_requests:
            return False
        requests.append(timestamp)

        while len(self.rate_limit_clients) > MAX_RATE_LIMIT_CLIENTS:
            self.rate_limit_clients.popitem(last=False)
        return True

    def resolve_ack(
        self,
        *,
        request_id: str,
        session_id: str,
        status: str,
        detail: str,
    ) -> str:
        pending = self.pending_acks.get(request_id)
        if pending is None:
            return "unknown_request"
        if pending.session_id != session_id:
            return "session_mismatch"
        if pending.future.done():
            return "already_acknowledged"
        pending.future.set_result({"status": status, "detail": detail})
        return "accepted"


class ApiError(Exception):
    def __init__(self, code: str, message: str, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


BRIDGE_TOKEN = TokenStore(TOKEN_PATH).load_or_create()
BRIDGE = BridgeState()

print(
    f"[{API_NAME}] Bearer authentication is enabled. "
    f"The pairing token is stored in {TOKEN_PATH}. "
    "Use a deliberate local file read to copy it; the token is not printed."
)


def _json_ok(payload: dict[str, Any], *, status: int = 200) -> web.Response:
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


def _authorize_or_error(request: web.Request) -> web.Response | None:
    if not BRIDGE.allow_request(_client_key(request)):
        return _json_error(
            "rate_limited",
            "Too many bridge requests. Try again shortly.",
            status=429,
        )

    authorization = request.headers.get("Authorization", "")
    scheme, separator, supplied_token = authorization.partition(" ")
    valid = (
        bool(separator)
        and scheme.lower() == "bearer"
        and len(supplied_token) <= 256
        and hmac.compare_digest(supplied_token, BRIDGE_TOKEN)
    )
    if not valid:
        return _json_error(
            "unauthorized",
            "A valid MMH3 Prompt Bridge Bearer token is required.",
            status=401,
        )
    return None


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
    if content_length is not None and content_length > MAX_REQUEST_BYTES:
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
                f"JSON request body must not exceed {MAX_REQUEST_BYTES} bytes.",
                413,
            )
    raw = b"".join(chunks)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError("invalid_json", "Request body is not valid UTF-8 JSON.", 400) from exc

    if not isinstance(payload, dict):
        raise ApiError("invalid_json", "The JSON root must be an object.", 400)
    return payload


async def _payload_or_error(
    request: web.Request,
) -> tuple[dict[str, Any] | None, web.Response | None]:
    try:
        return await _read_json_object(request), None
    except ApiError as exc:
        return None, _json_error(exc.code, exc.message, status=exc.http_status)


def _required_string(
    payload: dict[str, Any],
    name: str,
    *,
    max_length: int = MAX_IDENTIFIER_LENGTH,
    allow_empty: bool = False,
) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise ApiError("invalid_request", f"{name} must be a string.", 400)
    if not allow_empty and not value.strip():
        raise ApiError("invalid_request", f"{name} must not be empty.", 400)
    if len(value) > max_length:
        raise ApiError(
            "invalid_request",
            f"{name} must not exceed {max_length} characters.",
            400,
        )
    return value


def _session_connected(session_id: str) -> bool:
    socket = PromptServer.instance.sockets.get(session_id)
    return socket is not None and not bool(getattr(socket, "closed", False))


def _target_matches(left: Target, right: Target) -> bool:
    return (
        left.session_id == right.session_id
        and left.node_id == right.node_id
        and left.widget_name == right.widget_name
        and left.node_type == right.node_type
        and left.graph_id == right.graph_id
    )


routes = PromptServer.instance.routes


@routes.get(f"{API_PREFIX}/status")
async def status_route(_request: web.Request) -> web.Response:
    target = BRIDGE.active_target
    connected = bool(target and _session_connected(target.session_id))
    return _json_ok(
        {
            "status": "ready",
            "name": API_NAME,
            "version": API_VERSION,
            "security": {
                "authentication": "bearer",
                "protected_endpoints": ["register", "send", "ack"],
            },
            "deployment_modes": ["local", "remote"],
            "limits": {
                "max_request_bytes": MAX_REQUEST_BYTES,
                "max_text_bytes": MAX_TEXT_BYTES,
                "ack_timeout_seconds": BRIDGE.ack_timeout_seconds,
            },
            "target_registered": target is not None,
            "target_session_connected": connected,
        }
    )


@routes.post(f"{API_PREFIX}/register")
async def register_route(request: web.Request) -> web.Response:
    authorization_error = _authorize_or_error(request)
    if authorization_error is not None:
        return authorization_error

    payload, error = await _payload_or_error(request)
    if error is not None:
        return error
    assert payload is not None

    try:
        session_id = _required_string(payload, "session_id")
        node_id = _required_string(payload, "node_id")
        widget_name = _required_string(payload, "widget_name")
        node_type = _required_string(payload, "node_type")
        graph_id = _required_string(payload, "graph_id")
    except ApiError as exc:
        return _json_error(exc.code, exc.message, status=exc.http_status)

    if not _session_connected(session_id):
        return _json_error(
            "stale_session",
            "The registering ComfyUI browser session is not connected.",
            status=410,
        )

    previous, current = BRIDGE.register_target(
        session_id=session_id,
        node_id=node_id,
        widget_name=widget_name,
        node_type=node_type,
        graph_id=graph_id,
    )

    if previous is not None and not _target_matches(previous, current):
        if _session_connected(previous.session_id):
            PromptServer.instance.send_sync(
                TARGET_CHANGED_EVENT,
                {"active": False, "target": previous.event_dict()},
                previous.session_id,
            )

    PromptServer.instance.send_sync(
        TARGET_CHANGED_EVENT,
        {"active": True, "target": current.event_dict()},
        current.session_id,
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
    authorization_error = _authorize_or_error(request)
    if authorization_error is not None:
        return authorization_error

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
        return _json_error(exc.code, exc.message, status=exc.http_status)

    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        return _json_error(
            "text_too_large",
            f"text must not exceed {MAX_TEXT_BYTES} UTF-8 bytes.",
            status=413,
            request_id=request_id,
        )

    if not BRIDGE.claim_request_id(request_id):
        return _json_error(
            "duplicate_request_id",
            "request_id was already used recently.",
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

    if not _session_connected(target.session_id):
        BRIDGE.clear_target_if(target)
        return _json_error(
            "stale_session",
            "The target ComfyUI browser session is no longer connected.",
            status=410,
            request_id=request_id,
        )

    future = BRIDGE.create_pending_ack(request_id, target.session_id)
    if future is None:
        return _json_error(
            "duplicate_request_id",
            "request_id is already awaiting acknowledgement.",
            status=409,
            request_id=request_id,
        )

    try:
        PromptServer.instance.send_sync(
            SET_TEXT_EVENT,
            {
                "request_id": request_id,
                "text": text,
                "target": target.event_dict(),
            },
            target.session_id,
        )
        ack = await asyncio.wait_for(
            future,
            timeout=BRIDGE.ack_timeout_seconds,
        )
    except asyncio.TimeoutError:
        if not _session_connected(target.session_id):
            BRIDGE.clear_target_if(target)
            return _json_error(
                "stale_session",
                "The target browser session disconnected before acknowledgement.",
                status=410,
                request_id=request_id,
            )
        return _json_error(
            "ack_timeout",
            "The target browser did not acknowledge the widget update in time.",
            status=504,
            request_id=request_id,
        )
    except Exception:
        return _json_error(
            "internal_error",
            "The bridge could not dispatch the text update.",
            status=500,
            request_id=request_id,
        )
    finally:
        BRIDGE.finish_pending_ack(request_id)

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
        "target_not_found": "The selected target node is not in the current workflow.",
        "widget_not_found": "The selected widget no longer exists on the target node.",
        "invalid_widget": "The selected widget is no longer STRING-compatible.",
        "stale_session": "The browser session no longer matches the registered target.",
        "internal_error": "The browser could not apply the text update.",
    }.get(ack_status, "The browser returned an unknown acknowledgement status.")
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
    authorization_error = _authorize_or_error(request)
    if authorization_error is not None:
        return authorization_error

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
        session_id = _required_string(payload, "session_id")
        ack_status = _required_string(payload, "status", max_length=64)
        detail_value = payload.get("detail", "")
        if not isinstance(detail_value, str):
            raise ApiError("invalid_request", "detail must be a string.", 400)
        detail = detail_value[:MAX_ACK_DETAIL_LENGTH]
    except ApiError as exc:
        return _json_error(exc.code, exc.message, status=exc.http_status)

    if ack_status not in ACK_STATUSES:
        return _json_error(
            "invalid_request",
            "status is not a supported acknowledgement value.",
            status=400,
            request_id=request_id,
        )

    result = BRIDGE.resolve_ack(
        request_id=request_id,
        session_id=session_id,
        status=ack_status,
        detail=detail,
    )
    if result == "unknown_request":
        return _json_error(
            "unknown_request_id",
            "No pending send request has this request_id.",
            status=404,
            request_id=request_id,
        )
    if result == "session_mismatch":
        return _json_error(
            "stale_session",
            "ACK session does not match the targeted browser session.",
            status=409,
            request_id=request_id,
        )
    if result == "already_acknowledged":
        return _json_error(
            "duplicate_ack",
            "This request has already been acknowledged.",
            status=409,
            request_id=request_id,
        )

    return _json_ok(
        {
            "status": "acknowledged",
            "request_id": request_id,
            "widget_status": ack_status,
        }
    )
