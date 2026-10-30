# -*- coding: utf-8 -*-
"""
探针 2：搞清 bit0(IN_ATTACK) 空触发的成因，以及鼠标左键的正确还原方式。

假设：
  H1. IN_ATTACK 里 88.7% 是「刀/雷/道具」的挥砍投掷，不是枪械开火
  H2. 剩下的是「瞄人点右键」误判 —— 需要看 IN_ATTACK2
  H3. 「真·左键点击」应该用 weapon_fire 事件（服务器确认）而非掩码

验证：
  1. weapon_fire 的武器分布
  2. IN_ATTACK 高位时 active_weapon 是什么
  3. 用「掩码上升沿 + 武器=枪 + 弹药减少」三重判定，看能否逼近真值
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "logs" / "verify_attack.txt"

DEMO = sys.argv[1]
TARGET = sys.argv[2] if len(sys.argv) > 2 else "Twistzz"

log: list[str] = []
def L(s=""):
    log.append(str(s))


def main():
    from demoparser2 import DemoParser
    p = DemoParser(DEMO)

    allp = p.parse_event("player_death")
    name_map = {str(r.get("attacker_name")): int(r["attacker_steamid"])
                for _, r in allp.iterrows()}
    pid = name_map[TARGET]

    # ---- 1. weapon_fire 武器分布 ----
    L("=" * 60)
    L("1. weapon_fire 武器分布")
    L("=" * 60)
    fires = p.parse_event("weapon_fire")
    mine = fires[fires["user_name"] == TARGET]
    L(f"总数: {len(mine)}")
    for w, n in Counter(mine["weapon"].tolist()).most_common():
        L(f"  {w:<34} {n}")

    # ---- 2. IN_ATTACK 时的 active_weapon ----
    L("")
    L("=" * 60)
    L("2. IN_ATTACK(bit0) 置位时，手上拿的是什么")
    L("=" * 60)
    df = p.parse_ticks(
        ["buttons", "active_weapon", "X", "Y", "is_alive",
         "total_rounds_played", "health"],
        players=[pid],
    )
    btn = df["buttons"].fillna(0).astype("int64")
    atk = df[(btn & 1) > 0]
    L(f"IN_ATTACK 触发 tick: {len(atk)}")
    for w, n in Counter(atk["active_weapon"].dropna().tolist()).most_common(20):
        L(f"  itemdef={w:<12} n={n}")

    # ---- 3. 武器名映射（itemdef -> 名字）----
    L("")
    L("=" * 60)
    L("3. active_weapon 与 weapon_fire 的武器名能否对上")
    L("=" * 60)
    # 看 weapon_fire 那一 tick 的 active_weapon 值
    fw = mine.copy()
    tick2aw = dict(zip(df["tick"].astype(int), df["active_weapon"]))
    pairs = Counter()
    for t in fw["tick"].astype(int):
        aw = tick2aw.get(t)
        for k in range(0, 4):
            if aw is None and (t - k) in tick2aw:
                aw = tick2aw[t - k]
                break
        pairs[(str(aw), str(fw[fw["tick"].astype(int) == t]["weapon"].iloc[0]))] += 1
    L("  (active_weapon, weapon_fire中的武器) -> 次数")
    for (aw, w), n in pairs.most_common(25):
        L(f"  {aw:<14} {w:<32} {n}")

    # ---- 4. 上升沿检测 ----
    L("")
    L("=" * 60)
    L("4. IN_ATTACK 上升沿（新按下的那一下）")
    L("=" * 60)
    prev = btn.shift(1).fillna(0).astype("int64")
    rising = df[((btn & 1) > 0) & ((prev & 1) == 0)]
    falling = df[((btn & 1) == 0) & ((prev & 1) > 0)]
    L(f"  上升沿(按下瞬间) tick 数: {len(rising)}")
    L(f"  下降沿(松开瞬间) tick 数: {len(falling)}")

    # 上升沿 vs weapon_fire 命中率
    ftick = set(mine["tick"].astype(int).tolist())
    rset = set(rising["tick"].astype(int).tolist())
    hit = sum(1 for t in ftick if any((t + k) in rset for k in range(-2, 6)))
    L(f"  weapon_fire 前 6 tick 内有上升沿: {hit}/{len(ftick)} "
      f"= {hit/max(len(ftick),1)*100:.1f}%")

    orph = sum(1 for t in rset if not any((t - k) in ftick for k in range(0, 8)))
    L(f"  上升沿但之后 7 tick 内无 weapon_fire: {orph}/{len(rset)} "
      f"= {orph/max(len(rset),1)*100:.1f}%")

    L("")
    L("  上升沿时的手上武器分布:")
    for w, n in Counter(rising["active_weapon"].dropna().tolist()).most_common(15):
        L(f"    itemdef={w:<12} n={n}")

    OUT.write_text("\n".join(log), encoding="utf-8")
    print("WROTE", OUT)


if __name__ == "__main__":
    main()
