#!/usr/bin/env python3
"""Preview the Clawd buddy in a browser: pick a Claude Code state, watch the splash.

Reads the real firmware tables (firmware/src/splash_animations.h and
buddy_animations.h) and asks daemon/buddy.py which animation and which action text
each state maps to, so the page always matches what the daemon would send and the
device would play. The text is drawn under the art the way splash.cpp places its
label: grid row 50, centred, 24 px on a 480 px panel, in THEME_DIM.
Placement follows splash.cpp compose_stage(): a 55x37 art stage anchored at (2,11)
on the 60x60 grid, with the left/right edge snap. Playback holds the loop while a
state is selected, and a state change lets the current loop run out through its
outro (capped at the firmware's 1.5 s) before the new animation starts.

If juppee's fork is checked out (default ~/dev/oss/Clawdmeter), a second panel
shows what juppee's Session Browser build played for the same state.

Usage: python tools/buddy_preview.py [--out PATH] [--juppee PATH] [--no-open]
"""
import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import types
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JUPPEE_REV = "10a8082"
HOST_SWITCH_MAX_MS = 1500      # splash.cpp HOST_SWITCH_MAX_MS


def load_buddy():
    """Import daemon/buddy.py. httpx is only needed for fetch, so stub it if absent."""
    try:
        import httpx  # noqa: F401
    except ImportError:
        sys.modules["httpx"] = types.ModuleType("httpx")
    spec = importlib.util.spec_from_file_location("buddy", ROOT / "daemon" / "buddy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def states(b):
    """(label, juppee's original, animation, action text) per Claude Code state.

    Each row's `top` is the 4-tuple buddy.top_session() builds from the sidecar's
    /top answer: (state, elapsed_s, tool, detail).
    """
    ok = {"s": 40, "st": "allowed", "ok": True}
    out_of_quota = {"s": 100, "st": "allowed", "ok": True}
    bash = "Install package dependencies"
    rows = [
        ("Thinking", "work think", (b.THINKING, 5, None, None), ok),
        ("Responding", "write", (b.RESPONDING, 5, None, None), ok),
        ("Running Bash, with a description", "work coding",
         (b.RUNNING_TOOL, 5, "Bash", bash), ok),
        ("Running Bash, no description", "work coding",
         (b.RUNNING_TOOL, 5, "Bash", None), ok),
        ("Running Read", "work coding", (b.RUNNING_TOOL, 5, "Read", None), ok),
        ("Editing or writing a file", "write", (b.RUNNING_TOOL, 5, "Edit", None), ok),
        ("Running a connector tool (CLI name)", "work coding",
         (b.RUNNING_TOOL, 5, "mcp__claude_ai_Amplitude__query", None), ok),
        ("Compacting", "think", (b.COMPACTING, 5, None, None), ok),
        ("Waiting for permission, Bash", "allow",
         (b.WAITING_PERMISSION, 5, "Bash", bash), ok),
        ("Waiting for permission, Edit", "allow",
         (b.WAITING_PERMISSION, 5, "Edit", None), ok),
        ("Asking a question", "allow", (b.WAITING_QUESTION, 5, None, None), ok),
        ("Needs input", "done", (b.WAITING_INPUT, 5, None, None), ok),
        ("Error", "expression surprise", (b.ERROR, 5, None, None), ok),
        ("Out of quota", "limit", (b.THINKING, 5, None, None), out_of_quota),
        ("Starting", "idle look around", (b.STARTING, 0, None, None), ok),
        (f"Idle, under {b.JUST_DONE_S // 60} min", "done", (b.IDLE, 60, None, None), ok),
        (f"Idle, {b.JUST_DONE_S // 60} to {b.SLEEP_AFTER_S // 60} min", "idle breathe",
         (b.IDLE, b.JUST_DONE_S + 60, None, None), ok),
        (f"Idle, over {b.SLEEP_AFTER_S // 60} min", "expression sleep",
         (b.IDLE, b.SLEEP_AFTER_S + 60, None, None), ok),
        ("No session, or sidecar not running", "", None, ok),
    ]
    return [(label, orig, b.pick(top, usage), b.action_text(top, usage))
            for label, orig, top, usage in rows]


def nums(s):
    return [int(x, 0) for x in re.findall(r"0x[0-9A-Fa-f]+|\d+", s)]


def parse(text, table, src):
    """Animations from a generated splash header. `table` is the C array name."""
    arrays = {m.group(1): nums(m.group(2)) for m in re.finditer(
        r"static const uint(?:8|16)_t (\w+)(?:\[\w+\])+\s*(?:PROGMEM\s*)?=\s*\{(.*?)\};",
        text, re.S)}
    body = re.search(table + r"\[\w+\] = \{(.*?)\n\};", text, re.S).group(1)
    out = []
    for m in re.finditer(r'\{"([^"]+)",\s*"[^"]*",([^{}]*)\}', body):
        name = m.group(1)
        rest = [x.strip() for x in m.group(2).split(",") if x.strip()]
        if len(rest) == 11:   # w,h,ox,oy,count,loop_start,loop_end,pal_count,pal,frames,holds
            w, h, ox, oy, count, ls, le, _ = map(int, rest[:8])
            pal, frames, holds = rest[8:]
        else:                 # juppee's old format: count,pal,frames,holds (20x20, whole-file loop)
            count, (pal, frames, holds) = int(rest[0]), rest[1:]
            w = h = 20
            ox = oy = 0
            ls, le = 0, count - 1
        out.append(dict(name=name, src=src, w=w, h=h, ox=ox, oy=oy, count=count,
                        loop_start=ls, loop_end=le, palette=arrays[pal],
                        frames=arrays[frames], holds=arrays[holds]))
    return out


def juppee_anims(repo):
    try:
        text = subprocess.run(
            ["git", "-C", str(repo), "show", f"{JUPPEE_REV}:firmware/src/splash_animations.h"],
            capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return parse(text, "splash_anims", "juppee")


HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clawd buddy preview</title>
<style>
:root{--bg:#111;--panel:#1b1b1b;--text:#ddd;--muted:#999;--accent:#d97757;--line:#2c2c2c}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--text);font:14px/1.4 -apple-system,system-ui,sans-serif;margin:0;padding:16px}
h1{font-size:20px;margin:0 0 12px}h2{font-size:15px;margin:24px 0 8px;color:var(--muted)}
.layout{display:grid;grid-template-columns:260px 1fr;gap:20px;max-width:1300px}
@media (max-width:760px){.layout{grid-template-columns:1fr}}
.states{display:flex;flex-direction:column;gap:4px}
.states button{all:unset;cursor:pointer;padding:8px 10px;border-radius:8px;background:var(--panel);border:1px solid var(--line)}
.states button small{display:block;color:var(--muted);font-size:12px}
.states button[aria-pressed=true]{border-color:var(--accent);background:#2a1d18}
.row{display:flex;gap:20px;flex-wrap:wrap}
figure{margin:0;flex:1 1 300px;max-width:480px}
canvas{width:100%;aspect-ratio:1;background:#000;border-radius:16px;image-rendering:pixelated;display:block}
figcaption{margin-top:8px;color:var(--muted)}figcaption b{color:var(--text)}
code{background:var(--panel);padding:1px 5px;border-radius:4px;font-size:12px}
.opts{display:flex;gap:16px;flex-wrap:wrap;margin:0 0 12px;color:var(--muted)}
select{font:inherit;padding:4px;background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:6px;max-width:100%}
</style></head><body>
<h1>Clawd buddy preview</h1>
<div class="layout">
<nav class="states" id="states" aria-label="Claude Code state"></nav>
<main>
<div class="opts">
<label><input type="checkbox" id="smooth" checked> Switch like the device (outro first, 1.5 s cap)</label>
<label id="jscaleWrap">juppee scale <select id="jscale"><option>1</option><option selected>2</option></select></label>
</div>
<div class="row">
<figure><canvas id="a" width="480" height="480"></canvas><figcaption id="ca"></figcaption></figure>
<figure id="fb"><canvas id="b" width="480" height="480"></canvas><figcaption id="cb"></figcaption></figure>
</div>
<h2>Any animation</h2>
<p><select id="all"></select></p>
<figure><canvas id="c" width="480" height="480"></canvas><figcaption id="cc"></figcaption></figure>
</main></div>
<script>
const DATA = __DATA__;
const GRID = 60, CELL = 8, STAGE_W = 55, AX = 2, AY = 11;   // splash.cpp, 480 px panel
const TEXT_ROW0 = 50, TEXT_PX = 24, TEXT_MARGIN = 20, THEME_DIM = "#b0aea5";   // splash text label
const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const byKey = Object.fromEntries(DATA.anims.map(a => [a.src + ":" + a.name, a]));
const find = (name, srcs) => srcs.map(s => byKey[s + ":" + name]).find(Boolean);
const rgb = v => `rgb(${(v>>11&31)*255/31|0},${(v>>5&63)*255/63|0},${(v&31)*255/31|0})`;
let jScale = 2;

function player(canvasId, capId) {
  const ctx = document.getElementById(canvasId).getContext("2d");
  const cap = document.getElementById(capId);
  const p = {anim: null, text: "", frame: 0, t: 0, release: false, pending: undefined, deadline: 0, label: "",
    start(anim, label) {
      Object.assign(p, {anim, frame: 0, t: performance.now(), release: false, pending: undefined});
      cap.innerHTML = label; p.draw();
    },
    // Like splash_set_anim + switch_soon: let the loop run out, cut after the cap.
    // The text changes at once, as splash_set_text does; only the art waits for its outro.
    request(anim, label, smooth, text = "") {
      p.text = text;
      if (!smooth || !p.anim) return p.start(anim, label);
      if (anim === p.anim && p.pending === undefined) { cap.innerHTML = label; return p.draw(); }
      Object.assign(p, {release: true, pending: anim, pendingLabel: label,
                        deadline: performance.now() + DATA.switchMaxMs});
      p.draw();
    },
    draw() {
      ctx.fillStyle = "#000"; ctx.fillRect(0, 0, GRID*CELL, GRID*CELL);
      const a = p.anim;
      if (a) p.drawArt(a);
      p.drawText();
    },
    // Label box: x = 20, y = row 50 + 4 px, width = panel width - 40, one line cut with dots.
    drawText() {
      if (!p.text) return;
      ctx.font = `${TEXT_PX}px -apple-system, system-ui, sans-serif`;
      ctx.fillStyle = THEME_DIM; ctx.textAlign = "center"; ctx.textBaseline = "top";
      const room = GRID*CELL - 2*TEXT_MARGIN;
      let t = p.text;
      while (t.length > 1 && ctx.measureText(t).width > room) t = t.slice(0, -4) + "...";
      ctx.fillText(t, GRID*CELL / 2, TEXT_ROW0*CELL + 4);
    },
    drawArt(a) {
      let x0, y0, s = 1;
      if (a.src === "juppee") {                                 // old 20x20 frames, centred
        s = jScale; x0 = (GRID - a.w*s) / 2 | 0; y0 = (GRID - a.h*s) / 2 | 0;
      } else {                                                   // compose_stage()
        x0 = AX + a.ox; y0 = AY + a.oy;
        if (a.ox === 0) x0 = 0;
        if (a.ox + a.w === STAGE_W) x0 = GRID - a.w;
      }
      const base = p.frame * a.w * a.h;
      for (let y = 0; y < a.h; y++) for (let x = 0; x < a.w; x++) {
        const c = a.palette[a.frames[base + y*a.w + x]];
        if (!c) continue;                                        // 0x0000 = background
        ctx.fillStyle = rgb(c);
        ctx.fillRect((x0 + x*s)*CELL, (y0 + y*s)*CELL, s*CELL, s*CELL);
      }
    },
    tick(now) {
      if (p.pending !== undefined && now >= p.deadline) return p.start(p.pending, p.pendingLabel);
      const a = p.anim;
      if (!a || now - p.t < a.holds[p.frame]) return;
      p.t = now;
      let n = p.frame + 1;
      if (p.frame === a.loop_end && !p.release) n = a.loop_start;  // hold the loop
      if (n >= a.count) {
        if (p.pending !== undefined) return p.start(p.pending, p.pendingLabel);
        n = 0; p.release = false;
      }
      p.frame = n; p.draw();
    }};
  return p;
}

const A = player("a", "ca"), B = player("b", "cb"), C = player("c", "cc");
const smooth = () => document.getElementById("smooth").checked;
const hasJuppee = DATA.anims.some(a => a.src === "juppee");
if (!hasJuppee) { document.getElementById("fb").hidden = true; document.getElementById("jscaleWrap").hidden = true; }

function describe(a) {
  return `<b>${a.name}</b> (${a.src === "buddy" ? "claudepix buddy sprite" : a.src}), ${a.count} frames, loop ${a.loop_start}-${a.loop_end}`;
}
function showState(i) {
  const [label, orig, pick, text] = DATA.states[i];
  const xs = text ? `,"x":"${esc(text)}"` : `,"x":""`;
  document.querySelectorAll("#states button").forEach((b, j) => b.setAttribute("aria-pressed", j === i));
  const mine = pick ? find(pick, ["official", "buddy"]) : null;
  A.request(mine, mine ? `Device: ${describe(mine)}<br>Daemon sends <code>{"a":"${pick}"${xs}}</code>`
                       : `Device: no buddy, it rotates official art by usage rate<br>Daemon sends <code>{"a":""${xs}}</code>`, smooth(), text);
  if (hasJuppee) {
    const o = orig ? find(orig, ["juppee"]) : null;
    B.request(o, o ? `juppee's build: ${describe(o)}` : "juppee's build: nothing for this state", smooth());
  }
}
const nav = document.getElementById("states");
DATA.states.forEach(([label, , pick, text], i) => {
  const b = document.createElement("button");
  b.innerHTML = `${label}<small>${pick || "device rotation"}${text ? " / " + esc(text) : ""}</small>`;
  b.onclick = () => showState(i);
  nav.append(b);
});
document.getElementById("jscale").onchange = e => { jScale = +e.target.value; [A, B, C].forEach(p => p.draw()); };
const all = document.getElementById("all");
for (const src of ["official", "buddy", "juppee"]) {
  const list = DATA.anims.filter(a => a.src === src); if (!list.length) continue;
  const g = document.createElement("optgroup"); g.label = src;
  list.forEach(a => g.append(new Option(a.name, src + ":" + a.name)));
  all.append(g);
}
all.onchange = () => { const a = byKey[all.value]; C.start(a, describe(a)); };
showState(0); all.onchange();
(function loop(now) { [A, B, C].forEach(p => p.tick(now)); requestAnimationFrame(loop); })(performance.now());
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path,
                    default=Path(tempfile.gettempdir()) / "clawdmeter-buddy-preview.html")
    ap.add_argument("--juppee", type=Path, default=Path.home() / "dev/oss/Clawdmeter",
                    help="juppee's fork checkout, for the comparison panel")
    ap.add_argument("--no-open", action="store_true", help="don't open a browser")
    args = ap.parse_args()

    src = ROOT / "firmware" / "src"
    official = parse((src / "splash_animations.h").read_text(), "splash_anims", "official")
    buddy_sprites = parse((src / "buddy_animations.h").read_text(), "buddy_anims", "buddy")
    juppee = juppee_anims(args.juppee)
    data = {"anims": official + buddy_sprites + juppee, "states": states(load_buddy()),
            "switchMaxMs": HOST_SWITCH_MAX_MS}

    known = {a["name"] for a in official + buddy_sprites}
    missing = sorted({pick for _, _, pick, _ in data["states"] if pick and pick not in known})
    if missing:
        sys.exit(f"buddy.py names animations the firmware lacks: {', '.join(missing)}")

    args.out.write_text(HTML.replace("__DATA__", json.dumps(data, separators=(",", ":"))))
    print(f"wrote {args.out} ({len(official)} official, {len(buddy_sprites)} buddy, "
          f"{len(juppee)} juppee animations)")
    if not args.no_open:
        webbrowser.open(args.out.as_uri())


if __name__ == "__main__":
    main()
