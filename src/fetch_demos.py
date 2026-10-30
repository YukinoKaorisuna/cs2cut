# -*- coding: utf-8 -*-
"""
demo 批量下载入口。

从 data/*.json 读取已采集的 demo 直链，按条件批量下载到 demos/。

用法：
  python src/fetch_demos.py                  # 下载所有高击杀场次的 demo
  python src/fetch_demos.py --player donk666 # 只下某选手
  python src/fetch_demos.py --min-kills 30   # 只下 >=30 杀的
  python src/fetch_demos.py --limit 5        # 最多下 5 个
  python src/fetch_demos.py --probe-only     # 只探测链接可用性，不下载
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from demo_downloader import DemoDownloader  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def main() -> None:
    ap = argparse.ArgumentParser(description="批量下载 FACEIT demo")
    ap.add_argument("--player", help="只处理指定选手")
    ap.add_argument("--min-kills", type=int, default=25, help="最低击杀数")
    ap.add_argument("--limit", type=int, default=0, help="最多下载数（0=不限）")
    ap.add_argument("--probe-only", action="store_true", help="只探测不下载")
    args = ap.parse_args()

    cfg = load_config()
    dd_cfg = cfg.get("demo_download", {})
    out_dir = ROOT / dd_cfg.get("output_dir", "demos")

    files = sorted(DATA_DIR.glob("*.json"))
    if args.player:
        files = [f for f in files if f.stem == args.player]
    if not files:
        print("[!] 没有匹配的数据文件")
        return

    # 收集任务
    tasks: list[tuple[str, str, int, str]] = []
    for path in files:
        with open(path, "r", encoding="utf-8") as fh:
            ds = json.load(fh)
        nick = ds.get("nickname", path.stem)
        for m in ds.get("matches", []):
            if m.get("kills", 0) < args.min_kills:
                continue
            for url in m.get("demo_urls", []):
                tasks.append((nick, m.get("match_id", ""), m["kills"], url))

    if not tasks:
        print(f"[!] 没有 >= {args.min_kills} 杀且有 demo 链接的场次")
        return

    # 按击杀降序
    tasks.sort(key=lambda t: t[2], reverse=True)
    if args.limit:
        tasks = tasks[: args.limit]

    total_mb = 0
    print(f"共 {len(tasks)} 个 demo 待处理\n")

    dd = DemoDownloader(output_dir=out_dir)

    if args.probe_only:
        print("模式：仅探测链接可用性\n")
        ok_n = 0
        for nick, mid, k, url in tasks:
            ok, msg = dd.probe(url)
            mark = "OK " if ok else "FAIL"
            print(f"  [{mark}] {nick:<14} {k:>3}K  {mid[:20]}  {msg}")
            ok_n += 1 if ok else 0
        print(f"\n可下载 {ok_n}/{len(tasks)}")
        if ok_n == 0:
            print("\n全部失败 → 大概率是代理问题：")
            print("  请把 *.backblaze.faceit-cdn.net 加入代理放行规则，")
            print("  或在 Clash 中为该域名指定可用节点。")
        return

    print("开始下载（支持断点续传）\n")
    done = 0
    for i, (nick, mid, k, url) in enumerate(tasks, 1):
        print(f"[{i}/{len(tasks)}] {nick} {k}K {mid[:20]}")
        result = dd.download(mid, url)
        if result:
            done += 1
            total_mb += result.stat().st_size / 1024 / 1024

    print(f"\n完成 {done}/{len(tasks)}，共 {total_mb:.0f} MB")
    if done == 0:
        print("\n全部失败 → 检查代理设置（见 --probe-only 提示）")
    elif done < len(tasks):
        print("部分失败可重跑，已完成的会跳过，未完成的支持续传。")


if __name__ == "__main__":
    main()
