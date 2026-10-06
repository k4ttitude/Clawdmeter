"""Pick the Clawdmeter buddy animation from live Claude Code session state.

The session sidecar (clawdmeter_sessions.py) serves its attention-first table on
GET http://127.0.0.1:<hook_port>/. Row 0 is the session that most needs me, so
its state decides the animation. Names must match the firmware exactly: official
Clawd from splash_animations.h where one fits, the claudepix sprites in
buddy_animations.h where none does. "" hands the splash back to the device's own
usage-rate rotation.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

# Wire state codes (clawdmeter_sessions.py, issue #135; append-only).
STARTING, IDLE, THINKING, RESPONDING, RUNNING_TOOL, COMPACTING = 0, 1, 2, 3, 4, 5
WAITING_PERMISSION, WAITING_QUESTION, WAITING_INPUT, ERROR = 6, 7, 8, 9

JUST_DONE_S = 180        # idle this short still reads as "done"
SLEEP_AFTER_S = 1800     # idle this long falls asleep
FETCH_TIMEOUT_S = 0.5

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


def top_session(wire: str) -> tuple[int, int, int] | None:
    """(state, elapsed_s, tool) of the most urgent session, or None."""
    try:
        row = json.loads(wire)["ss"][0]
        return int(row[2]), int(row[4]), int(row[6])
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def _limited(usage: dict | None) -> bool:
    if not usage or not usage.get("ok"):
        return False
    return usage.get("st") == "rejected" or float(usage.get("s") or 0) >= 100


def pick(top: tuple[int, int, int] | None, usage: dict | None) -> str:
    if _limited(usage):
        return "limit"
    if top is None:
        return ""
    state, elapsed, _tool = top
    if state == IDLE:
        if elapsed < JUST_DONE_S:
            return DONE
        return "" if elapsed < SLEEP_AFTER_S else "expression sleep"   # buddy
    return _BY_STATE.get(state, "")


async def fetch_anim(client: httpx.AsyncClient, port: int, usage: dict | None) -> str:
    """Animation for right now; "" when the sidecar is unreachable."""
    try:
        resp = await client.get(f"http://127.0.0.1:{port}/", timeout=FETCH_TIMEOUT_S)
        resp.raise_for_status()
    except httpx.HTTPError:
        return "limit" if _limited(usage) else ""
    return pick(top_session(resp.text), usage)
