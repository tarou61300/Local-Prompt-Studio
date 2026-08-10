from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class MockLlamaServer(ThreadingHTTPServer):
    delay: float = 0.0
    response_text: str | None = None


class MockHandler(BaseHTTPRequestHandler):
    server: MockLlamaServer

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/v1/chat/completions":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload: dict[str, Any] = json.loads(self.rfile.read(length).decode("utf-8"))
            messages = payload["messages"]
        except (ValueError, KeyError, json.JSONDecodeError):
            self._json(400, {"error": "invalid request"})
            return
        if self.server.delay:
            time.sleep(self.server.delay)
        user_text = next(
            (str(item.get("content", "")) for item in reversed(messages) if item.get("role") == "user"),
            "",
        )
        dialogue = " ".join(re.findall(r"「[^」]+」", user_text))
        content = self.server.response_text or (
            "<think>This private mock reasoning must be removed.</think>\n"
            "A 10-second single continuous cinematic shot follows the requested subject and action "
            "with natural motion, coherent timing, and matching environmental sound."
            + (f" The dialogue remains exactly: {dialogue}" if dialogue else "")
        )
        self._json(
            200,
            {
                "id": "mock-mmh3",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            },
        )

    def _json(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def log_message(self, format: str, *args: object) -> None:
        # Deliberately avoid logging request bodies in the development mock.
        return


def start_mock_server(
    *, delay: float = 0.0, response_text: str | None = None
) -> tuple[MockLlamaServer, str]:
    server = MockLlamaServer(("127.0.0.1", 0), MockHandler)
    server.delay = delay
    server.response_text = response_text
    thread = threading.Thread(target=server.serve_forever, name="mmh3-mock-server", daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


if __name__ == "__main__":
    server, url = start_mock_server()
    print(f"MMH3 mock server: {url}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()
        server.server_close()
