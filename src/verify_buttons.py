# -*- coding: utf-8 -*-
"""
验证：buttons 掩码到底解码出什么，能不能还原 WASD / 左键。

核心问题：
  - `buttons` 这个 prop 的位定义是什么？
  - 它和 weapon_fire 事件、和玩家位移方向能否对上？

做法：
  1. 拉某个选手全场的 tick 流（buttons / X,Y / yaw / is_alive / total_rounds_played）
  2. 统计 buttons 出现过的所有取值（十六进制），反推位定义
  3. 逐位分解：每一位置 1 时，玩家在干什么（位移方向 / 是否开火）
  4. 交叉验证：左键位 vs weapon_fire 事件的时间对齐率

输出 logs/verify_buttons.txt
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "logs" / "verify_buttons.txt"

DEMO = sys.argv[1] if len(sys.argv) > 1 else None
TARGET = sys.argv[2] if len(sys.argv) > 2 else "Twistzz"

log: list[str] = []
def L(s=""):
    log.append(str(s))

# CS2 / Source2 按键位定义（公开的 usercmd buttons 枚举）
BIT_NAMES = {
    0: "IN_ATTACK",
    1: "IN_JUMP",
    2: "IN_DUCK",
    3: "IN_FORWARD",
    4: "IN_BACK",
    5: "IN_USE",
    6: "IN_CANCEL",
    7: "IN_LEFT",
    8: "IN_RIGHT",
    9: "IN_MOVELEFT",
    10: "IN_MOVERIGHT",
    11: "IN_ATTACK2",
    12: "IN_RUN",
    13: "IN_RELOAD",
    14: "IN_ALT1",
    15: "IN_ALT2",
    16: "IN_SCORE",
    17: "IN_SPEED",
    18: "IN_WALK",
    19: "IN_ZOOM",
    20: "IN_WEAPON1",
    21: "IN_WEAPON2",
    22: "IN_BULLRUSH",
    23: "IN_GRENADE1",
    24: "IN_GRENADE2",
    25: "IN_ATTACK3",
}


def main():
    from demoparser2 import DemoParser

    p = DemoParser(DEMO)
    L(f"demo={DEMO}  target={TARGET}")

    allp = p.parse_event("player_death")
    name_map = {str(r.get("attacker_name")): int(r["attacker_steamid"])
                for _, r in allp.iterrows()}
    pid = name_map.get(TARGET)
    L(f"playerid={pid}")
    if pid is None:
        L("[!] no such player")
        OUT.write_text("\n".join(log), encoding="utf-8")
        return

    cols = ["buttons", "X", "Y", "yaw", "pitch", "is_alive",
            "total_rounds_played", "is_freeze_period", "game_phase",
            "active_weapon", "health"]
    df = p.parse_ticks(cols, players=[pid])
    L(f"ticks={len(df)}")

    btn = df["buttons"].fillna(0).astype("int64")
    L("")
    L("=" * 60)
    L("A. buttons 取值分布")
    L("=" * 60)
    cnt = Counter(btn.tolist())
    for val, n in cnt.most_common(30):
        bits = [BIT_NAMES.get(i, f"bit{i}") for i in range(32) if val & (1 << i)]
        L(f"  0x{val:08X} ({val:>12})  n={n:>7}  {bits}")

    L("")
    L("=" * 60)
    L("B. 每个 bit 的触发频次（全 tick）")
    L("=" * 60)
    for i in range(32):
        n = int((btn & (1 << i) > 0).sum())
        if n:
            L(f"  bit{i:>2} {BIT_NAMES.get(i,'?'):<12} n={n:>7} "
              f"({n/len(df)*100:.2f}%)")

    # ---------------- 位移方向 vs IN_FORWARD/BACK/MOVELEFT/MOVERIGHT ----------------
    L("")
    L("=" * 60)
    L("C. 移动键位 vs 实际位移方向（验证 WASD 映射）")
    L("=" * 60)

    alive = df[df["is_alive"] == True].copy() if "is_alive" in df else df.copy()
    L(f"  存活 tick 数: {len(alive)}")

    # 计算相邻 tick 位移
    a = df.copy()
    a["dx"] = a["X"].diff()
    a["dy"] = a["Y"].diff()
    a["dist"] = np.hypot(a["dx"], a["dy"])
    moving = a[(a["dist"] > 0.5) & (a["is_alive"] == True)]
    L(f"  有效位移 tick 数(>0.5单位): {len(moving)}")

    # yaw 是视角朝向；把位移投影到「前/右」两个轴上
    # Source 坐标系: yaw=0 朝 +X, 逆时针为正
    yaw_rad = np.deg2rad(moving["yaw"].to_numpy())
    dx, dy = moving["dx"].to_numpy(), moving["dy"].to_numpy()
    # 前向单位向量
    fx, fy = np.cos(yaw_rad), np.sin(yaw_rad)
    # 右向 = 前向顺时针 90°
    rx, ry = np.sin(yaw_rad), -np.cos(yaw_rad)

    m_btn = moving["buttons"].fillna(0).astype("int64").to_numpy()
    fwd_proj = dx * fx + dy * fy      # >0 表示朝前走
    right_proj = dx * rx + dy * ry    # >0 表示朝右走

    IN_FORWARD = 1 << 3
    IN_BACK = 1 << 4
    IN_MOVELEFT = 1 << 9
    IN_MOVERIGHT = 1 << 10
    IN_ATTACK = 1 << 0

    def stat(name, mask):
        sel = (m_btn & mask) > 0
        if sel.sum() == 0:
            L(f"  {name:<14} 触发 0 次")
            return
        L(f"  {name:<14} n={sel.sum():>7}  "
          f"前向投影均值={fwd_proj[sel].mean():+.3f}  "
          f"右向投影均值={right_proj[sel].mean():+.3f}")

    stat("IN_FORWARD", IN_FORWARD)
    stat("IN_BACK", IN_BACK)
    stat("IN_MOVELEFT", IN_MOVELEFT)
    stat("IN_MOVERIGHT", IN_MOVERIGHT)

    L("  说明：前向投影>0 说明该按键确实在「朝视角前方移动」，")
    L("        右向投影>0 说明朝视角右方移动。符合即为映射成立。")

    # ---------------- 左键 vs weapon_fire ----------------
    L("")
    L("=" * 60)
    L("D. IN_ATTACK(bit0) vs weapon_fire 事件 交叉验证")
    L("=" * 60)

    fires = p.parse_event("weapon_fire")
    fires = fires[fires["user_name"] == TARGET]
    # 只保留步枪/手枪等真正靠左键触发的（去掉刀、雷的投掷）
    fire_ticks = set(int(t) for t in fires["tick"].tolist())
    L(f"  weapon_fire 条数(全武器): {len(fire_ticks)}")

    attack_ticks = set(df.loc[(btn & IN_ATTACK) > 0, "tick"].astype(int).tolist())
    L(f"  IN_ATTACK 触发 tick 数: {len(attack_ticks)}")

    # 每个武器开火 tick，往前 8 tick 内是否有 IN_ATTACK
    hit = 0
    miss = []
    for t in sorted(fire_ticks):
        if any((t - k) in attack_ticks for k in range(0, 9)):
            hit += 1
        else:
            miss.append(t)
    L(f"  开火 tick 前 8 tick 内命中 IN_ATTACK: {hit}/{len(fire_ticks)} "
      f"= {hit/max(len(fire_ticks),1)*100:.1f}%")
    if miss[:10]:
        L(f"  未命中样例 tick: {miss[:10]}")

    # 反向：IN_ATTACK 却没有任何开火（多半是刀/雷/开关镜）
    orph = []
    for t in sorted(attack_ticks):
        if not any((t + k) in fire_ticks for k in range(0, 9)):
            orph.append(t)
    L(f"  IN_ATTACK 但 8 tick 内无 weapon_fire: {len(orph)}/{len(attack_ticks)}"
      f" = {len(orph)/max(len(attack_ticks),1)*100:.1f}%")

    # ---------------- 一回合切片样例 ----------------
    L("")
    L("=" * 60)
    L("E. 样例：某一回合的按键时间线（找一段有动作的）")
    L("=" * 60)

    rounds = sorted(df["total_rounds_played"].dropna().unique())
    L(f"  总回合数: {len(rounds)}")
    for r in rounds[:14]:
        sub = df[df["total_rounds_played"] == r]
        b = sub["buttons"].fillna(0).astype("int64")
        nz = int((b > 0).sum())
        # 统计该回合出现的键
        bitset = set()
        for v in b.tolist():
            for i in range(32):
                if v & (1 << i):
                    bitset.add(BIT_NAMES.get(i, f"bit{i}"))
        L(f"  回合 {int(r):>2}  tick[{int(sub['tick'].min())}-{int(sub['tick'].max())}]"
          f"  有按键 tick={nz:>5}/{len(sub):<5} 键={sorted(bitset)}")

    OUT.write_text("\n".join(log), encoding="utf-8")
    print("WROTE", OUT)


if __name__ == "__main__":
    main()
