# -*- coding: utf-8 -*-
"""
验证官方按键字段：demoparser2 README 里列出的那组 True/False 布尔字段。

官方文档（LaihoE/demoparser README）写的是：
    Buttons  True/False if player is pressing button.
      FORWARD / LEFT / RIGHT / BACK / FIRE / RIGHTCLICK
      RELOAD / INSPECT / USE / ZOOM / SCOREBOARD

  —— 都来自 m_nButtonDownMaskPrev，parser 已经帮你解码成布尔值了。
     我手写位掩码是多余的。这里验证真伪 + 覆盖度。

同时验证 LovelyO0Sam 的做法：用状态属性判断走/蹲/跳，
    is_walking / duck_amount / velocity_Z / is_airborne

输出 logs/verify_official.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "logs" / "verify_official.txt"

DEMO = sys.argv[1]
TARGET = sys.argv[2] if len(sys.argv) > 2 else "Twistzz"

# 官方 README 里列出的按键布尔字段（大写名）
OFFICIAL_BUTTONS = [
    "FORWARD", "LEFT", "RIGHT", "BACK", "FIRE", "RIGHTCLICK",
    "RELOAD", "INSPECT", "USE", "ZOOM", "SCOREBOARD", "WALK",
    "JUMP", "DUCK", "SPRINT",
]
# 小写变体也试
OFFICIAL_LOWER = [b.lower() for b in OFFICIAL_BUTTONS]

# LovelyO0Sam 用的状态属性
STATE_PROPS = [
    "is_walking", "duck_amount", "velocity_Z", "is_airborne",
    "is_defusing", "health", "is_alive", "total_rounds_played",
    "yaw", "pitch", "X", "Y",
]

log: list[str] = []
def L(s=""):
    log.append(str(s))


def main():
    from demoparser2 import DemoParser
    p = DemoParser(DEMO)

    deaths = p.parse_event("player_death")
    name_map = {str(r.get("attacker_name")): int(r["attacker_steamid"])
                for _, r in deaths.iterrows()}
    pid = name_map.get(TARGET)
    L(f"demo={DEMO}")
    L(f"player={TARGET} id={pid}")

    # ---- 1. 试官方大写按键名 ----
    L("")
    L("=" * 62)
    L("1. 官方按键字段（大写 FORWARD/LEFT/RIGHT/BACK/FIRE...）")
    L("=" * 62)
    ok_upper, bad_upper = [], []
    for k in OFFICIAL_BUTTONS:
        try:
            df = p.parse_ticks([k], players=[pid])
            col = [c for c in df.columns if c != "tick"]
            ok_upper.append(k)
            L(f"  [OK] {k:<12} cols={col} dtype={df[col[0]].dtype} "
              f"true={int(df[col[0]].fillna(False).astype(bool).sum())}")
        except Exception as e:
            bad_upper.append(k)
            L(f"  [--] {k:<12} {str(e)[:60]}")

    # ---- 2. 小写变体 ----
    L("")
    L("=" * 62)
    L("2. 小写变体")
    L("=" * 62)
    has_lower = {}
    for k in OFFICIAL_LOWER:
        try:
            df = p.parse_ticks([k], players=[pid])
            col = [c for c in df.columns if c != "tick"][0]
            has_lower[k] = df
            L(f"  [OK] {k:<12} dtype={df[col].dtype} "
              f"true={int(df[col].fillna(False).astype(bool).sum())}")
        except Exception as e:
            L(f"  [--] {k:<12} {str(e)[:60]}")

    # ---- 3. 状态属性（LovelyO0Sam 路线）----
    L("")
    L("=" * 62)
    L("3. 状态属性（判断 走/蹲/跳 用）")
    L("=" * 62)
    ok_state = {}
    for k in STATE_PROPS:
        try:
            df = p.parse_ticks([k], players=[pid])
            col = [c for c in df.columns if c != "tick"][0]
            ok_state[k] = df[col]
            s = df[col]
            L(f"  [OK] {k:<20} dtype={str(s.dtype):<9} "
              f"min={s.min()} max={s.max()} nan={int(s.isna().sum())}")
        except Exception as e:
            L(f"  [--] {k:<20} {str(e)[:60]}")

    # ---- 4. 一致性：官方 FIRE vs weapon_fire 事件 ----
    L("")
    L("=" * 62)
    L("4. 官方 FIRE 字段 vs weapon_fire 事件（对齐率）")
    L("=" * 62)
    fires = p.parse_event("weapon_fire")
    fires = fires[fires["user_name"] == TARGET]
    ftick = sorted(set(fires["tick"].astype(int).tolist()))
    L(f"  weapon_fire 事件数: {len(ftick)}")

    for key in ("forward", "left", "right", "back", "fire", "duck", "jump", "walk"):
        if key not in has_lower:
            continue
        d = has_lower[key]
        col = [c for c in d.columns if c != "tick"][0]
        # 上升沿
        v = d[col].fillna(False).astype(bool)
        tk = d["tick"].astype(int)
        n_true = int(v.sum())
        up = int(((v) & (~v.shift(1).fillna(False))).sum())
        L(f"  {key:<10} 按下tick={n_true:>7}  上升沿={up:>6}")

    # FIRE 上升沿 vs weapon_fire
    if "fire" in has_lower:
        d = has_lower["fire"]
        col = [c for c in d.columns if c != "tick"][0]
        v = d[col].fillna(False).astype(bool)
        tk = d["tick"].astype(int)
        up_ticks = set(tk[v & (~v.shift(1).fillna(False))].tolist())
        hit = sum(1 for t in ftick if any((t + k) in up_ticks for k in range(-2, 8)))
        L("")
        L(f"  FIRE 上升沿 vs weapon_fire: {hit}/{len(ftick)} "
          f"= {hit/max(len(ftick),1)*100:.1f}%")

    # ---- 5. duck_amount 分布（验证蹲）----
    L("")
    L("=" * 62)
    L("5. duck_amount（蹲）与 velocity_Z（跳）取值分布")
    L("=" * 62)
    if "duck_amount" in ok_state:
        d = ok_state["duck_amount"]
        L(f"  duck_amount > 0.5 的 tick: {int((d>0.5).sum())}")
        L(f"  duck_amount > 0.1 的 tick: {int((d>0.1).sum())}")
        L(f"  取值样例: {sorted(d.dropna().unique())[:12]}")
    if "velocity_Z" in ok_state:
        z = ok_state["velocity_Z"]
        L(f"  velocity_Z > 50 (上升中): {int((z>50).sum())}")
        L(f"  velocity_Z < -50(下落中): {int((z<-50).sum())}")
    if "is_airborne" in ok_state:
        a = ok_state["is_airborne"]
        L(f"  is_airborne=True 的 tick: {int(a.fillna(False).astype(bool).sum())}")
    if "is_walking" in ok_state:
        w = ok_state["is_walking"]
        L(f"  is_walking=True 的 tick: {int(w.fillna(False).astype(bool).sum())}")

    OUT.write_text("\n".join(log), encoding="utf-8")
    print("WROTE", OUT)


if __name__ == "__main__":
    main()
