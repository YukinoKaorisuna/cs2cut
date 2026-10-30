# -*- coding: utf-8 -*-
"""
把 FACEIT 现成的多杀字段（i14=3K / i15=4K / i16=5K）回填进 data/*.json。

这样一来，看板的多杀列无需下载 demo 即可显示真实数据。

用法：
  python src/backfill_multikills.py                # 处理 data/ 下所有选手
  python src/backfill_multikills.py donk666        # 只处理一个
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

H = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.faceit.com/",
}

_last_ts = 0.0


def _throttle(min_interval: float = 1.2) -> None:
    global _last_ts
    wait = min_interval - (time.time() - _last_ts)
    if wait > 0:
        time.sleep(wait)
    _last_ts = time.time()


def get_multikills(match_id: str, player_id: str, retries: int = 3):
    """返回 dict(triples, quadras, aces, kills, deaths, assists, mvps) 或 None。"""
    arr = None
    for attempt in range(retries):
        _throttle()
        try:
            r = requests.get(
                f"https://www.faceit.com/api/stats/v1/stats/matches/{match_id}",
                headers=H, timeout=15,
            )
            if r.status_code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            arr = r.json()
            break
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(2)

    if not arr or not isinstance(arr, list) or not arr:
        return None
    for team in arr[0].get("teams", []):
        for p in team.get("players", []):
            if p.get("playerId") == player_id:
                return {
                    "kills": int(p.get("i6", 0) or 0),
                    "deaths": int(p.get("i8", 0) or 0),
                    "assists": int(p.get("i7", 0) or 0),
                    "triples": int(p.get("i14", 0) or 0),
                    "quadras": int(p.get("i15", 0) or 0),
                    "aces": int(p.get("i16", 0) or 0),
                    "mvps": int(p.get("i9", 0) or 0),
                }
    return None


def process(nick: str) -> None:
    path = DATA_DIR / f"{nick}.json"
    if not path.exists():
        print(f"[!] 找不到 {path}")
        return

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    pid = data.get("player_id")
    matches = data.get("matches", [])
    print(f"\n{nick}  (Elo {data.get('elo')})  共 {len(matches)} 场")

    t3 = t4 = t5 = 0
    ok = 0
    for i, m in enumerate(matches, 1):
        mid = m.get("match_id")
        if not mid:
            continue
        r = get_multikills(mid, pid)
        if not r:
            print(f"  [{i:>2}/{len(matches)}] {m.get('started_at_str','')} "
                  f"{m.get('map',''):<13} 无统计数据")
            continue
        m["multikills"] = {
            "triples": r["triples"],
            "quadras": r["quadras"],
            "aces": r["aces"],
        }
        m["mvps"] = r["mvps"]
        t3 += r["triples"]; t4 += r["quadras"]; t5 += r["aces"]
        ok += 1
        mark = ""
        if r["aces"]:
            mark = "  ★ACE"
        elif r["quadras"]:
            mark = "  ★4K"
        print(f"  [{i:>2}/{len(matches)}] {m.get('started_at_str',''):<17}"
              f"{m.get('map',''):<13}K{r['kills']:>3}  "
              f"3K×{r['triples']}  4K×{r['quadras']}  5K×{r['aces']}{mark}")

    data["multikills_total"] = {"triples": t3, "quadras": t4, "aces": t5}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)

    print(f"  → {ok}/{len(matches)} 场回填完成 | 合计 3K {t3} | 4K {t4} | 5K {t5}")


def main() -> None:
    if len(sys.argv) > 1:
        nicks = sys.argv[1:]
    else:
        nicks = [p.stem for p in sorted(DATA_DIR.glob("*.json"))]
    for nick in nicks:
        process(nick)


if __name__ == "__main__":
    main()
