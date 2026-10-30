# -*- coding: utf-8 -*-
"""
CS2 demo 解析模块 —— 提取 3K / 4K / 5K（多杀）回合。

依赖 demoparser2（Rust 实现的 CS2 demo 解析库，Python 绑定，速度快）。

思路：
  1. 从 demo 中解析出所有击杀事件（kills），含 tick、攻击者、受害者、回合编号。
  2. 按 (回合, 攻击者) 分组统计击杀数。
  3. 击杀数 >= 3 的即为多杀：3 = triple kill，4 = quadra kill，5 = ace(5K)。
  4. 同时可拿到该回合的武器、爆头等信息，用于剪辑选素材。

注意：
  - demo 文件若是 .dem.zst / .dem.gz 需先解压。
  - CS2 demo 用 zstd 压缩，demoparser2 部分版本可直接读 .zst，
    保险起见这里先解压再解析。
"""

from __future__ import annotations

import gzip
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import zstandard as zstd
except ImportError:  # 可选依赖
    zstd = None


@dataclass
class MultiKill:
    """一次多杀事件。"""

    round_number: int
    killer_steamid: str
    killer_name: str
    kill_count: int
    tick_start: int
    tick_end: int
    victims: list[str] = field(default_factory=list)
    headshots: int = 0
    weapons: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        """多杀标签。"""
        mapping = {5: "5K (ACE)", 4: "4K", 3: "3K", 2: "2K"}
        return mapping.get(self.kill_count, f"{self.kill_count}K")

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_number": self.round_number,
            "killer_steamid": self.killer_steamid,
            "killer_name": self.killer_name,
            "kill_count": self.kill_count,
            "label": self.label,
            "tick_start": self.tick_start,
            "tick_end": self.tick_end,
            "victims": self.victims,
            "headshots": self.headshots,
            "weapons": self.weapons,
        }


class DemoParserError(Exception):
    """demo 解析异常。"""


def decompress_demo(demo_path: str | Path, delete_original: bool = False) -> Path:
    """解压 .zst / .gz 的 demo 文件，返回解压后的 .dem 路径。

    若已是 .dem 则原样返回。
    """
    demo_path = Path(demo_path)
    suffix = demo_path.suffix.lower()

    if suffix == ".dem":
        return demo_path

    if suffix in (".zst", ".zstd"):
        if zstd is None:
            raise DemoParserError(
                "解压 .zst 需要 zstandard 库：pip install zstandard"
            )
        out = demo_path.with_suffix("")
        if out.suffix.lower() != ".dem":
            out = demo_path.parent / (demo_path.stem + ".dem")
        print(f"  解压 {demo_path.name} → {out.name} ...")
        dctx = zstd.ZstdDecompressor()
        with open(demo_path, "rb") as src, open(out, "wb") as dst:
            dctx.copy_stream(src, dst)
        if delete_original:
            demo_path.unlink(missing_ok=True)
        return out

    if suffix == ".gz":
        out = demo_path.parent / (demo_path.stem + ".dem")
        print(f"  解压 {demo_path.name} → {out.name} ...")
        with gzip.open(demo_path, "rb") as src, open(out, "wb") as dst:
            dst.write(src.read())
        if delete_original:
            demo_path.unlink(missing_ok=True)
        return out

    return demo_path


def parse_multikills(
    demo_path: str | Path,
    min_kills: int = 3,
) -> list[MultiKill]:
    """解析 demo，返回所有 >= min_kills 的多杀事件。

    Args:
        demo_path: demo 文件路径（.dem / .zst / .gz）。
        min_kills: 最小击杀数阈值，默认 3（即 3K 起）。

    Returns:
        多杀事件列表，按回合号排序。
    """
    try:
        from demoparser2 import DemoParser
    except ImportError as exc:
        raise DemoParserError(
            "缺少 demoparser2：pip install demoparser2"
        ) from exc

    demo_path = Path(demo_path)
    if not demo_path.exists():
        raise DemoParserError(f"demo 文件不存在：{demo_path}")

    # 解压（如需）
    dem_file = decompress_demo(demo_path)

    try:
        parser = DemoParser(str(dem_file))
        # 解析击杀事件；字段名以 demoparser2 为准
        kill_df = parser.parse_event(
            "player_death",
            player=["last_place_name"],
            other=["total_rounds_played"],
        )
    except Exception as exc:
        raise DemoParserError(f"解析 demo 失败：{dem_file.name} — {exc}") from exc

    if kill_df is None or len(kill_df) == 0:
        return []

    rows = kill_df.to_dict("records")

    # 按 (回合, 攻击者) 聚合
    buckets: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        attacker = row.get("attacker_steamid") or row.get("attacker_steam_id")
        if attacker in (None, "", "0", 0):
            continue  # 自杀/世界伤害
        round_no = row.get("total_rounds_played")
        if round_no is None:
            round_no = row.get("round", 0)
        buckets[(int(round_no), str(attacker))].append(row)

    results: list[MultiKill] = []
    for (round_no, attacker), events in buckets.items():
        if len(events) < min_kills:
            continue

        events.sort(key=lambda r: r.get("tick", 0))
        name = events[0].get("attacker_name") or events[0].get("attacker") or "unknown"
        victims = [
            (e.get("user_name") or e.get("victim_name") or e.get("user_steamid") or "?")
            for e in events
        ]
        weapons = [e.get("weapon") or "?" for e in events]
        headshots = sum(
            1 for e in events if str(e.get("headshot", "")).lower() in ("true", "1")
        )

        results.append(
            MultiKill(
                round_number=int(round_no),
                killer_steamid=str(attacker),
                killer_name=str(name),
                kill_count=len(events),
                tick_start=int(events[0].get("tick", 0)),
                tick_end=int(events[-1].get("tick", 0)),
                victims=[str(v) for v in victims],
                headshots=headshots,
                weapons=[str(w) for w in weapons],
            )
        )

    results.sort(key=lambda m: (m.round_number, -m.kill_count))
    return results


def filter_by_player(multikills: list[MultiKill], steamid: str) -> list[MultiKill]:
    """筛出指定选手（按 SteamID64）的多杀。"""
    sid = str(steamid)
    return [m for m in multikills if m.killer_steamid == sid]


def summarize(multikills: list[MultiKill]) -> dict[str, int]:
    """统计各类多杀数量。"""
    summary = {"3K": 0, "4K": 0, "5K": 0}
    for mk in multikills:
        if mk.kill_count >= 5:
            summary["5K"] += 1
        elif mk.kill_count == 4:
            summary["4K"] += 1
        elif mk.kill_count == 3:
            summary["3K"] += 1
    return summary
