"""BuddyLink: writes {"a":...} only on change; usage payloads carry the current "a".

Run: daemon/.venv/bin/python -m pytest daemon/tests/test_macos_buddy.py -q
"""
import asyncio
from unittest.mock import AsyncMock, patch

import daemon.claude_usage_daemon as mod

OK = {"s": 40, "st": "allowed", "ok": True}


def _session():
    s = AsyncMock()
    s.write_payload = AsyncMock(return_value=True)
    return s


def test_writes_only_on_change():
    link = mod.BuddyLink(port=45999)
    s = _session()
    seq = ["allow", "allow", "laptop"]
    with patch.object(mod.buddy, "fetch_anim", AsyncMock(side_effect=seq)):
        for _ in seq:
            asyncio.run(link.step(s, OK))
    assert [c.args[0] for c in s.write_payload.await_args_list] == [
        {"a": "allow"}, {"a": "laptop"}]


def test_failed_write_retries_next_step():
    link = mod.BuddyLink(port=45999)
    s = _session()
    s.write_payload = AsyncMock(side_effect=[False, True])
    with patch.object(mod.buddy, "fetch_anim", AsyncMock(return_value="allow")):
        asyncio.run(link.step(s, OK))
        asyncio.run(link.step(s, OK))
    assert s.write_payload.await_count == 2


def test_stamp_adds_current_anim_to_usage_payload():
    link = mod.BuddyLink(port=45999)
    with patch.object(mod.buddy, "fetch_anim", AsyncMock(return_value="jumping happy")):
        asyncio.run(link.step(_session(), OK))
    p = dict(OK)
    link.stamp(p)
    assert p["a"] == "jumping happy"


def test_disabled_link_is_inert():
    link = mod.BuddyLink(port=None)
    s = _session()
    asyncio.run(link.step(s, OK))
    p = dict(OK)
    link.stamp(p)
    assert s.write_payload.await_count == 0 and "a" not in p


def test_unexpected_fetch_error_keeps_previous_anim_and_does_not_raise():
    link = mod.BuddyLink(port=45999)
    s = _session()
    seq = ["allow", RuntimeError("boom"), RuntimeError("boom again"), "laptop"]
    with patch.object(mod.buddy, "fetch_anim", AsyncMock(side_effect=seq)):
        for _ in seq:
            asyncio.run(link.step(s, OK))
    # the two failures wrote nothing and kept "allow"; recovery then writes "laptop"
    assert [c.args[0] for c in s.write_payload.await_args_list] == [
        {"a": "allow"}, {"a": "laptop"}]
