import argparse
import asyncio
import hmac
import logging
import os
from ipaddress import ip_address
from typing import Literal

import uvicorn
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse

from vision_mcp.config import Settings, integer
from vision_mcp.vision import DEFAULT_PROMPT, analyze


def create_server(settings: Settings) -> MCPServer:
    mcp = MCPServer("vision-mcp", version="1.0.0")
    busy = asyncio.Lock()

    @mcp.tool()
    async def analyze_image(image: str, prompt: str = DEFAULT_PROMPT,
                            detail: Literal["low", "high", "auto"] = "high") -> str:
        """画像専用NAS共有内の画像をOpenAIへ送り、見える事実・文字を簡潔に返す。
        imageは共有内の相対名（例: error.png, screenshots/ui.png）。Windows絶対パス・URL・base64は不可。
        ユーザーが指定した画像だけを送る。最終判断はStrataが行う。
        detail: highは文字/UI用（長辺最大2048px）、lowは概要用（512px）、autoはAPIに委任。
        1回1画像。失敗や不足時に自動で繰り返し呼び出さない。
        """
        if busy.locked():
            raise ToolError("別の画像を解析中です。この依頼で自動再試行しないでください。")
        async with busy:
            return await analyze(settings, image, prompt, detail)

    return mcp


class BearerAuth:
    def __init__(self, app, token: str):
        self.app, self.expected = app, ("Bearer " + token).encode("ascii")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            supplied = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(supplied, self.expected):
                await PlainTextResponse("Unauthorized", status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_http_app(mcp: MCPServer):
    token = os.environ.get("VISION_MCP_TOKEN", "")
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError("VISION_MCP_TOKENに空白を含まない32文字以上のASCII文字列を設定してください。")
    hosts = [x.strip() for x in os.environ.get(
        "MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*,[::1]:*"
    ).split(",") if x.strip()]
    if "*" in hosts:
        raise ValueError("MCP_ALLOWED_HOSTSで全ホスト許可は指定できません。")
    server_ip = os.environ.get("SERVER_IP", "").strip()
    if server_ip:
        addr = ip_address(server_ip)
        hosts.append(f"[{addr}]:*" if addr.version == 6 else f"{addr}:*")
    app = mcp.streamable_http_app(
        stateless_http=True, json_response=True, max_request_body_size=16 * 1024,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts),
    )
    return BearerAuth(app, token)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    # SDK diagnostics may contain request data. Only our aggregate usage log is needed.
    for name in ("openai", "httpx", "httpcore", "mcp"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    mcp = create_server(Settings.from_env())
    if args.http:
        uvicorn.run(create_http_app(mcp), host=os.environ.get("MCP_HOST", "0.0.0.0"),
                    port=integer("MCP_PORT", 8001, 1, 65535), access_log=False)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
