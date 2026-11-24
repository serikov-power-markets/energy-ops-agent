import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402
from mcp import ClientSession  # noqa: E402

from eoa import mcp_server as S  # noqa: E402
from eoa import tools as T  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TOOLS = {"daily_summary", "data_quality", "price_anomalies", "compare_days", "wind_price_slope", "search_kb",
         "draft_notice", "verify_answer", "reset_session"}


def run(coro):
    return asyncio.run(coro)


def payload(result):
    return json.loads(result.content[0].text)


async def _session(fn):
    async with Client(S.server) as c:
        return await fn(c)


def test_lists_tools_with_required_arguments():
    async def go(c):
        return (await c.list_tools()).tools
    tools = {t.name: t for t in run(_session(go))}
    assert set(tools) == TOOLS
    assert tools["compare_days"].input_schema["required"] == ["day_a", "day_b"]
    assert tools["verify_answer"].input_schema["required"] == ["answer"]


def test_tool_result_equals_direct_call():
    async def go(c):
        return payload(await c.call_tool("daily_summary", {"day": "2024-12-12"}))
    assert run(_session(go)) == json.loads(json.dumps(T.daily_summary("2024-12-12")))


def test_bad_date_is_an_error_result_not_a_crash():
    async def go(c):
        return payload(await c.call_tool("daily_summary", {"day": "yesterday"}))
    assert "error" in run(_session(go))


def test_verify_answer_flags_invented_numbers_only():
    async def go(c):
        await c.call_tool("reset_session", {})
        await c.call_tool("daily_summary", {"day": "2024-12-12"})
        ok = payload(await c.call_tool("verify_answer", {"answer": "Wind averaged 1453 MW."}))
        bad = payload(await c.call_tool("verify_answer", {"answer": "Wind averaged 1453 MW and the price will be 77777."}))
        return ok, bad
    ok, bad = run(_session(go))
    assert ok["grounded"] and ok["ungrounded_numbers"] == []
    assert not bad["grounded"] and bad["ungrounded_numbers"] == ["77777"]


def test_verify_answer_without_calls_flags_every_number():
    async def go(c):
        await c.call_tool("reset_session", {})
        return payload(await c.call_tool("verify_answer", {"answer": "The price was 120 EUR/MWh."}))
    assert run(_session(go))["ungrounded_numbers"] == ["120"]


def test_runbook_resource_and_unknown_name():
    async def go(c):
        text = (await c.read_resource("kb://01_residual_load")).contents[0].text
        try:
            await c.read_resource("kb://does_not_exist")
            err = False
        except Exception:
            err = True
        return text, err
    text, err = run(_session(go))
    assert "residual" in text.lower() and err


def test_prompt_is_listed():
    async def go(c):
        return [p.name for p in (await c.list_prompts()).prompts]
    assert run(_session(go)) == ["grounded_report"]


def test_stdio_end_to_end():
    """The server started as a separate process speaks the protocol over stdio."""
    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "eoa.mcp_server"],
                                       env={"PYTHONPATH": str(ROOT / "src"), "PATH": "", "SYSTEMROOT": "C:\\Windows"}, cwd=str(ROOT))
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                res = await s.call_tool("data_quality", {"day": "2024-06-01"})
                return names, json.loads(res.content[0].text)
    names, dq = run(go())
    assert names == TOOLS and dq["flags"] == []
