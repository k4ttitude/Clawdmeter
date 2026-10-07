"""Tests for daemon/buddy.py. Run: daemon/.venv/bin/python -m pytest daemon/tests/test_buddy.py -q"""
import asyncio
import json
import re
from pathlib import Path

import httpx

from daemon import buddy

OK = {"s": 40, "st": "allowed", "ok": True}


def _wire(state, elapsed_s=0, tool=None, detail=None):
    # GET /top body: the one most urgent session.
    return json.dumps({"state": state, "elapsed_s": elapsed_s, "tool": tool, "detail": detail})


def test_top_session_reads_the_body():
    assert buddy.top_session(_wire(4, 5, "Bash", "Install deps")) == (4, 5, "Bash", "Install deps")
    assert buddy.top_session(_wire(1, 9)) == (1, 9, None, None)


def test_top_session_empty_or_garbage_is_none():
    assert buddy.top_session("{}") is None
    assert buddy.top_session("not json") is None
    assert buddy.top_session("[1, 2]") is None
    assert buddy.top_session('{"state": 4}') is None
    assert buddy.top_session('{"state": "x", "elapsed_s": 1}') is None


def test_top_session_non_string_tool_or_detail_becomes_none():
    body = '{"state": 4, "elapsed_s": 1, "tool": 5, "detail": ["x"]}'
    assert buddy.top_session(body) == (4, 1, None, None)


def test_mapping_table():
    cases = [
        ((6, 0, None, None), "allow"), ((7, 0, None, None), "allow"),
        ((8, 0, None, None), "jumping happy"),
        ((9, 0, None, None), "expression surprise"), ((2, 0, None, None), "magnifier"),
        ((5, 0, None, None), "magnifier"), ((3, 0, None, None), "laptop"),
        ((4, 0, "Bash", "x"), "laptop"), ((4, 0, "Read", None), "laptop"),
        ((0, 0, None, None), ""), ((1, 10, None, None), "jumping happy"),
        ((1, 600, None, None), ""), ((1, 4000, None, None), "expression sleep"),
    ]
    for top, want in cases:
        assert buddy.pick(top, OK) == want, top


def test_no_session_releases():
    assert buddy.pick(None, OK) == ""


def test_limit_beats_everything():
    assert buddy.pick((6, 0, None, None), {"s": 100, "st": "allowed", "ok": True}) == "limit"
    assert buddy.pick(None, {"s": 80, "st": "rejected", "ok": True}) == "limit"


def test_unknown_state_code_releases():
    assert buddy.pick((42, 0, None, None), OK) == ""


def test_every_name_exists_in_firmware():
    # Names travel as BLE strings; a typo would only show up as a log line on the device.
    src = Path(buddy.__file__).resolve().parents[1] / "firmware" / "src"
    headers = (src / "splash_animations.h").read_text() + (src / "buddy_animations.h").read_text()
    known = set(re.findall(r'\{"([^"]+)",\s*"(?:core|persona|buddy)"', headers))
    names = {buddy.pick((st, el, None, None), OK) for st in range(10) for el in (10, 600, 4000)}
    names.add(buddy.pick(None, {"s": 100, "st": "allowed", "ok": True}))
    names.discard("")
    assert names and names <= known, names - known


def test_read_hook_port(tmp_path):
    cfg = tmp_path / "config"
    assert buddy.read_hook_port(cfg) is None
    cfg.write_text("chime = on\nhook_port = 45999  # sidecar\n")
    assert buddy.read_hook_port(cfg) == 45999
    cfg.write_text("hook_port = nope\n")
    assert buddy.read_hook_port(cfg) is None


LIMITED = {"s": 100, "st": "allowed", "ok": True}


def _fetch(handler, usage=OK):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await buddy.fetch(c, 45999, usage)
    return asyncio.run(go())


def _refuse(request):
    raise httpx.ConnectError("refused", request=request)


def test_fetch_sidecar_down_releases():
    assert _fetch(_refuse) == ("", "")


def test_fetch_404_releases():
    assert _fetch(lambda r: httpx.Response(404)) == ("", "")


def test_fetch_errors_with_limited_usage_still_say_limit():
    assert _fetch(_refuse, LIMITED) == ("limit", "Out of quota")
    assert _fetch(lambda r: httpx.Response(404), LIMITED) == ("limit", "Out of quota")


def test_fetch_uses_the_single_top_answer():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, text=_wire(6, 3, "Bash", "Run tests"))

    assert _fetch(handler) == ("allow", "Permission: Running: Run tests")
    assert seen == ["/top"]


def test_fetch_no_session_is_blank():
    assert _fetch(lambda r: httpx.Response(200, text="{}")) == ("", "")
    assert _fetch(lambda r: httpx.Response(200, text="{}"), LIMITED) == ("limit", "Out of quota")


def test_action_text_state_table():
    t = buddy.action_text
    assert t((4, 0, "Read", None), OK) == "Reading files"
    assert t((4, 0, None, None), OK) == "Running a tool"
    assert t((6, 0, "Edit", None), OK) == "Permission: Editing code"
    assert t((6, 0, None, None), OK) == "Waiting for permission"
    assert t((2, 0, None, None), OK) == "Thinking"
    assert t((3, 0, None, None), OK) == "Writing a reply"
    assert t((5, 0, None, None), OK) == "Compacting context"
    assert t((7, 0, None, None), OK) == "Asking you a question"
    assert t((8, 0, None, None), OK) == "Waiting for you"
    assert t((9, 0, None, None), OK) == "Something went wrong"
    assert t((0, 0, None, None), OK) == "Starting up"
    assert t((1, buddy.JUST_DONE_S - 1, None, None), OK) == "Done"
    assert t((1, buddy.JUST_DONE_S, None, None), OK) == ""
    assert t((1, buddy.SLEEP_AFTER_S - 1, None, None), OK) == ""
    assert t((1, buddy.SLEEP_AFTER_S, None, None), OK) == "Sleeping"
    assert t((42, 0, None, None), OK) == ""
    assert t(None, OK) == ""
    assert t(None, LIMITED) == "Out of quota"
    assert t((6, 0, "Bash", "x"), LIMITED) == "Out of quota"
    assert t(None, {"s": 80, "st": "rejected", "ok": True}) == "Out of quota"


def test_permission_text_is_cut_to_fit():
    text = buddy.action_text((6, 0, "Bash", "Run the full integration suite"), OK)
    assert text == "Permission: Running: Run the..."
    assert len(text) <= buddy.MAX_TEXT


UUID = "85aa66ba-a9f6-4a5a-8ebd-4264ce1f94c2"


def test_tool_text_builtin_tools():
    for tool, want in [
        ("Read", "Reading files"), ("Grep", "Searching code"), ("Glob", "Searching code"),
        ("Edit", "Editing code"), ("MultiEdit", "Editing code"), ("Write", "Editing code"),
        ("NotebookEdit", "Editing a notebook"), ("WebFetch", "Browsing the web"),
        ("WebSearch", "Searching the web"), ("Task", "Running a subagent"),
        ("Agent", "Running a subagent"), ("TodoWrite", "Planning"),
        ("Skill", "Using a skill"), ("AskUserQuestion", "Asking you a question"),
        ("Frobnicate", "Using Frobnicate"),
    ]:
        assert buddy.tool_text(tool, None) == want, tool


def test_tool_text_only_bash_reads_detail():
    assert buddy.tool_text("Read", "/etc/passwd") == "Reading files"
    assert buddy.tool_text("Frobnicate", "secret") == "Using Frobnicate"


def test_tool_text_mcp_names():
    for tool, want in [
        ("mcp__claude_ai_Amplitude__query_dashboards", "Using Amplitude"),
        (f"mcp__{UUID}__query_amplitude_data", "Using Amplitude"),
        (f"mcp__{UUID}__do_thing", "Using a connector"),
        ("mcp__plugin_atlassian_atlassian__getJiraIssue", "Using Atlassian"),
        ("mcp__claude_ai_Atlassian_Rovo__search", "Using Atlassian"),
        ("mcp__Claude_Browser__navigate", "Using the browser"),
        ("mcp__claude-in-chrome__computer", "Using Chrome"),
        ("mcp__terminal__run_in_terminal", "Using the terminal"),
        ("mcp__visualize__show_widget", "Drawing a chart"),
        ("mcp__ccd_session__mark_chapter", "Working"),
        ("mcp__plugin_context7_context7__resolve", "Using Context7"),
        ("mcp__my_server__x", "Using My server"),
    ]:
        assert buddy.tool_text(tool, None) == want, tool


def test_tool_text_bash():
    assert buddy.tool_text("Bash", None) == "Running a command"
    assert buddy.tool_text("Bash", "") == "Running a command"
    assert buddy.tool_text("Bash", "Install deps") == "Running: Install deps"
    assert buddy.tool_text("Bash", "line1\nline2\t x") == "Running: line1 line2 x"
    assert buddy.tool_text("Bash", "Ship it \U0001F680  now") == "Running: Ship it now"
    assert buddy.tool_text("Bash", "\U0001F680 \n ") == "Running a command"
    long = buddy.tool_text("Bash", "a" * 200)
    assert len(long) == 32 and long.endswith("...")


def test_every_action_text_is_short_ascii():
    tools = [None, "Bash", "Read", "Edit", "Frobnicate", "mcp__claude_ai_Amplitude__q",
             f"mcp__{UUID}__x", "mcp__plugin_atlassian_atlassian__x", "mcp__ccd_a__b",
             "T\u00e9l\u00e9phone", "\U0001F680" * 60, "mcp__" + "z" * 100 + "__x"]
    details = [None, "x", "caf\u00e9 \U0001F680 \u4e2d\u6587", "y" * 300, "tab\there\r\nnow"]
    for st in list(range(10)) + [42]:
        for el in (0, 600, 4000):
            for tool in tools:
                for detail in details:
                    for usage in (OK, LIMITED):
                        text = buddy.action_text((st, el, tool, detail), usage)
                        assert len(text) <= buddy.MAX_TEXT, text
                        assert all(32 <= ord(ch) <= 126 for ch in text), repr(text)


# An old sidecar (started before the /top route) answers GET /top with the "/" wire.
OLD_WIRE = json.dumps({"ss": [["sid1", "myproj", 4, 55, 7, "opus", 3],
                              ["sid2", "other", 1, 10, 900, "opus", 0]]})


def test_top_session_reads_an_old_sidecar_body():
    assert buddy.top_session(OLD_WIRE) == (4, 7, None, None)
    assert buddy.top_session('{"ss": []}') is None
    assert buddy.top_session('{"ss": [["sid", "x", "bad", 1, 2]]}') is None
    assert buddy.top_session('{"ss": [["sid", "x"]]}') is None


def test_fetch_old_sidecar_body_keeps_the_buddy_alive(monkeypatch):
    monkeypatch.setattr(buddy, "_warned_old_sidecar", False)
    assert _fetch(lambda r: httpx.Response(200, text=OLD_WIRE)) == ("laptop", "Running a tool")


def test_old_sidecar_is_logged_once_per_run(monkeypatch, capsys):
    monkeypatch.setattr(buddy, "_warned_old_sidecar", False)
    handler = lambda r: httpx.Response(200, text=OLD_WIRE)  # noqa: E731
    _fetch(handler)
    _fetch(handler)
    out = capsys.readouterr().out
    assert out.count("older than the daemon") == 1
    assert "launchctl kickstart -k gui/$UID/com.user.clawdmeter-sessions" in out
