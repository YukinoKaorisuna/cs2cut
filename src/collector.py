# -*- coding: utf-8 -*-
"""
主采集流程（内部接口方案，无需 API Key）。

流程：
  1. 昵称 → 玩家档案（校验 Elo，排除同名假号）
  2. 拉比赛历史（含每场击杀/死亡/KD/ADR）
  3. 对高击杀场次拉比赛详情 → 取 demo 直链
  4. 结果落盘到 data/

用法：
  python src/collector.py                      # 按 config.json 采集
  python src/collector.py --player donk666     # 只采集一个选手
  python src/collector.py --no-demo-url        # 跳过 demo 链接查询（快）
  python src/collector.py --min-kills 25       # 只对 >=25 杀的场次查 demo
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from faceit_client import FaceitClient, FaceitError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"找不到配置：{CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def collect_player(
    client: FaceitClient,
    nickname: str,
    match_limit: int,
    fetch_demo_url: bool = True,
    min_kills_for_demo: int = 0,
    expect_elo: int = 0,
) -> dict[str, Any]:
    """采集单个选手。"""
    print(f"\n{'=' * 60}")
    print(f"选手：{nickname}")
    print("=" * 60)

    # 1) 档案
    try:
        player = client.get_player(nickname)
    except FaceitError as exc:
        print(f"  [X] {exc}")
        return {"nickname": nickname, "error": str(exc), "matches": []}

    pid = player["player_id"]
    elo = player.get("elo", 0)
    print(f"  player_id : {pid}")
    print(f"  地区      : {player.get('country', '-').upper()}")
    print(f"  Elo       : {elo}  (等级 {player.get('skill_level', '-')})")
    if player.get("game_player_name"):
        print(f"  游戏内名  : {player['game_player_name']}")

    # 同名假号校验
    if expect_elo and elo < expect_elo:
        print(
            f"  [!] 警告：Elo {elo} 低于预期 {expect_elo}，"
            f"这可能不是目标选手（FACEIT 存在同名号）。"
        )

    # 2) 比赛历史
    print(f"\n  拉取比赛历史（最多 {match_limit} 场）...")
    try:
        raw_list = client.get_all_match_history(pid, "cs2", match_limit)
    except FaceitError as exc:
        print(f"  [X] {exc}")
        return {"nickname": nickname, "player_id": pid, "matches": []}

    print(f"  共 {len(raw_list)} 场")

    matches: list[dict[str, Any]] = []
    for i, raw in enumerate(raw_list, 1):
        rec = FaceitClient.parse_match_record(raw)
        matches.append(rec)
        flag = ""
        if rec["kills"] >= min_kills_for_demo and min_kills_for_demo:
            flag = "  ← 高击杀"
        print(
            f"  [{i:>2}/{len(raw_list)}] {rec['started_at_str']} "
            f"{rec['map']:<13} {rec['result']:<5} "
            f"{rec['kills']:>3}/{rec['deaths']:<3} "
            f"KD {rec['kd']:>4.2f} ADR {rec['adr']:>5.1f}{flag}"
        )

    # 3) demo 链接（只查目标场次）
    if fetch_demo_url:
        targets = [
            m for m in matches
            if m["kills"] >= min_kills_for_demo and m["match_id"]
        ]
        if targets:
            print(f"\n  查询 {len(targets)} 场高击杀比赛的 demo 链接 ...")
            for m in targets:
                try:
                    urls = client.get_demo_urls(m["match_id"])
                    m["demo_urls"] = urls
                    status = f"{len(urls)} 个" if urls else "无（可能已过期）"
                    print(f"    {m['kills']:>3}K {m['map']:<13} demo: {status}")
                except FaceitError as exc:
                    print(f"    {m['match_id'][:16]} 查询失败：{exc}")

    return {
        "nickname": player.get("nickname", nickname),
        "player_id": pid,
        "country": player.get("country", ""),
        "avatar": player.get("avatar", ""),
        "elo": elo,
        "skill_level": player.get("skill_level", 0),
        "game_player_name": player.get("game_player_name", ""),
        "matches": matches,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="FACEIT 选手数据采集（内部接口，无需 Key）")
    ap.add_argument("--player", help="只采集指定选手昵称")
    ap.add_argument("--no-demo-url", action="store_true", help="跳过 demo 链接查询")
    ap.add_argument("--min-kills", type=int, default=None, help="查 demo 的最低击杀数")
    args = ap.parse_args()

    cfg = load_config()
    players = [args.player] if args.player else cfg.get("players", [])
    # 兼容两种写法：字符串 或 {nickname, expect_elo}
    normalized: list[tuple[str, int]] = []
    for p in players:
        if isinstance(p, dict):
            normalized.append((p.get("nickname", ""), p.get("expect_elo", 0)))
        else:
            normalized.append((str(p), 0))

    match_limit = cfg.get("match_limit", 30)
    min_kills = (
        args.min_kills if args.min_kills is not None
        else cfg.get("min_kills", 25)
    )
    rl = cfg.get("rate_limit", {})

    client = FaceitClient(
        rps=rl.get("requests_per_second", 2),
        retry_times=rl.get("retry_times", 3),
        retry_delay=rl.get("retry_delay_seconds", 5),
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    for nickname, expect_elo in normalized:
        if not nickname:
            continue
        result = collect_player(
            client,
            nickname,
            match_limit,
            fetch_demo_url=not args.no_demo_url,
            min_kills_for_demo=min_kills,
            expect_elo=expect_elo,
        )
        out_file = DATA_DIR / f"{nickname}.json"
        with open(out_file, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        print(f"  → 已保存 {out_file}")

    print(f"\n完成，耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
