"""MCP protocol handling is pure JSON-RPC: tested without a database."""
from app import mcp


def test_initialize_and_tools_list():
    r = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r["result"]["protocolVersion"] and r["result"]["capabilities"]["tools"] is not None
    names = {t["name"] for t in mcp.handle({"id": 2, "method": "tools/list"})["result"]["tools"]}
    assert names == {"search_leads", "get_lead", "explain_fit", "pipeline_stats"}


def test_all_tools_are_read_only_by_name_and_schema():
    for t in mcp.TOOLS:
        assert not any(w in t["name"] for w in ("create", "delete", "update", "send", "edit", "set"))
        assert t["inputSchema"]["type"] == "object"


def test_notifications_get_no_reply_and_unknowns_error():
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert mcp.handle({"id": 3, "method": "nope"})["error"]["code"] == -32601
    assert mcp.handle({"id": 4, "method": "tools/call", "params": {"name": "delete_everything"}})["error"]["code"] == -32602
    assert mcp.handle({"id": 5, "method": "ping"})["result"] == {}


def test_bad_arguments_are_reported_not_raised():
    r = mcp.handle({"id": 6, "method": "tools/call", "params": {"name": "get_lead", "arguments": {"id": "abc"}}})
    assert r["result"]["isError"] is True
