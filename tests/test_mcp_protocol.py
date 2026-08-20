import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    MCP_INSTALLED = True
except ImportError:
    MCP_INSTALLED = False

from meeter.demo import demo_meeting
from meeter.storage import LocalStore


@unittest.skipUnless(MCP_INSTALLED, "MCP SDK is an optional dependency")
class McpProtocolTests(unittest.TestCase):
    def test_stdio_tools_follow_the_process_privacy_level(self):
        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as directory:
                meeting = demo_meeting().to_dict()
                LocalStore(Path(directory)).save_meeting(meeting)
                base_args = [
                    str(Path(__file__).resolve().parents[1] / "mcp_server.py"),
                    "--data-dir",
                    directory,
                ]

                insight_tools, result = await self._connect(base_args, meeting["id"])
                excerpt_tools, _ = await self._connect(
                    [*base_args, "--privacy", "excerpts"], meeting["id"]
                )
                full_tools, _ = await self._connect(
                    [*base_args, "--privacy", "full"], meeting["id"]
                )

                self.assertEqual(
                    insight_tools,
                    {
                        "list_meetings",
                        "get_meeting_insights",
                        "search_meetings",
                        "get_action_items",
                    },
                )
                self.assertIn("get_transcript_excerpts", excerpt_tools)
                self.assertNotIn("get_meeting_transcript", excerpt_tools)
                self.assertIn("get_meeting_transcript", full_tools)
                self.assertIn("get_speaker_transcript", full_tools)
                self.assertFalse(result.isError)
                self.assertNotIn("transcript", result.structuredContent["meeting"])

        asyncio.run(exercise())

    async def _connect(self, args: list[str], meeting_id: str):
        parameters = StdioServerParameters(command=sys.executable, args=args)
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool(
                    "get_meeting_insights", {"meeting_id": meeting_id}
                )
                return {tool.name for tool in tools.tools}, result


if __name__ == "__main__":
    unittest.main()
