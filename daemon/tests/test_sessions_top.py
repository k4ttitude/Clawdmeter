#!/usr/bin/env python3
"""Tests for GET /top, the Clawdmeter fork's buddy view of the sidecar.

Run: python -m pytest daemon/tests/test_sessions_top.py -q
"""
import json
import threading
import urllib.request

from daemon.clawdmeter_sessions import (
    STATE_RUNNING_TOOL, STATE_WAITING_PERMISSION, DEFAULT_BUDGET_BYTES,
    HookServer,
)
from daemon.tests.test_sessions import SID, SID2, FakeClock, ev, make_table


def pre_tool(table, tool, tool_input=None, sid=SID, tuid="t1"):
    fields = {"tool_name": tool, "tool_use_id": tuid}
    if tool_input is not None:
        fields["tool_input"] = tool_input
    table.handle_event(ev("PreToolUse", sid=sid, **fields))


def test_bash_exposes_description():
    t = make_table()
    pre_tool(t, "Bash", {"command": "npm i", "description": "Install deps"})
    top = t.top()
    assert top["tool"] == "Bash"
    assert top["detail"] == "Install deps"
    assert top["state"] == STATE_RUNNING_TOOL


def test_non_bash_detail_is_none():
    t = make_table()
    pre_tool(t, "Read", {"file_path": "/secret", "description": "do not leak"})
    top = t.top()
    assert top["tool"] == "Read"
    assert top["detail"] is None


def test_mcp_tool_name_is_exact():
    t = make_table()
    pre_tool(t, "mcp__claude_ai_Amplitude__query", {"q": "x"})
    assert t.top()["tool"] == "mcp__claude_ai_Amplitude__query"


def test_non_dict_tool_input_does_not_raise():
    t = make_table()
    for bad in ("ls -la", ["a", "b"], 42):
        pre_tool(t, "Bash", bad, tuid=str(bad))
        assert t.top()["detail"] is None


def test_non_string_description_is_none():
    t = make_table()
    pre_tool(t, "Bash", {"description": {"nested": 1}})
    assert t.top()["detail"] is None


def test_prompt_submit_and_stop_clear_tool_and_detail():
    for clearing in ("UserPromptSubmit", "Stop"):
        t = make_table()
        pre_tool(t, "Bash", {"description": "Install deps"})
        t.handle_event(ev("PostToolUse", tool_name="Bash", tool_use_id="t1"))
        # PostToolUse keeps the tool so the line doesn't flicker.
        assert t.top()["tool"] == "Bash"
        assert t.top()["detail"] == "Install deps"
        t.handle_event(ev(clearing))
        top = t.top()
        assert top["tool"] is None, clearing
        assert top["detail"] is None, clearing


def test_next_tool_replaces_detail():
    t = make_table()
    pre_tool(t, "Bash", {"description": "Install deps"}, tuid="t1")
    pre_tool(t, "Grep", {"pattern": "x"}, tuid="t2")
    top = t.top()
    assert top["tool"] == "Grep"
    assert top["detail"] is None


def test_top_matches_row_zero_of_rows():
    clock = FakeClock()
    t = make_table(clock)
    pre_tool(t, "Bash", {"description": "Running tests"}, sid=SID)
    clock.tick(5)
    pre_tool(t, "Read", {"file_path": "/x"}, sid=SID2)
    t.handle_event(ev("PermissionRequest", sid=SID2))
    clock.tick(3)
    top = t.top()
    first = t.rows()[0]
    assert first[0] == t.sessions[SID2].sid
    assert top["state"] == STATE_WAITING_PERMISSION == first[2]
    assert top["elapsed_s"] == first[4] == 3
    assert top["tool"] == "Read"
    assert top["detail"] is None


def test_empty_table_has_no_top():
    assert make_table().top() is None


def serve(table):
    server = HookServer(("127.0.0.1", 0), table, "/dev/null", DEFAULT_BUDGET_BYTES)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def get(server, path):
    url = f"http://127.0.0.1:{server.server_address[1]}{path}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "application/json"
        return resp.read().decode("utf-8")


def test_http_top_and_root():
    t = make_table()
    server, thread = serve(t)
    try:
        assert json.loads(get(server, "/top")) == {}
        pre_tool(t, "Bash", {"command": "npm i", "description": "Install deps"})
        body = json.loads(get(server, "/top"))
        assert body == {
            "state": STATE_RUNNING_TOOL,
            "elapsed_s": body["elapsed_s"],
            "tool": "Bash",
            "detail": "Install deps",
        }
        assert isinstance(body["elapsed_s"], int)
        assert json.loads(get(server, "/top?x=1")) == body
        # The upstream wire format is untouched, and carries no detail text.
        root = get(server, "/")
        assert root == t.project(DEFAULT_BUDGET_BYTES)
        assert "Install deps" not in root
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
