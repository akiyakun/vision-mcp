"""Optional live HTTP compatibility test against a caller-supplied Strata checkout.
STRATA_SOURCE=/path/to/Strata python -m unittest discover -s tests -v
No Strata engine and no OpenAI API calls are required.
"""
import importlib
import asyncio
import os
import socket
import sys
import threading
import time
import unittest
from types import SimpleNamespace

import uvicorn

from test_vision import Fixture, TOKEN
from server import create_http_app, create_server
from check_mcp import check


@unittest.skipUnless(os.environ.get("STRATA_SOURCE"), "STRATA_SOURCE is not set")
class StrataCompatibility(Fixture):
    def test_real_strata_http_client(self):
        sys.path.insert(0, os.environ["STRATA_SOURCE"])
        self.addCleanup(sys.path.pop, 0)
        strata = importlib.import_module("serve.mcp")
        sock = socket.socket()
        self.addCleanup(sock.close)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        app = create_http_app(create_server(self.settings))
        service = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        thread = threading.Thread(target=service.run, kwargs={"sockets": [sock]}, daemon=True)
        with self.api():
            thread.start()
            try:
                deadline = time.monotonic() + 5
                while not service.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(service.started)
                asyncio.run(check(SimpleNamespace(url=f"http://127.0.0.1:{port}/mcp", image=None)))
                self.assertFalse(self.requests)
                client = strata.McpServer("vision-mcp", {
                    "url": f"http://127.0.0.1:{port}/mcp",
                    "headers": {"Authorization": "Bearer " + TOKEN},
                }, strata.DEFAULTS)
                try:
                    self.assertTrue(client.start(), client.error)
                    self.assertEqual([t["name"] for t in client.tools], ["analyze_image"])
                    result = client.call("analyze_image", {"image": "screen.png", "detail": "low"}, 10)
                    self.assertFalse(result.get("isError"), result)
                    self.assertIn("E42", strata.result_text(result))
                finally:
                    client.close()
            finally:
                service.should_exit = True
                thread.join(5)
                sock.close()
