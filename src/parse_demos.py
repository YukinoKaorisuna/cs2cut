# -*- coding: utf-8 -*-
"""
demo 解析入口 —— 批量解析 demos/ 目录下的 demo，提取多杀并回填到 data/。

流程：
  1. 扫描 data/*.json，找出目标选手的场次及 steamid 映射
  2. 扫描 demos/ 目录，按 match_id 匹配 demo 文件
  3. 解析每个 demo，提取 3K/4K/5K
  4. 把结果写回 data/*.json 的 matches[].multikills

用法：
  python src/parse_demos.py                    # 解析所有已下载 demo
  python src/parse_demos.py --min-kills 4      # 只要 4K 以上
  python src/parse_demos.py --demo path.dem    # 解析单个 demo
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from demo_parser import (  # noqa: E402
    DemoParserError,
    parse_multikills,
    summarize,
)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEMOS_DIR = ROOT / "demos"


def find_demo_for_match(match_id: str) -> Path | None:
    """在 demos/ 里按 match_id 前缀找 demo 文件。"""
    if not DEMOS_DIR.exists():
        return None
    for path in DEMOS_DIR.iterdir():
        if path.is_file() and match_id in path.name and path.suffix != ".part":
            return path
    return None


def steamid_for_player(nickname: str) -> str | None:
    """从 data JSON 里拿选手的 steam_id（FACEIT 的 game_player_id）。"""
    path = DATA_DIR / f"{nickname}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("steam_id") or data.get("game_player_id")


def parse_all(min_kills: int = 3) -> None:
    """解析所有选手的所有已下载 demo。"""
    if not DATA_DIR.exists():
        print("[!] data/ 目录不存在，请先运行 collector.py")
        return

    files = sorted(DATA_DIR.glob("*.json"))
    if not files:
        print("[!] data/ 下没有选手数据")
        return

    for path in files:
        with open(path, "r", encoding="utf-8") as fh:
            ds = json.load(fh)

        nickname = ds.get("nickname", path.stem)
        steam_id = ds.get("steam_id") or ""
        matches = ds.get("matches", [])
        if not matches:
            continue

        print(f"\n{'=' * 55}")
        print(f"选手：{nickname}（steam_id: {steam_id or '未知'}）")
        print("=" * 55)

        done = 0
        for m in matches:
            mid = m.get("match_id", "")
            demo = find_demo_for_match(mid)
            if not demo:
                continue
            print(f"\n  解析 {demo.name} ...")
            try:
                mks = parse_multikills(demo, min_kills=min_kills)
            except DemoParserError as exc:
                print(f"    [X] {exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"    [X] 未预期错误：{exc}")
                traceback.print_exc()
                continue

            # 若知道 steam_id，只保留该选手的多杀
            if steam_id:
                mks = [mk for mk in mks if mk.killer_steamid == str(steam_id)]

            m["multikills"] = [mk.to_dict() for mk in mks]
            m["multikill_summary"] = summarize(mks)
            done += 1

            if mks:
                for mk in mks:
                    print(
                        f"    回合 {mk.round_number:>2}  {mk.label:<9} "
                        f"武器 {','.join(mk.weapons[:3])}"
                    )
            else:
                print(f"    无 {min_kills}K 以上多杀")

        if done:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(ds, fh, ensure_ascii=False, indent=2)
            print(f"\n  → 已回填 {done} 场，保存 {path.name}")
        else:
            print("\n  （无用例 demo）")

    print("\n完成。运行 dashboard.py 刷新看板。")


def parse_single(demo_path: str, min_kills: int = 3) -> None:
    """解析单个 demo 并打印结果。"""
    path = Path(demo_path)
    if not path.exists():
        print(f"[X] 文件不存在：{path}")
        return

    print(f"解析：{path.name}")
    try:
        mks = parse_multikills(path, min_kills=min_kills)
    except DemoParserError as exc:
        print(f"[X] {exc}")
        return

    if not mks:
        print(f"没有 {min_kills}K 以上多杀")
        return

    print(f"\n找到 {len(mks)} 次多杀：")
    for mk in mks:
        print(
            f"  回合 {mk.round_number:>2}  {mk.label:<9} "
            f"{mk.killer_name}  击杀 {mk.kill_count}  "
            f"爆头 {mk.headshots}  武器 {','.join(mk.weapons)}"
        )

    print(f"\n汇总：{summarize(mks)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="解析 CS2 demo，提取 3K/4K/5K")
    ap.add_argument("--min-kills", type=int, default=3, help="最小击杀阈值（默认 3）")
    ap.add_argument("--demo", help="解析单个 demo 文件")
    args = ap.parse_args()

    if args.demo:
        parse_single(args.demo, args.min_kills)
    else:
        parse_all(args.min_kills)


if __name__ == "__main__":
    main()
