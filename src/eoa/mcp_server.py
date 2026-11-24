"""MCP server for the energy operations tools (Model Context Protocol, official Python SDK v2).

Any MCP client (a chat application, an IDE, an agent framework) can list and call the same validated
tools that the agent in agent.py uses, read the runbooks as resources, and ask the server to check an
answer: `verify_answer` compares every number in a draft answer with the results of the tool calls made
in this session and names the numbers no tool returned. Tools never write or send anything.

Run:  python -m eoa.mcp_server                      (stdio, for a local client)
      python -m eoa.mcp_server --http --port 8765   (streamable HTTP)
"""
from __future__ import annotations

import argparse
from collections import deque

from mcp.server.mcpserver import MCPServer

from . import tools as T
from .agent import Agent, check_grounded

INSTRUCTIONS = (
    "Tools for German day-ahead power data 2023-2025 (SMARD) and an operations knowledge base. "
    "Answer only with numbers returned by a tool; there is no forecast tool and the data end on 2025-12-31. "
    "Before presenting an answer, call verify_answer with the draft text. Customer notices are drafts that need approval."
)

server = MCPServer("energy-ops-agent", instructions=INSTRUCTIONS)
_agent = Agent()
_calls: deque = deque(maxlen=200)   # (tool, args, result) of this server process


def _run(name: str, args: dict) -> dict:
    res = _agent.execute(name, args)
    _calls.append((name, args, res))
    return res


@server.tool()
def daily_summary(day: str) -> dict:
    """Price, wind, solar and residual load of one day (day as YYYY-MM-DD)."""
    return _run("daily_summary", {"day": day})


@server.tool()
def data_quality(day: str) -> dict:
    """Completeness and plausibility checks for one day (day as YYYY-MM-DD)."""
    return _run("data_quality", {"day": day})


@server.tool()
def price_anomalies(day: str) -> dict:
    """Hours with unusual prices against the same hour of the previous 28 days (day as YYYY-MM-DD)."""
    return _run("price_anomalies", {"day": day})


@server.tool()
def compare_days(day_a: str, day_b: str) -> dict:
    """Two daily summaries side by side (dates as YYYY-MM-DD)."""
    return _run("compare_days", {"day_a": day_a, "day_b": day_b})


@server.tool()
def wind_price_slope(start: str, end: str) -> dict:
    """Price change per GW of residual load over a period (dates as YYYY-MM-DD); a descriptive number."""
    return _run("wind_price_slope", {"start": start, "end": end})


@server.tool()
def search_kb(query: str) -> dict:
    """Passages from the operations knowledge base (definitions, alert rules, fault handling), with sources."""
    return _run("search_kb", {"query": query})


@server.tool()
def draft_notice(day: str) -> dict:
    """Draft customer notice for a day, built only from tool results; needs_approval is always true."""
    return _run("draft_notice", {"day": day})


@server.tool()
def verify_answer(answer: str) -> dict:
    """Check a draft answer: numbers that no tool call of this session returned (as written or rounded)."""
    bad = check_grounded(answer, list(_calls))
    return {"grounded": not bad, "ungrounded_numbers": bad, "tool_calls_in_session": len(_calls)}


@server.tool()
def reset_session() -> dict:
    """Forget the tool calls logged for verify_answer."""
    n = len(_calls)
    _calls.clear()
    return {"cleared": n}


@server.resource("kb://{name}")
def runbook(name: str) -> str:
    """One runbook of the knowledge base (names as returned by search_kb sources, e.g. 01_residual_load)."""
    path = T.KB / f"{name}.md"
    if not path.is_file() or path.parent != T.KB:
        raise ValueError(f"unknown runbook {name}")
    return path.read_text(encoding="utf-8")


@server.prompt()
def grounded_report(day: str) -> str:
    """Instruction for a daily report that stays inside the tool results."""
    return (f"Write a short report for {day}. Call daily_summary, data_quality and price_anomalies for that day, "
            "use only their numbers, then call verify_answer with your text and fix anything it flags. "
            "If a tool returns an error, say that the day has no data and stop.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="streamable HTTP instead of stdio")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    if a.http:
        server.settings.port = a.port
        server.run(transport="streamable-http")
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
