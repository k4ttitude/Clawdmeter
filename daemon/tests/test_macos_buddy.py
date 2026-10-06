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


class _FakeClient:
    is_connected = True

    def __init__(self, *_a, **_k):
        pass

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def start_notify(self, *_a):
        pass

    async def write_gatt_char(self, *_a, **_k):
        pass


def _count_polls(poll_result, write_ok, run_s=0.5):
    """Drive connect_and_run with buddy on and a failing poll; count poll calls."""
    polls = []

    async def fake_poll():
        polls.append(1)
        return poll_result

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(mod.connect_and_run("addr", stop))
        await asyncio.sleep(run_s)
        stop.set()
        await asyncio.wait_for(task, 3)

    with patch.object(mod, "BleakClient", _FakeClient), \
         patch.object(mod, "poll_active", fake_poll), \
         patch.object(mod, "BUDDY_TICK", 0.01), \
         patch.object(mod, "TICK", 0.2), \
         patch.object(mod.buddy, "read_hook_port", lambda _p: 45999), \
         patch.object(mod.buddy, "fetch_anim", AsyncMock(return_value="")), \
         patch.object(mod.Session, "write_payload", AsyncMock(return_value=write_ok)):
        asyncio.run(go())
    return len(polls)


def test_failed_poll_not_retried_faster_than_tick_with_buddy_on():
    # 0.5 s window, TICK 0.2 s: polls at ~0, 0.2, 0.4. Un-gated it would be ~50.
    assert _count_polls((None, False), True) <= 4


def test_failed_dead_token_write_not_retried_faster_than_tick_with_buddy_on():
    assert _count_polls((None, True), False) <= 4
