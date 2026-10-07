"""Pick the Clawdmeter buddy animation and action text from live Claude Code state.

The session sidecar (clawdmeter_sessions.py) serves the session that most needs
me on GET http://127.0.0.1:<hook_port>/top. Its state decides the animation.
Names must match the firmware exactly: official Clawd from splash_animations.h
where one fits, the claudepix sprites in buddy_animations.h where none does.
"" hands the splash back to the device's own usage-rate rotation.

The same answer also becomes a short line of text under the animation. The text
is built only from fixed phrases, an MCP service name and Bash's own
description. It never carries a command line, a path, a prompt or the sidecar's
cwd-derived label. "" hides the line.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

# Wire state codes (clawdmeter_sessions.py, issue #135; append-only).
STARTING, IDLE, THINKING, RESPONDING, RUNNING_TOOL, COMPACTING = 0, 1, 2, 3, 4, 5
WAITING_PERMISSION, WAITING_QUESTION, WAITING_INPUT, ERROR = 6, 7, 8, 9

JUST_DONE_S = 180        # idle this short still reads as "done"
SLEEP_AFTER_S = 1800     # idle this long falls asleep
FETCH_TIMEOUT_S = 0.5
MAX_TEXT = 32            # the device line is short, and its fonts cover ASCII 32..126 only

DONE = "jumping happy"   # official
_BY_STATE = {
    WAITING_PERMISSION: "allow",             # buddy
    WAITING_QUESTION: "allow",               # buddy
    WAITING_INPUT: DONE,
    ERROR: "expression surprise",            # buddy
    THINKING: "magnifier",                   # official
    COMPACTING: "magnifier",
    RESPONDING: "laptop",                    # official
    RUNNING_TOOL: "laptop",
    STARTING: "",
}


def read_hook_port(config_file: Path) -> int | None:
    """hook_port from the daemon config, or None (buddy off)."""
    try:
        text = config_file.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        if key.strip().lower() == "hook_port":
            try:
                return int(val.strip())
            except ValueError:
                return None
    return None


QUOTA_TEXT = "Out of quota"

# Fixed text for states that need no tool.
_TEXT_BY_STATE = {
    THINKING: "Thinking",
    RESPONDING: "Writing a reply",
    COMPACTING: "Compacting context",
    WAITING_QUESTION: "Asking you a question",
    WAITING_INPUT: "Waiting for you",
    ERROR: "Something went wrong",
    STARTING: "Starting up",
}

_TOOL_TEXT = {
    "Read": "Reading files",
    "Grep": "Searching code",
    "Glob": "Searching code",
    "Edit": "Editing code",
    "MultiEdit": "Editing code",
    "Write": "Editing code",
    "NotebookEdit": "Editing a notebook",
    "WebFetch": "Browsing the web",
    "WebSearch": "Searching the web",
    "Task": "Running a subagent",
    "Agent": "Running a subagent",
    "TodoWrite": "Planning",
    "Skill": "Using a skill",
    "AskUserQuestion": "Asking you a question",
}

# Service words looked for in a connector's name, in this order.
_SERVICES = {
    "amplitude": "Amplitude", "datadog": "Datadog", "jira": "Jira",
    "confluence": "Confluence", "atlassian": "Atlassian", "slack": "Slack",
    "github": "GitHub", "gitlab": "GitLab", "linear": "Linear", "notion": "Notion",
    "figma": "Figma", "sentry": "Sentry", "gmail": "Gmail",
    "databricks": "Databricks", "buildkite": "Buildkite", "stripe": "Stripe",
}

_DESKTOP_SERVERS = {
    "claude_browser": "Using the browser",
    "claude-in-chrome": "Using Chrome",
    "terminal": "Using the terminal",
    "visualize": "Drawing a chart",
}

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_NOT_ASCII_PRINTABLE = re.compile(r"[^\x20-\x7e]")


def read_hook_port(config_file: Path) -> int | None:
    """hook_port from the daemon config, or None (buddy off)."""
    try:
        text = config_file.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        if key.strip().lower() == "hook_port":
            try:
                return int(val.strip())
            except ValueError:
                return None
    return None


def top_session(body: str) -> tuple[int, int, str | None, str | None] | None:
    """(state, elapsed_s, tool, detail) from a /top body, or None for {} or garbage."""
    try:
        top = json.loads(body)
        tool, detail = top.get("tool"), top.get("detail")
        return (int(top["state"]), int(top["elapsed_s"]),
                tool if isinstance(tool, str) else None,
                detail if isinstance(detail, str) else None)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _limited(usage: dict | None) -> bool:
    if not usage or not usage.get("ok"):
        return False
    return usage.get("st") == "rejected" or float(usage.get("s") or 0) >= 100


def pick(top: tuple | None, usage: dict | None) -> str:
    if _limited(usage):
        return "limit"
    if top is None:
        return ""
    state, elapsed = top[0], top[1]
    if state == IDLE:
        if elapsed < JUST_DONE_S:
            return DONE
        return "" if elapsed < SLEEP_AFTER_S else "expression sleep"   # buddy
    return _BY_STATE.get(state, "")


def _clean(text: str) -> str:
    """ASCII 32..126 only, whitespace runs collapsed to one space."""
    text = re.sub(r"\s+", " ", text)
    text = _NOT_ASCII_PRINTABLE.sub("", text)
    return re.sub(r" {2,}", " ", text).strip()


def _fit(text: str) -> str:
    """Clean text and cut it to MAX_TEXT characters, ending in "..." when cut."""
    text = _clean(text)
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT - 3].rstrip() + "..."
    return text


def _service(name: str) -> str | None:
    low = name.lower()
    return next((shown for word, shown in _SERVICES.items() if word in low), None)


def _mcp_text(tool: str) -> str:
    server, _, rest = tool[len("mcp__"):].partition("__")
    if _UUID.fullmatch(server):
        service = _service(rest)
        return f"Using {service}" if service else "Using a connector"
    low = server.lower()
    if low in _DESKTOP_SERVERS:
        return _DESKTOP_SERVERS[low]
    if low.startswith("ccd_"):
        return "Working"
    if low.startswith("claude_ai_"):
        server = server[len("claude_ai_"):]
    if server.startswith("plugin_") and server.count("_") >= 2:
        server = server.split("_", 2)[2]
    service = _service(server)
    if service:
        return f"Using {service}"
    name = server.replace("_", " ")
    return f"Using {name[:1].upper()}{name[1:]}"


def tool_text(tool: str, detail: str | None) -> str:
    """What a tool call looks like on the device. Only Bash's description is used."""
    if tool == "Bash":
        described = _clean(detail or "")
        return _fit(f"Running: {described}" if described else "Running a command")
    if tool in _TOOL_TEXT:
        return _TOOL_TEXT[tool]
    if tool.startswith("mcp__"):
        return _fit(_mcp_text(tool))
    return _fit(f"Using {tool}")


def action_text(top: tuple | None, usage: dict | None) -> str:
    """The line under the animation for right now, "" for none."""
    if _limited(usage):
        return QUOTA_TEXT
    if top is None:
        return ""
    state, elapsed, tool, detail = top
    if state == RUNNING_TOOL:
        return _fit(tool_text(tool, detail) if tool else "Running a tool")
    if state == WAITING_PERMISSION:
        return _fit(f"Permission: {tool_text(tool, detail)}" if tool else "Waiting for permission")
    if state == IDLE:
        if elapsed < JUST_DONE_S:
            return "Done"
        return "" if elapsed < SLEEP_AFTER_S else "Sleeping"
    return _TEXT_BY_STATE.get(state, "")


async def fetch(client: httpx.AsyncClient, port: int, usage: dict | None) -> tuple[str, str]:
    """(animation, text) for right now, both from one /top answer.

    On any HTTP error (a refused connection or a 404 from an older sidecar
    included) there is nothing to show: ("", ""), or the quota pair when limited.
    """
    try:
        resp = await client.get(f"http://127.0.0.1:{port}/top", timeout=FETCH_TIMEOUT_S)
        resp.raise_for_status()
    except httpx.HTTPError:
        return ("limit", QUOTA_TEXT) if _limited(usage) else ("", "")
    top = top_session(resp.text)
    return pick(top, usage), action_text(top, usage)
