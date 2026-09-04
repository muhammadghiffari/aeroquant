"""Alpaca MCP Server client -- hackathon requirement: MUST use MCP/CLI.

Launches the OFFICIAL `uvx alpaca-mcp-server` (v2, FastMCP) as a stdio
subprocess with READ-ONLY toolsets (news, assets, stock-data) and calls its
tools through the `mcp` Python client. Order execution deliberately stays on
alpaca-py (safety wrapper, idempotency) -- MCP is the agent-facing data layer.

One session per cycle (batched calls), graceful fallback to alpaca-py on any
failure so the pipeline never dies because of MCP.
"""
import asyncio
import json
import logging
import os

import config

log = logging.getLogger(__name__)

_server_params = None


def _params():
    global _server_params
    if _server_params is None:
        from mcp import StdioServerParameters

        env = {
            **os.environ,
            "ALPACA_API_KEY": config.ALPACA_PAPER_API_KEY,
            "ALPACA_SECRET_KEY": config.ALPACA_PAPER_SECRET_KEY,
            "ALPACA_PAPER_TRADE": "true" if config.PAPER_TRADE else "false",
            "ALPACA_TOOLSETS": config.MCP_TOOLSETS,
        }
        _server_params = StdioServerParameters(command="uvx", args=["alpaca-mcp-server"], env=env)
    return _server_params


def _parse_tool_result(result) -> dict:
    """MCP CallToolResult -> dict (best-effort JSON parse of text blocks).

    The official server wraps every payload as
    {"_alpaca_mcp_security": {...}, "data": <actual payload>} -> unwrap `data`.
    """
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"raw": text[:2000]}
        if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, list)):
            payload = payload["data"]
        return payload if isinstance(payload, dict) else {"items": payload}
    return {}


async def _fetch_context(symbols: list[str]) -> dict:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    out: dict = {"mcp_used": True, "clock": None, "news": {}, "quotes": {}}
    async with stdio_client(_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            try:
                clock = _parse_tool_result(await session.call_tool("get_clock", {}))
                out["clock"] = clock
            except Exception as exc:  # noqa: BLE001
                log.warning("MCP get_clock failed: %s", exc)

            for sym in symbols:
                news_args_variants = [{"symbols": sym, "limit": 6}]
                for args in news_args_variants:
                    try:
                        data = _parse_tool_result(
                            await session.call_tool("get_news", args)
                        )
                        items = data.get("news") or data.get("items") or (
                            data if isinstance(data.get("items"), list) else []
                        )
                        out["news"][sym] = [
                            (it.get("headline") or "")[:200] for it in items[:6]
                        ] if isinstance(items, list) else []
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_err = exc
                else:
                    log.warning("MCP get_news failed for %s: %s", sym, last_err)

                try:
                    q = _parse_tool_result(
                        await session.call_tool("get_stock_latest_quote", {"symbols": sym})
                    )
                    if not q:
                        q = _parse_tool_result(
                            await session.call_tool("get_stock_latest_quote", {"symbol_or_symbols": sym})
                        )
                    out["quotes"][sym] = q
                except Exception as exc:  # noqa: BLE001
                    log.debug("MCP quote failed for %s: %s", sym, exc)
    return out


def fetch_cycle_context(symbols: list[str]) -> dict:
    """Batched MCP context for one cycle. Returns {} when disabled/failed."""
    if not config.MCP_ENABLED:
        return {}
    try:
        ctx = asyncio.run(asyncio.wait_for(_fetch_context(symbols), timeout=config.MCP_TIMEOUT_S))
        log.info(
            "MCP context ok: clock=%s news=%d symbols",
            bool(ctx.get("clock")), len(ctx.get("news") or {}),
        )
        return ctx
    except Exception as exc:  # noqa: BLE001
        log.warning("MCP unavailable (%s) -- falling back to alpaca-py", exc)
        return {}


def health_check() -> dict:
    """Tiny connectivity probe used by ops scripts and the dashboard."""
    try:
        ctx = asyncio.run(asyncio.wait_for(_fetch_context(["SPY"]), timeout=config.MCP_TIMEOUT_S))
        return {"ok": bool(ctx.get("mcp_used")), "clock": ctx.get("clock")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
