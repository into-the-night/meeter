import asyncio
import json
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

try:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
except ImportError:  # pragma: no cover
    httpx = ClientSession = streamable_http_client = None


@unittest.skipIf(ClientSession is None, "MCP SDK is not installed")
class McpHttpProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        self.process = subprocess.Popen([
            str(Path(__file__).resolve().parents[1] / ".venv/bin/python"),
            str(Path(__file__).resolve().parents[1] / "mcp_server.py"),
            "--transport", "streamable-http", "--port", str(self.port),
            "--data-dir", self.temp.name,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            try:
                urllib.request.urlopen(self.url, timeout=.2)
            except urllib.error.HTTPError:
                break
            except OSError:
                time.sleep(.05)

    def tearDown(self):
        self.process.terminate()
        self.process.wait(timeout=5)
        self.temp.cleanup()

    def test_streamable_http_and_dns_rebinding_guards(self):
        async def exercise():
            async with streamable_http_client(self.url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertIn("list_meetings", {tool.name for tool in tools.tools})
            async with httpx.AsyncClient() as client:
                bad_host = await client.post(self.url, headers={"Host": "attacker.example"}, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
                self.assertIn(bad_host.status_code, {400, 403, 421})
                bad_origin = await client.post(self.url, headers={"Origin": "https://attacker.example"}, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
                self.assertIn(bad_origin.status_code, {400, 403})
                self.assertNotIn("access-control-allow-origin", bad_origin.headers)
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
