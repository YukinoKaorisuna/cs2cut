# -*- coding: utf-8 -*-
"""导出所有选手的多杀场次为一张 CSV（方便进 Excel / 剪辑软件）。

用法：
  python src/export_multikills.py                 # 全部选手合并
  python src/export_multikills.py donk666         # 仅单个
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

REAL = {
    "donk666": "donk", "dem0nnnnnnnn": "dem0n", "nipl": "npl",
    "z1Nny": "z1Nny", "CEMEN_BAKIN": "kyousuke", "m0NESY": "m0NESY",
    "w0nderful": "w0nderful", "b1t": "b1t", "niko": "NiKo",
    "sh1ro": "sh1ro", "ZywOo": "ZywOo", "broky": "broky",
    "karrigan": "karrigan", "Twistzz": "Twistzz", "ropz": "ropz",
}

COLUMNS = ["选手", "FACEIT昵称", "日期(UTC)", "地图", "结果", "比分",
           "击杀", "死亡", "ADR", "3K", "4K", "5K", "多杀合计", "标记", "Demo直链"]


def collect_one(nick: str) -> list[dict]:
    path = DATA / f"{nick}.json"
    if not path.exists():
        return []
    d = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for m in d.get("matches", []):
        mk = m.get("multikills")
        if not isinstance(mk, dict):
            continue
        t3 = int(mk.get("triples", 0) or 0)
        t4 = int(mk.get("quadras", 0) or 0)
        t5 = int(mk.get("aces", 0) or 0)
        if t3 + t4 + t5 == 0:
            continue
        rows.append({
            "选手": REAL.get(nick, nick),
            "FACEIT昵称": nick,
            "日期(UTC)": m.get("started_at_str", ""),
            "地图": m.get("map", ""),
            "结果": {"win": "胜", "loss": "负"}.get(m.get("result", ""), "-"),
            "比分": m.get("score", ""),
            "击杀": m.get("kills", 0),
            "死亡": m.get("deaths", 0),
            "ADR": f"{m.get('adr', 0):.0f}",
            "3K": t3, "4K": t4, "5K": t5,
            "多杀合计": t3 + t4 + t5,
            "标记": "★ACE" if t5 else ("★4K" if t4 else ""),
            "Demo直链": (m.get("demo_urls") or [""])[0],
        })
    return rows


def main():
    if len(sys.argv) > 1:
        nicks = sys.argv[1:]
    else:
        nicks = sorted(p.stem for p in DATA.glob("*.json"))

    all_rows = []
    for n in nicks:
        all_rows.extend(collect_one(n))

    # 排序：选手 → 5K → 4K → 3K → 击杀
    all_rows.sort(key=lambda r: (r["选手"], r["5K"], r["4K"], r["3K"], r["击杀"]),
                  reverse=True)

    if len(sys.argv) > 1:
        out_csv = OUT / f"{sys.argv[1]}_多杀场次.csv"
    else:
        out_csv = OUT / "全部选手_多杀场次.csv"

    with open(out_csv, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(all_rows)

    t3 = sum(r["3K"] for r in all_rows)
    t4 = sum(r["4K"] for r in all_rows)
    t5 = sum(r["5K"] for r in all_rows)
    print(f"OK {out_csv}")
    print(f"共 {len(all_rows)} 场有多杀 | 3K {t3} | 4K {t4} | 5K {t5}")


if __name__ == "__main__":
    main()
