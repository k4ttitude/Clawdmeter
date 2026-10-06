"""Tests for daemon/buddy.py. Run: daemon/.venv/bin/python -m pytest daemon/tests/test_buddy.py -q"""
import asyncio
import json
import re
from pathlib import Path

import httpx

from daemon import buddy

OK = {"s": 40, "st": "allowed", "ok": True}


def _wire(*rows):
    # row: [sid, label, state, ctx, elapsed_s, model, tool, ntools, nagents, tdone, ttotal, tok]
    return json.dumps({"ss": [[f"{i:02x}", "x", st, -1, el, 0, tool, 0, 0, 0, 0, -1]
                              for i, (st, el, tool) in enumerate(rows)]})


def test_top_session_reads_row_zero():
    assert buddy.top_session(_wire((6, 5, 1), (4, 1, 3))) == (6, 5, 1)


def test_top_session_empty_or_garbage_is_none():
    assert buddy.top_session('{"ss":[]}') is None
    assert buddy.top_session("not json") is None
    assert buddy.top_session('{"ss":[[1]]}') is None


def test_mapping_table():
    cases = [
        ((6, 0, 0), "allow"), ((7, 0, 0), "allow"), ((8, 0, 0), "jumping happy"),
        ((9, 0, 0), "expression surprise"), ((2, 0, 0), "magnifier"),
        ((5, 0, 0), "magnifier"), ((3, 0, 0), "laptop"), ((4, 0, 3), "laptop"),
        ((4, 0, 1), "laptop"), ((0, 0, 0), ""), ((1, 10, 0), "jumping happy"),
        ((1, 600, 0), ""), ((1, 4000, 0), "expression sleep"),
    ]
    for top, want in cases:
        assert buddy.pick(top, OK) == want, top


def test_no_session_releases():
    assert buddy.pick(None, OK) == ""


def test_limit_beats_everything():
    assert buddy.pick((6, 0, 0), {"s": 100, "st": "allowed", "ok": True}) == "limit"
    assert buddy.pick(None, {"s": 80, "st": "rejected", "ok": True}) == "limit"


def test_unknown_state_code_releases():
    assert buddy.pick((42, 0, 0), OK) == ""


def test_every_name_exists_in_firmware():
    # Names travel as BLE strings; a typo would only show up as a log line on the device.
    src = Path(buddy.__file__).resolve().parents[1] / "firmware" / "src"
    headers = (src / "splash_animations.h").read_text() + (src / "buddy_animations.h").read_text()
    known = set(re.findall(r'\{"([^"]+)",\s*"(?:core|persona|buddy)"', headers))
    names = {buddy.pick((st, el, 0), OK) for st in range(10) for el in (10, 600, 4000)}
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


def test_fetch_anim_sidecar_down_releases():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await buddy.fetch_anim(c, 45999, OK)
    assert asyncio.run(go()) == ""


def test_fetch_anim_most_urgent_session_wins():
    body = _wire((6, 3, 0), (4, 1, 1), (1, 9000, 0))
    async def go():
        t = httpx.MockTransport(lambda r: httpx.Response(200, text=body))
        async with httpx.AsyncClient(transport=t) as c:
            return await buddy.fetch_anim(c, 45999, OK)
    assert asyncio.run(go()) == "allow"
