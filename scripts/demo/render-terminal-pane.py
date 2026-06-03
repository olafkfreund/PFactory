#!/usr/bin/env python3
"""Render a scenario-accurate "Claude Code → PFactory" terminal pane.

Reads the real test_plan.json + verdicts.json from a scenario's workspace and
emits an HTML terminal (for headless-Chrome screenshot) showing that scenario's
actual handover, pipeline phases, and verdict counts — so each demo's terminal
pane matches its own run instead of reusing another scenario's recording.

Usage:
  render-terminal-pane.py <workspace_dir> <command> <out.html>
      [--frameworks "pytest, jest"] [--lane-note "..."]
"""
import argparse
import html
import json
from collections import Counter
from pathlib import Path


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


# Self-playing line-reveal: each body line cascades in, then a blinking cursor
# holds. Captured by Playwright recordVideo to make an animated terminal pane.
_ANIM_CSS = """
.wrap > *:not(.bar){opacity:0;transform:translateY(3px)}
.wrap > *.shown{opacity:1;transform:none;transition:opacity .2s ease,transform .2s ease}
#cur{display:inline-block;width:10px;height:20px;background:#58a6ff;vertical-align:-4px;
  margin-left:4px;opacity:0;animation:blink 1.05s steps(1) infinite}
#cur.on{opacity:1}
@keyframes blink{50%{opacity:0}}
"""

_ANIM_JS = """
<script>
(function(){
  var items=[].slice.call(document.querySelectorAll('.wrap > *:not(.bar)'));
  var cur=document.createElement('span'); cur.id='cur';
  document.querySelector('.wrap').appendChild(cur);
  var k=0;
  (function step(){
    if(k>=items.length){ cur.className='on'; return; }
    items[k].classList.add('shown'); k++;
    setTimeout(step, 430);
  })();
})();
</script>
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace")
    ap.add_argument("command")
    ap.add_argument("out")
    ap.add_argument("--lane-note", default="")
    ap.add_argument(
        "--animate",
        action="store_true",
        help="emit a self-playing typewriter/line-reveal terminal (for video capture)",
    )
    args = ap.parse_args()

    ws = Path(args.workspace)
    plan = _load(ws / "test_plan.json")
    verdicts = _load(ws / "findings" / "verdicts.json").get("verdicts", [])

    subs = [s for ph in plan.get("phases", []) for s in ph.get("subtasks", [])]
    n_sub = len(subs)
    fw = Counter(s.get("framework") for s in subs if s.get("framework"))
    fw_str = ", ".join(f"{k}×{v}" for k, v in fw.items()) or "—"
    langs = sorted({s.get("language") for s in subs if s.get("language")})
    lang_str = " + ".join(langs) if langs else "—"

    counts = Counter(v.get("verdict") for v in verdicts)
    n_acc, n_rej, n_flag = counts.get("accept", 0), counts.get("reject", 0), counts.get("flag", 0)
    verdict_bits = []
    if n_acc:
        verdict_bits.append(f'<span class="g">{n_acc} accept</span>')
    if n_rej:
        verdict_bits.append(f'<span class="r">{n_rej} reject</span>')
    if n_flag:
        verdict_bits.append(f'<span class="y">{n_flag} flag</span>')
    verdict_str = " · ".join(verdict_bits) or "—"

    proj = plan.get("project_id") or ws.parts[-3] if len(ws.parts) >= 3 else "?"
    spec = ws.name

    def row(dot, name, detail):
        return (
            f'<div class="row"><span class="g">●</span> '
            f'<span class="ph">{html.escape(name):<16}</span>'
            f'<span class="dim">{detail}</span></div>'
        )

    body = f"""<div class="line"><span class="p">❯</span> {html.escape(args.command)}</div>
<div class="dim sp">  project <b>{html.escape(str(proj))}</b> · spec <b>{html.escape(spec)}</b></div>
<div class="sp"></div>
{row('●','Planner', f'{n_sub} subtasks <span class=dim>({html.escape(fw_str)})</span>')}
{row('●','Gen-Functional', f'generated {n_sub} tests <span class=dim>({html.escape(lang_str)})</span>')}
{row('●','Executor', 'sandboxed in Docker')}
{row('●','Evaluator', 'coverage · 3× stability · mutation · semantic')}
{row('●','Triager', verdict_str)}
<div class="sp"></div>
<div class="line"><span class="g">✓ triaged</span> <span class="dim">→ findings/triage_report.md</span></div>
{f'<div class="dim sp">{html.escape(args.lane_note)}</div>' if args.lane_note else ''}"""

    out = f"""<!doctype html><html><head><meta charset=utf-8><style>
html,body{{margin:0;background:#0d1117;color:#c9d1d9;
  font-family:'DejaVu Sans Mono','JetBrains Mono',monospace;font-size:21px;line-height:1.6}}
.wrap{{padding:26px 30px}}
.bar{{color:#8b949e;font-size:16px;margin-bottom:14px;border-bottom:1px solid #21262d;padding-bottom:8px}}
.dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}}
.p{{color:#58a6ff;font-weight:700}} .g{{color:#3fb950}} .r{{color:#f85149}} .y{{color:#d29922}}
.dim{{color:#6e7681}} .ph{{color:#c9d1d9;white-space:pre}} .row{{margin:3px 0}} .sp{{margin-top:10px}}
b{{color:#58a6ff;font-weight:600}}
{_ANIM_CSS if args.animate else ''}
</style></head><body><div class="wrap">
<div class="bar">● ● ●&nbsp;&nbsp;Claude Code — PFactory handover</div>
{body}
</div>{_ANIM_JS if args.animate else ''}</body></html>"""
    Path(args.out).write_text(out)
    print(f"terminal pane → {args.out}  ({n_sub} subtasks, {verdict_str})")


if __name__ == "__main__":
    main()
