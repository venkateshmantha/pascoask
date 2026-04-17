"""
MCP server for PascoAsk.

Transports:
  stdio  — for Claude Desktop (run: python -m mcp_server.server)
  HTTP   — for web UI (run: python -m mcp_server.server --http --port 8001)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure project root on path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

import mcp_server.tools as pasco_tools

logger = logging.getLogger(__name__)

server = Server("pascoask")

TOOLS: list[Tool] = [
    Tool(
        name="search_pasco_records",
        description=(
            "Search Pasco County government records using natural language. "
            "Returns relevant chunks from meeting minutes, ordinances, staff reports, and zoning data."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "doc_type": {
                    "type": "string",
                    "enum": ["meeting_minutes", "ordinance", "zoning_code", "staff_report"],
                    "description": "Filter by document type",
                },
                "date_from": {"type": "string", "description": "ISO 8601 start date filter"},
                "date_to": {"type": "string", "description": "ISO 8601 end date filter"},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="get_zoning_rules",
        description="Look up rules and permitted uses for a specific zoning code (e.g. MPUD, R2, C1).",
        inputSchema={
            "type": "object",
            "properties": {
                "zoning_code": {"type": "string", "description": "Zoning code to look up"},
            },
            "required": ["zoning_code"],
        },
    ),
    Tool(
        name="get_meeting_item",
        description="Retrieve a specific Board of County Commissioners agenda item and its vote result.",
        inputSchema={
            "type": "object",
            "properties": {
                "meeting_date": {"type": "string", "description": "Meeting date in YYYY-MM-DD format"},
                "item_number": {"type": "integer", "description": "Agenda item number"},
            },
            "required": ["meeting_date", "item_number"],
        },
    ),
    Tool(
        name="find_commissioner_votes",
        description="Find how commissioners voted on a topic, optionally filtered by commissioner name.",
        inputSchema={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic to search for votes on"},
                "commissioner": {"type": "string", "description": "Optional commissioner name filter"},
            },
            "required": ["topic"],
        },
    ),
    Tool(
        name="summarize_topic",
        description="Summarize all county decisions and discussions about a topic across time.",
        inputSchema={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic to summarize"},
                "date_range": {
                    "type": "string",
                    "description": "Optional date range e.g. '2023-01-01 to 2024-12-31'",
                },
            },
            "required": ["topic"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "search_pasco_records":
            result = pasco_tools.search_pasco_records(**arguments)
        elif name == "get_zoning_rules":
            result = pasco_tools.get_zoning_rules(**arguments)
        elif name == "get_meeting_item":
            result = pasco_tools.get_meeting_item(**arguments)
        elif name == "find_commissioner_votes":
            result = pasco_tools.find_commissioner_votes(**arguments)
        elif name == "summarize_topic":
            result = pasco_tools.summarize_topic(**arguments)
        else:
            result = {"error": f"Unknown tool: {name}"}
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]


async def _run_stdio() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


async def _run_http(port: int) -> None:
    """HTTP/SSE transport for web UI."""
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    import uvicorn

    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ]
    )
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server_instance = uvicorn.Server(config)
    await server_instance.serve()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="PascoAsk MCP server")
    parser.add_argument("--http", action="store_true", help="Run HTTP/SSE transport instead of stdio")
    parser.add_argument("--port", type=int, default=8001, help="HTTP port (default 8001)")
    args = parser.parse_args()

    import asyncio
    if args.http:
        asyncio.run(_run_http(args.port))
    else:
        asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
