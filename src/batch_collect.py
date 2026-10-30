# -*- coding: utf-8 -*-
"""可续跑批量采集：只处理缺失部分。

- 已有历史但多杀不全 → 只补多杀
- 完全没有 → 全流程
- 已完成 → 跳过

进度写 <项目根>\\_progress.txt（项目根由 __file__ 推导，改名不会失效）
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from collector import collect_player          # noqa: E402
from faceit_client import FaceitClient        # noqa: E402
from backfill_multikills import get_multikills  # noqa: E402

# 需要处理的选手（已完成的会被自动跳过）
TODO = [
    ("niko", 3000),
    ("sh1ro", 3000), ("ZywOo", 3000), ("broky", 2800),
    ("karrigan", 2800), ("Twistzz", 2800), ("ropz", 2200),
]

PROG = ROOT / "_progress.txt"
DATA = ROOT / "data"
LOG = []


def w(msg):
    LOG.append(msg)
    PROG.write_text("\n".join(LOG[-60:]), encoding="utf-8")


def save(nick, res):
    (DATA / f"{nick}.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")


t0 = time.time()
client = FaceitClient(rps=2)
done = 0

for idx, (nick, expect) in enumerate(TODO, 1):
    path = DATA / f"{nick}.json"
    res = None

    # 1) 已有数据 → 复用
    if path.exists():
        try:
            res = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            res = None

    # 2) 没有历史 → 采集
    if not res or not res.get("matches"):
        w(f"[{idx}/{len(TODO)}] {nick} 采集历史...")
        try:
            res = collect_player(client, nick, 300, fetch_demo_url=False,
                                 min_kills_for_demo=0, expect_elo=expect)
            if res.get("error"):
                w(f"    ✗ {res['error']}")
                continue
            save(nick, res)
            w(f"    历史 {len(res.get('matches', []))} 场 ✓")
        except Exception as e:
            w(f"    ✗ 异常 {e}")
            continue
    else:
        w(f"[{idx}/{len(TODO)}] {nick} 已有历史 {len(res['matches'])} 场，检查多杀...")

    # 3) 补多杀（只补缺的）
    pid = res["player_id"]
    matches = res["matches"]
    todo_m = [m for m in matches if not isinstance(m.get("multikills"), dict)]
    if not todo_m:
        w(f"    ✓ 已完整，跳过")
        continue

    w(f"    需补 {len(todo_m)}/{len(matches)} 场")
    t3 = sum(m["multikills"].get("triples", 0) for m in matches
             if isinstance(m.get("multikills"), dict))
    t4 = sum(m["multikills"].get("quadras", 0) for m in matches
             if isinstance(m.get("multikills"), dict))
    t5 = sum(m["multikills"].get("aces", 0) for m in matches
             if isinstance(m.get("multikills"), dict))

    for i, m in enumerate(todo_m, 1):
        r = get_multikills(m.get("match_id"), pid)
        if not r:
            continue
        m["multikills"] = {"triples": r["triples"], "quadras": r["quadras"],
                           "aces": r["aces"]}
        m["mvps"] = r["mvps"]
        t3 += r["triples"]; t4 += r["quadras"]; t5 += r["aces"]
        if i % 25 == 0:
            w(f"    {i}/{len(todo_m)}  3K={t3} 4K={t4} 5K={t5}")
            save(nick, res)
    res["multikills_total"] = {"triples": t3, "quadras": t4, "aces": t5}
    save(nick, res)
    done += 1
    w(f"    ✓ {nick} 完成 | 3K={t3} 4K={t4} 5K={t5} | 累计 {(time.time()-t0)/60:.1f} 分")

w(f"\n本批完成 {done} 人，总耗时 {(time.time()-t0)/60:.1f} 分钟")
print("ALL DONE")
