import asyncio
import base64
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx2 as httpx
from PIL import Image
from mcp.server.mcpserver.exceptions import ToolError
from openai import AsyncOpenAI
from starlette.testclient import TestClient

from server import create_http_app, create_server
from vision_mcp.budget import BudgetError, reserve
from vision_mcp.config import Settings
from vision_mcp.images import ImageError, prepare_image, read_image
from vision_mcp.vision import analyze

TOKEN = "test-token-" + "a" * 32


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.images = self.root / "images"
        self.images.mkdir()
        Image.new("RGB", (2100, 1000), "white").save(self.images / "screen.png")
        self.settings = Settings(self.images, "gpt-4.1-mini", 800, self.root / "usage.db", 20, 3)
        env = patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret-never-log", "VISION_MCP_TOKEN": TOKEN})
        env.start()
        self.addCleanup(env.stop)

    def api(self, status=200, response_status="completed"):
        self.requests = []

        def handler(request):
            self.requests.append(request)
            if status != 200:
                return httpx.Response(status, json={"error": {"message": "test-secret-never-log image-data", "type": "server_error"}})
            return httpx.Response(200, json={
                "id": "resp_test", "object": "response", "created_at": 1,
                "status": response_status, "model": "gpt-4.1-mini",
                "output": [{"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": "エラーコード: E42", "annotations": []}]}],
                "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110,
                          "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}},
            })

        def factory(**kwargs):
            # Exercise the real SDK serializer and HTTP error handling without external calls.
            supplied = kwargs.pop("http_client")
            asyncio.get_running_loop().create_task(supplied.aclose())
            return AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        return patch("vision_mcp.vision.AsyncOpenAI", side_effect=factory)


class ImageTests(Fixture):
    def test_resize_and_strip_metadata(self):
        for detail, edge in (("low", 512), ("high", 2048), ("auto", 2048)):
            url = prepare_image(self.images, "screen.png", detail)
            with Image.open(io.BytesIO(base64.b64decode(url.split(",")[1]))) as image:
                self.assertEqual(image.width, edge)
                self.assertEqual(image.format, "PNG")
                self.assertFalse(image.getexif())

    def test_paths_and_symlinks(self):
        (self.root / "private.png").write_bytes((self.images / "screen.png").read_bytes())
        (self.images / "link.png").symlink_to(self.root / "private.png")
        (self.images / "dirlink").symlink_to(self.root, target_is_directory=True)
        for name in ("../private.png", "/etc/passwd", "C:\\image.png", "https://example.com/a.png",
                     "data:image/png;base64,abc", "link.png", "dirlink/private.png", ".env", "a//b", ""):
            with self.subTest(name=name), self.assertRaises(ImageError):
                read_image(self.images, name)
        (self.images / "nested").mkdir()
        (self.images / "nested" / "正常.png").write_bytes((self.images / "screen.png").read_bytes())
        self.assertTrue(read_image(self.images, "nested/正常.png"))

    def test_corrupt_animation_size_and_special_files(self):
        (self.images / "bad.png").write_text("not an image")
        with self.assertRaises(ImageError):
            prepare_image(self.images, "bad.png", "high")
        Image.new("RGB", (2, 2), "red").save(self.images / "anim.gif", save_all=True,
                                             append_images=[Image.new("RGB", (2, 2), "blue")])
        with self.assertRaises(ImageError):
            prepare_image(self.images, "anim.gif", "high")
        with (self.images / "big.png").open("wb") as stream:
            stream.truncate(10 * 1024 * 1024 + 1)
        os.mkfifo(self.images / "pipe.png")
        for name in ("big.png", "pipe.png"):
            with self.assertRaises(ImageError):
                read_image(self.images, name)
        with patch("vision_mcp.images.MAX_PIXELS", 100):
            with self.assertRaises(ImageError):
                prepare_image(self.images, "screen.png", "high")


class ApiTests(Fixture):
    def test_request_and_output(self):
        with self.api():
            result = asyncio.run(analyze(self.settings, "screen.png", "何と書いてある？", "high"))
        self.assertEqual(result, "エラーコード: E42")
        self.assertEqual(len(self.requests), 1)
        req = self.requests[0]
        self.assertEqual(str(req.url), "https://api.openai.com/v1/responses")
        body = json.loads(req.content)
        self.assertEqual(body["model"], "gpt-4.1-mini")
        self.assertEqual(body["max_output_tokens"], 800)
        self.assertFalse(body["store"])
        self.assertNotIn("tools", body)
        self.assertNotIn("reasoning", body)
        self.assertEqual(body["input"][0]["content"][1]["type"], "input_image")
        self.assertNotIn("screen.png", req.content.decode())

    def test_api_error_no_secret_or_retry(self):
        with self.api(status=500), self.assertRaises(ToolError) as exc:
            asyncio.run(analyze(self.settings, "screen.png", "read", "low"))
        self.assertNotIn("test-secret", str(exc.exception))
        self.assertEqual(len(self.requests), 1)
        with closing(sqlite3.connect(self.settings.usage_db)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM calls").fetchone()[0], 1)

    def test_incomplete_is_marked(self):
        with self.api(response_status="incomplete"):
            result = asyncio.run(analyze(self.settings, "screen.png", "read", "low"))
        self.assertIn("途中", result)
        self.assertIn("E42", result)

    def test_invalid_inputs_never_call_api(self):
        with self.api():
            for image, prompt, detail in (("screen.png", "", "low"), ("screen.png", "a" * 2001, "low"),
                                         ("screen.png", "read", "original"), ("../x", "read", "high")):
                with self.assertRaises(ToolError):
                    asyncio.run(analyze(self.settings, image, prompt, detail))
            self.assertFalse(self.requests)

    def test_budget_store_failure_closed(self):
        with self.api(), self.assertRaises(ToolError):
            asyncio.run(analyze(replace(self.settings, usage_db=self.root / "missing" / "db"),
                                "screen.png", "read", "low"))
        self.assertFalse(self.requests)


class BudgetTests(Fixture):
    def test_atomic_limit(self):
        def attempt(_):
            try:
                reserve(self.settings.usage_db, 20, 3)
                return True
            except BudgetError:
                return False
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(sum(executor.map(attempt, range(12))), 3)

    def test_day_limit_and_window_expiry(self):
        with patch("vision_mcp.budget.time.time", return_value=100000):
            reserve(self.settings.usage_db, 1, 3)
        with patch("vision_mcp.budget.time.time", return_value=101000), self.assertRaises(BudgetError):
            reserve(self.settings.usage_db, 1, 3)
        with patch("vision_mcp.budget.time.time", return_value=200000):
            reserve(self.settings.usage_db, 1, 3)


class HttpTests(Fixture):
    def test_mcp_roundtrip_and_guards(self):
        headers = {"Authorization": "Bearer " + TOKEN, "Accept": "application/json, text/event-stream"}
        with patch.dict(os.environ, {"MCP_ALLOWED_HOSTS": "testserver"}):
            app = create_http_app(create_server(self.settings))
        with self.api(), TestClient(app) as client:
            self.assertEqual(client.post("/mcp", json={}).status_code, 401)
            self.assertEqual(client.post("/mcp", headers={**headers, "Host": "evil.example"}, json={}).status_code, 421)
            self.assertEqual(client.post("/mcp", headers={**headers, "Origin": "https://evil.example"}, json={}).status_code, 403)
            init = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
            self.assertEqual(init.status_code, 200, init.text)
            headers["MCP-Protocol-Version"] = init.json()["result"]["protocolVersion"]
            listing = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()
            self.assertEqual([t["name"] for t in listing["result"]["tools"]], ["analyze_image"])
            result = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "analyze_image", "arguments": {"image": "screen.png"}}}).json()["result"]
            self.assertFalse(result.get("isError"), result)
            self.assertIn("E42", result["content"][0]["text"])
            oversized = client.post("/mcp", headers=headers, json={"data": "x" * 17000})
            self.assertEqual(oversized.status_code, 413)

    def test_missing_auth_configuration_rejected(self):
        with patch.dict(os.environ, {"VISION_MCP_TOKEN": ""}), self.assertRaises(ValueError):
            create_http_app(create_server(self.settings))


if __name__ == "__main__":
    unittest.main()
