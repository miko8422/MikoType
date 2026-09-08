"""Real loopback checks on OS-assigned ports; no camera or production server."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

import pytest

from deskvision.web.binding import (
    SERVICE_SCHEMA_VERSION,
    BoundEndpoint,
    ServicePortInUseError,
    _listener,
    discover_mikotype_services,
    reserve_loopback_endpoint,
)


pytestmark = pytest.mark.integration


def test_real_listener_remains_exclusive_until_closed() -> None:
    listener = _listener("127.0.0.1", 0)
    port = listener.getsockname()[1]
    with BoundEndpoint("127.0.0.1", port, port, listener):
        with pytest.raises(ServicePortInUseError):
            reserve_loopback_endpoint("127.0.0.1", port)
    with reserve_loopback_endpoint("127.0.0.1", port) as endpoint:
        assert endpoint.port == port


@pytest.mark.parametrize("service", ["Pimax Client", "MikoType"])
def test_real_discovery_identifies_service_without_proxy(monkeypatch, service) -> None:
    payload = {"service": service, "schema_version": SERVICE_SCHEMA_VERSION}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            port = server.server_port
            expected = {port: payload} if service == "MikoType" else {}
            assert discover_mikotype_services("127.0.0.1", [port]) == expected
            assert thread.is_alive()
        finally:
            server.shutdown()
            thread.join(timeout=2)
        assert not thread.is_alive()
