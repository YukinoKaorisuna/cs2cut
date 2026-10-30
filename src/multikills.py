# -*- coding: utf-8 -*-
"""抽取指定选手全部比赛的多杀数据。"""
import json
import sys
import time
from pathlib import Path

import requests

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
    """限速，避免触发 429。"""
    global _last_ts
    wait = min_interval - (time.time() - _last_ts)
    if wait > 0:
        time.sleep(wait)
    _last_ts = time.time()


def get_player_multikills(match_id, player_id, retries: int = 3):
    """从比赛统计里取指定玩家的 3K/4K/5K。"""
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

    # 有些比赛无统计数据，返回的是 dict（错误响应）而非 list
    if not arr or not isinstance(arr, list) or not arr:
        return None
    for t in arr[0]["teams"]:
        for p in t["players"]:
            if p.get("playerId") == player_id:
                return {
                    "map": arr[0].get("i1", ""),
                    "score": arr[0].get("i18", ""),
                    "rounds": arr[0].get("i12", ""),
                    "kills": int(p.get("i6", 0) or 0),
                    "deaths": int(p.get("i8", 0) or 0),
                    "assists": int(p.get("i7", 0) or 0),
                    "triples": int(p.get("i14", 0) or 0),
                    "quadras": int(p.get("i15", 0) or 0),
                    "aces": int(p.get("i16", 0) or 0),
                    "mvps": int(p.get("i9", 0) or 0),
                }
    return None


def main():
    nick = sys.argv[1] if len(sys.argv) > 1 else "donk666"
    data = json.load(open(Path(__file__).resolve().parent.parent / "data" / f"{nick}.json",
                      encoding="utf-8"))
    pid = data["player_id"]
    print(f"\n{nick}  (Elo {data.get('elo')})  共 {len(data['matches'])} 场\n")
    print(f"{'时间':<12}{'地图':<14}{'比分':<10}{'杀':>4}{'死':>4}"
          f"{'3K':>4}{'4K':>4}{'5K':>4}{'MVP':>5}")
    print("-" * 72)

    results = []
    for m in data["matches"]:
        r = get_player_multikills(m["match_id"], pid)
        if not r:
            continue
        r["date"] = m.get("started_at_str", "")
        results.append(r)

    # 按 (5K, 4K, 3K, 击杀) 排序
    results.sort(key=lambda r: (r["aces"], r["quadras"], r["triples"], r["kills"]),
                 reverse=True)

    for r in results:
        mark = ""
        if r["aces"]:
            mark = "  ★ACE"
        elif r["quadras"]:
            mark = "  ★4K"
        print(
            f"{r['date']:<12}{r['map']:<14}{r['score']:<10}"
            f"{r['kills']:>4}{r['deaths']:>4}"
            f"{r['triples']:>4}{r['quadras']:>4}{r['aces']:>4}{r['mvps']:>5}{mark}"
        )

    total3 = sum(r["triples"] for r in results)
    total4 = sum(r["quadras"] for r in results)
    total5 = sum(r["aces"] for r in results)
    print("-" * 72)
    print(f"合计: 3K {total3} 次 | 4K {total4} 次 | 5K(ACE) {total5} 次")


if __name__ == "__main__":
    main()
