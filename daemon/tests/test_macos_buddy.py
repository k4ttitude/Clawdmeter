"""BuddyLink: writes {"a":...,"x":...} only on change; usage payloads carry both.

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
    seq = [("allow", "Waiting for permission")] * 2 + [("laptop", "Writing a reply")]
    with patch.object(mod.buddy, "fetch", AsyncMock(side_effect=seq)):
        for _ in seq:
            asyncio.run(link.step(s, OK))
    assert [c.args[0] for c in s.write_payload.await_args_list] == [
        {"a": "allow", "x": "Waiting for permission"},
        {"a": "laptop", "x": "Writing a reply"}]


def test_text_only_change_writes_once_with_the_same_anim():
    link = mod.BuddyLink(port=45999)
    s = _session()
    seq = [("laptop", "Reading files"), ("laptop", "Searching code"), ("laptop", "Searching code")]
    with patch.object(mod.buddy, "fetch", AsyncMock(side_effect=seq)):
        for _ in seq:
            asyncio.run(link.step(s, OK))
    assert [c.args[0] for c in s.write_payload.await_args_list] == [
        {"a": "laptop", "x": "Reading files"}, {"a": "laptop", "x": "Searching code"}]


def test_each_new_bash_description_writes_and_a_repeat_does_not():
    link = mod.BuddyLink(port=45999)
    s = _session()
    seq = [("laptop", f"Running: {d}") for d in ("Install deps", "Run tests", "Lint")]
    seq.append(seq[-1])
    with patch.object(mod.buddy, "fetch", AsyncMock(side_effect=seq)):
        for _ in seq:
            asyncio.run(link.step(s, OK))
    assert [c.args[0]["x"] for c in s.write_payload.await_args_list] == [
        "Running: Install deps", "Running: Run tests", "Running: Lint"]


def test_failed_write_retries_next_step():
    link = mod.BuddyLink(port=45999)
    s = _session()
    s.write_payload = AsyncMock(side_effect=[False, True])
    with patch.object(mod.buddy, "fetch", AsyncMock(return_value=("allow", "Waiting for permission"))):
        asyncio.run(link.step(s, OK))
        asyncio.run(link.step(s, OK))
    assert s.write_payload.await_count == 2


def test_stamp_adds_current_anim_and_text_to_usage_payload():
    link = mod.BuddyLink(port=45999)
    with patch.object(mod.buddy, "fetch", AsyncMock(return_value=("jumping happy", "Done"))):
        asyncio.run(link.step(_session(), OK))
    p = dict(OK)
    link.stamp(p)
    assert p["a"] == "jumping happy" and p["x"] == "Done"


def test_disabled_link_is_inert():
    link = mod.BuddyLink(port=None)
    s = _session()
    asyncio.run(link.step(s, OK))
    p = dict(OK)
    link.stamp(p)
    assert s.write_payload.await_count == 0 and "a" not in p and "x" not in p


def test_unexpected_fetch_error_keeps_previous_state_and_does_not_raise():
    link = mod.BuddyLink(port=45999)
    s = _session()
    seq = [("allow", "Waiting for permission"), RuntimeError("boom"),
           RuntimeError("boom again"), ("laptop", "Writing a reply")]
    with patch.object(mod.buddy, "fetch", AsyncMock(side_effect=seq)):
        for _ in seq:
            asyncio.run(link.step(s, OK))
    # the two failures wrote nothing and kept "allow"; recovery then writes "laptop"
    assert [c.args[0] for c in s.write_payload.await_args_list] == [
        {"a": "allow", "x": "Waiting for permission"},
        {"a": "laptop", "x": "Writing a reply"}]


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
         patch.object(mod.buddy, "fetch", AsyncMock(return_value=("", ""))), \
         patch.object(mod.Session, "write_payload", AsyncMock(return_value=write_ok)):
        asyncio.run(go())
    return len(polls)


def test_failed_poll_not_retried_faster_than_tick_with_buddy_on():
    # 0.5 s window, TICK 0.2 s: polls at ~0, 0.2, 0.4. Un-gated it would be ~50.
    assert _count_polls((None, False), True) <= 4


def test_failed_dead_token_write_not_retried_faster_than_tick_with_buddy_on():
    assert _count_polls((None, True), False) <= 4


def _run_sequence(polls, run_s=0.8):
    """Drive the real connect_and_run with buddy on, a fake sidecar that says
    "laptop" (a session in RESPONDING), and a scripted poll_active.

    Returns every dict handed to Session.write_payload, in order. The last poll
    result repeats once the script runs out.
    """
    import httpx

    writes = []
    script = list(polls)

    async def fake_poll():
        return script.pop(0) if len(script) > 1 else script[0]

    async def record(_self, payload):
        writes.append(dict(payload))
        return True

    def sidecar(_request):
        # state 3 = RESPONDING -> buddy.pick() says "laptop", text "Writing a reply"
        return httpx.Response(
            200, text='{"state": 3, "elapsed_s": 0, "tool": null, "detail": null}')

    real_client = httpx.AsyncClient

    def fake_async_client(**kw):
        return real_client(transport=httpx.MockTransport(sidecar), **kw)

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(mod.connect_and_run("addr", stop))
        await asyncio.sleep(run_s)
        stop.set()
        await asyncio.wait_for(task, 3)

    with patch.object(mod, "BleakClient", _FakeClient), \
         patch.object(mod, "poll_active", fake_poll), \
         patch.object(mod, "BUDDY_TICK", 0.01), \
         patch.object(mod, "POLL_INTERVAL", 0.1), \
         patch.object(mod.httpx, "AsyncClient", fake_async_client), \
         patch.object(mod.buddy, "read_hook_port", lambda _p: 45999), \
         patch.object(mod.Session, "write_payload", record):
        asyncio.run(go())
    return writes


def test_usage_frames_carry_current_anim_and_text_and_no_buddy_only_frame_follows():
    rejected = {"s": 100, "st": "rejected", "ok": True}
    writes = _run_sequence([
        (dict(OK), False),       # connect: first usage frame
        (None, True),            # dead token beat
        (dict(rejected), False),  # crosses into quota
        (dict(OK), False),       # and back out
    ])
    # (a) the first usage frame already says "laptop"; (b) the dead-token beat
    # keeps it; (c) the limit state is stamped on the usage frame itself; and
    # leaving the limit stamps "laptop" again. Text travels with the name every
    # time. Never a separate {"a": ..., "x": ...} frame.
    writing = {"a": "laptop", "x": "Writing a reply"}
    assert writes[:4] == [
        {**OK, **writing},
        {"ok": False, **writing},
        {**rejected, "a": "limit", "x": "Out of quota"},
        {**OK, **writing},
    ]
    assert not [w for w in writes if set(w) == {"a", "x"}], writes


def _first_iteration_writes(poll_result):
    """Drive one loop iteration (BUDDY_TICK is longer than the run) against a
    sidecar whose answer changes after its first reply. Returns the writes."""
    import httpx

    writes, answers = [], [
        '{"state": 3, "elapsed_s": 0, "tool": null, "detail": null}',   # laptop
        '{"state": 6, "elapsed_s": 0, "tool": null, "detail": null}',   # allow
    ]

    async def fake_poll():
        return poll_result

    async def record(_self, payload):
        writes.append(dict(payload))
        return True

    def sidecar(_request):
        return httpx.Response(200, text=answers.pop(0) if len(answers) > 1 else answers[0])

    real_client = httpx.AsyncClient

    def fake_async_client(**kw):
        return real_client(transport=httpx.MockTransport(sidecar), **kw)

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(mod.connect_and_run("addr", stop))
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(task, 3)

    with patch.object(mod, "BleakClient", _FakeClient), \
         patch.object(mod, "poll_active", fake_poll), \
         patch.object(mod, "BUDDY_TICK", 0.5), \
         patch.object(mod, "POLL_INTERVAL", 60), \
         patch.object(mod.httpx, "AsyncClient", fake_async_client), \
         patch.object(mod.buddy, "read_hook_port", lambda _p: 45999), \
         patch.object(mod.Session, "write_payload", record):
        asyncio.run(go())
    return writes


def test_no_buddy_only_frame_right_behind_a_usage_frame():
    # The firmware has one rx buffer. If /top changes between the usage frame's
    # fetch and the step's fetch, the step must wait for the next iteration.
    writes = _first_iteration_writes((dict(OK), False))
    assert writes == [{**OK, "a": "laptop", "x": "Writing a reply"}], writes


def test_no_buddy_only_frame_right_behind_a_dead_token_beat():
    writes = _first_iteration_writes((None, True))
    assert writes == [{"ok": False, "a": "laptop", "x": "Writing a reply"}], writes


def test_worst_case_frame_fits_one_ble_write():
    # NimBLE's preferred ATT MTU is 255 and the daemon writes without response,
    # so a frame is at most 255 - 3 = 252 bytes. Pin 244 to keep a margin.
    # Worst case: the enterprise fields, every opt-in field, the longest animation
    # name, and 32 quote characters, which JSON escapes to 64 bytes.
    from daemon import buddy
    payload = {
        "s": 100, "sr": 44640, "w": 100, "wr": 10080, "st": "allowed_warning",
        "acct": "ent", "tp": 100, "pd": 31, "rd": "Sep 30", "ok": True,
        "c": 1, "t": 2_000_000_000, "tf": 12,
        "a": "expression surprise", "x": '"' * buddy.MAX_TEXT,
    }
    sent = []

    class Client:
        async def write_gatt_char(self, _uuid, data, response):
            sent.append(bytes(data))

    assert asyncio.run(mod.Session(Client()).write_payload(payload)) is True
    print(f"worst-case frame: {len(sent[0])} bytes")
    assert len(sent[0]) <= 244, len(sent[0])
