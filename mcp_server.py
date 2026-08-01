#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from meeter.mcp_access import (
    McpPrivacyConfig,
    MeetingContextService,
    PrivacyLevel,
    ReadOnlyMeetingStore,
)


def _boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("Expected true or false")


def _meeting_ids(value: str) -> frozenset[str] | None:
    ids = frozenset(item.strip() for item in value.split(",") if item.strip())
    if not ids:
        return None
    invalid = [item for item in ids if not ReadOnlyMeetingStore._valid_id(item)]
    if invalid:
        raise argparse.ArgumentTypeError(f"Invalid meeting id: {invalid[0]}")
    return ids


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Meeter's local, read-only MCP server")
    parser.add_argument("--data-dir", type=Path, default=None, help="Meeter data directory")
    parser.add_argument(
        "--privacy",
        choices=("insights", "excerpts", "full"),
        default=os.environ.get("MEETER_MCP_PRIVACY", "insights"),
        help="Maximum data the MCP server may reveal (default: insights)",
    )
    parser.add_argument(
        "--redact-pii",
        type=_boolean,
        default=_boolean(os.environ.get("MEETER_MCP_REDACT_PII", "true")),
        help="Redact email addresses and phone numbers (default: true)",
    )
    parser.add_argument(
        "--allowed-meetings",
        type=_meeting_ids,
        default=_meeting_ids(os.environ.get("MEETER_MCP_ALLOWED_MEETINGS", "")),
        help="Optional comma-separated allowlist of meeting IDs",
    )
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", choices=("127.0.0.1",), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MEETER_MCP_PORT", "4318")))
    parser.add_argument("--parent-pid", type=int, default=None, help=argparse.SUPPRESS)
    return parser


def create_mcp_server(service: MeetingContextService, *, host: str = "127.0.0.1", port: int = 4318) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.transport_security import TransportSecuritySettings
    except ImportError as exc:
        raise RuntimeError(
            "The MCP SDK is not installed. Run: python3 -m pip install -r requirements-mcp.txt"
        ) from exc

    server = FastMCP(
        "Meeter",
        instructions=(
            "Read-only access to locally stored Meeter insights. "
            "Use returned meeting IDs for follow-up calls. Respect the privacy metadata in every result. "
            "This server cannot create, update, or delete meetings, actions, speakers, or files."
        ),
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[f"127.0.0.1:{port}", f"localhost:{port}"],
            allowed_origins=[f"http://127.0.0.1:{port}", f"http://localhost:{port}"],
        ),
    )

    @server.tool()
    def list_meetings(
        query: str = "",
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List permitted meetings. Query matches titles and participant names; dates use ISO format."""
        return service.list_meetings(query, date_from, date_to, limit)

    @server.tool()
    def get_meeting_insights(meeting_id: str) -> dict[str, Any]:
        """Get summary, decisions, risks, discussion, participants, and actions; never returns transcript."""
        return service.get_meeting_insights(meeting_id)

    @server.tool()
    def search_meetings(query: str, limit: int = 10) -> dict[str, Any]:
        """Search permitted meeting insights and, only when enabled, matching transcript excerpts."""
        return service.search_meetings(query, limit)

    @server.tool()
    def get_action_items(
        owner: str = "",
        include_completed: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Collect action items across permitted meetings, optionally filtered by owner."""
        return service.get_action_items(owner, include_completed, limit)

    if service.config.level >= PrivacyLevel.EXCERPTS:

        @server.tool()
        def get_transcript_excerpts(
            meeting_id: str,
            query: str,
            limit: int = 5,
        ) -> dict[str, Any]:
            """Return only transcript turns matching a required query."""
            return service.get_transcript_excerpts(meeting_id, query, limit)

    if service.config.level >= PrivacyLevel.FULL:

        @server.tool()
        def get_meeting_transcript(meeting_id: str) -> dict[str, Any]:
            """Return the complete text transcript for one permitted meeting."""
            return service.get_meeting_transcript(meeting_id)

    return server


def main() -> None:
    args = _parser().parse_args()
    config = McpPrivacyConfig(
        level=PrivacyLevel.parse(args.privacy),
        redact_pii=args.redact_pii,
        allowed_meeting_ids=args.allowed_meetings,
    )
    service = MeetingContextService(ReadOnlyMeetingStore(args.data_dir), config)
    try:
        server = create_mcp_server(service, host=args.host, port=args.port)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    if args.parent_pid:
        def parent_watchdog() -> None:
            while True:
                try:
                    os.kill(args.parent_pid, 0)
                except OSError:
                    os._exit(0)
                time.sleep(1)
        threading.Thread(target=parent_watchdog, daemon=True).start()
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
