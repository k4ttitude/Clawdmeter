"""Tests for tools/import_buddy_sprites.py.

Run: daemon/.venv/bin/python -m pytest tools/tests -q
"""
import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "import_buddy_sprites", ROOT / "tools" / "import_buddy_sprites.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Two 20x20 frames. Index 1 is black (0x0000) like the CSB sprites' filler,
# index 2 is the body colour. Body: a 2x3 block at rows 9-10, cols 8-10.
def _frame(body_cols):
    cells = [1] * 400
    for r in (9, 10):
        for c in body_cols:
            cells[r * 20 + c] = 2
    return "{" + ",".join(map(str, cells)) + "}"

FIXTURE = f"""
static const uint16_t splash_csb_x_palette[10] = {{0x0000,0x0000,0xDBAA,0x0000,0x0000,0x0000,0x0000,0x0000,0x0000,0x0000}};
static const uint8_t splash_csb_x_frames[2][400] = {{
    {_frame((8, 9, 10))},
    {_frame((9, 10, 11))},
}};
static const uint16_t splash_csb_x_holds[2] = {{180,240}};
#define SPLASH_ANIM_COUNT 1
static const splash_anim_def_t splash_anims[SPLASH_ANIM_COUNT] = {{
    {{"x", "Session Browser", 2, splash_csb_x_palette, splash_csb_x_frames, splash_csb_x_holds}},
}};
"""


def test_parse_header_reads_table_entry():
    [a] = mod.parse_header(FIXTURE)
    assert a.name == "x"
    assert len(a.frames) == 2 and len(a.frames[0]) == 400
    assert a.holds == [180, 240]
    assert a.palette[2] == 0xDBAA


def test_black_indices_become_background():
    c = mod.convert(mod.parse_header(FIXTURE)[0], scale=1)
    assert c.palette[0] == 0x0000
    assert c.palette[1] == 0xDBAA          # compacted: body is now index 1
    assert set(c.frames[0]) <= {0, 1}


def test_crop_is_union_bbox_across_frames_then_scaled():
    c = mod.convert(mod.parse_header(FIXTURE)[0], scale=2)
    # union cols 8..11 (4 wide), rows 9..10 (2 tall), x2
    assert (c.w, c.h) == (8, 4)
    assert len(c.frames) == 2 and all(len(f) == 32 for f in c.frames)


def test_centred_and_standing_on_the_official_ground_line():
    c = mod.convert(mod.parse_header(FIXTURE)[0], scale=2)
    # splash.cpp draws at x = (60-55)/2 + ox, y = (60-37)/2 + oy. Official Clawd
    # stands on the stage bottom (grid row 48), so buddy feet go there too.
    assert 2 + c.ox == (60 - c.w) // 2
    assert 11 + c.oy + c.h == 48
    assert c.ox > 0 and c.ox + c.w != 55   # avoid compose_stage's edge pins


def test_loop_region_is_whole_file():
    c = mod.convert(mod.parse_header(FIXTURE)[0], scale=2)
    assert (c.loop_start, c.loop_end) == (0, 1)


def test_select_keeps_order_and_rejects_missing():
    anims = mod.parse_header(FIXTURE)
    assert [a.name for a in mod.select(anims, ("x",))] == ["x"]
    try:
        mod.select(anims, ("x", "nope"))
    except KeyError as e:
        assert "nope" in str(e)
    else:
        raise AssertionError("missing name must raise")


def test_real_source_converts_kept_four_and_compiles(tmp_path):
    src = (ROOT / "research/buddy-sprites/csb_splash_animations.h").read_text()
    anims = mod.parse_header(src)
    assert len(anims) == 18
    kept = mod.select(anims, mod.KEEP)
    assert [a.name for a in kept] == ["allow", "limit", "expression surprise", "expression sleep"]
    out = tmp_path / "buddy_animations.h"
    out.write_text(mod.emit([mod.convert(a, scale=2) for a in kept]))
    probe = tmp_path / "probe.cpp"
    probe.write_text('#include "splash_animations.h"\n#include "buddy_animations.h"\n'
                     'static_assert(BUDDY_ANIM_COUNT == 4, "count");\n'
                     'int main() { return buddy_anims[0].w > 0 ? 0 : 1; }\n')
    subprocess.run(["c++", "-std=c++17", "-fsyntax-only",
                    "-I", str(ROOT / "firmware/src"), "-I", str(tmp_path), str(probe)],
                   check=True)
