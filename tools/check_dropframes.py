# -*- coding: utf-8 -*-
"""核查：录制时卡顿有没有导致成片丢帧。

思路：
  1. 逐段 ffprobe 出真实时长与帧数
  2. 由 tick 区间 + tickrate 算出「理论时长/帧数」
  3. 对比二者的偏差 —— 偏差大 = 录制时掉帧
"""
import re
import subprocess
from pathlib import Path

FFPROBE = str(Path(__file__).resolve().parent
              / "ffmpeg-9.0.1-essentials_build" / "bin" / "ffprobe.exe")
D = Path(__file__).resolve().parent.parent / "output" / "videos"
TICK_RE = re.compile(r"tick-?(\d+)-to-?(\d+)", re.IGNORECASE)
TICKRATE = 64          # CS2 官方服 64 tick
FPS = 60


def probe(p):
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=nb_frames,r_frame_rate,duration",
         "-show_entries", "format=duration",
         "-of", "default=nw=1", str(p)],
        capture_output=True, text=True)
    d = {}
    for ln in r.stdout.splitlines():
        if "=" in ln:
            k, _, v = ln.partition("=")
            d[k.strip()] = v.strip()
    return d


parts = sorted([p for p in D.glob("sequence-*.mp4") if "_keyviz" not in p.stem],
               key=lambda p: int(TICK_RE.search(p.stem).group(1)))
print(f"{'段':>3} {'tick 区间':>20} {'理论帧':>7} {'实际帧':>7} {'差':>6} "
      f"{'理论s':>7} {'实际s':>7} {'帧率':>7}")
print("-" * 78)

tot_theory = tot_real = 0
for i, p in enumerate(parts, 1):
    m = TICK_RE.search(p.stem)
    t0, t1 = int(m.group(1)), int(m.group(2))
    theory_f = round((t1 - t0) / TICKRATE * FPS)
    theory_s = (t1 - t0) / TICKRATE
    info = probe(p)
    real_f = int(info.get("nb_frames", 0) or 0)
    real_s = float(info.get("duration", 0) or 0)
    rate = info.get("r_frame_rate", "?")
    tot_theory += theory_f
    tot_real += real_f
    diff = real_f - theory_f
    flag = "" if abs(diff) <= 2 else "  ← 偏差大"
    print(f"{i:>3} {t0:>9}->{t1:<9} {theory_f:>7} {real_f:>7} {diff:>+6} "
          f"{theory_s:>7.2f} {real_s:>7.2f} {rate:>7}{flag}")

print("-" * 78)
print(f"合计: 理论 {tot_theory} 帧 / 实际 {tot_real} 帧  "
      f"差异 {tot_real - tot_theory:+d} 帧 "
      f"({(tot_real-tot_theory)/max(1,tot_theory)*100:+.2f}%)")

final = D / "keyviz_final.mp4"
if final.exists():
    fi = probe(final)
    print(f"\n成品 keyviz_final.mp4: {fi.get('nb_frames')} 帧, "
          f"{float(fi.get('duration',0)):.2f}s, {fi.get('r_frame_rate')}")
    print(f"→ 成品帧数 vs 分段合计差: "
          f"{int(fi.get('nb_frames',0)) - tot_real:+d} 帧")
