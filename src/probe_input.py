# -*- coding: utf-8 -*-
"""
探针：摸清 demoparser2 能读出哪些「输入/按键」相关的数据。

目的 —— 回答三个问题：
  Q1. 有没有直接的按键状态（bitmask / IN_FORWARD 之类）？
  Q2. 能不能拿到逐 tick 的视角角度（用来反推鼠标移动）？
  Q3. weapon_fire 事件有哪些字段？

用法：
  python src/probe_input.py "path/to/x.dem" [player_name]

输出同时写 logs/probe_input_<demo>.txt，方便读。
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "logs"
OUT_DIR.mkdir(exist_ok=True)

IFACE_KEYS = [
    # --- 按键掩码（探针已发现真实 prop 名）---
    "CCSPlayerPawn.CCSPlayer_MovementServices.m_nButtonDownMaskPrev",
    "CCSPlayerPawn.CCSPlayer_MovementServices.m_nToggleButtonDownMask",
    # --- 常见别名/猜测 ---
    "m_nButtons", "m_iButtons", "buttons", "button_mask", "nButtons",
    # --- 视角 ---
    "m_angEyeAngles", "m_angEyeAnglesX", "m_angEyeAnglesY",
    "m_vecViewOffset", "yaw", "pitch",
    # --- 状态 ---
    "m_iHealth", "health", "m_lifeState", "life_state", "is_alive",
    "m_iTeamNum", "team_num", "team_name", "team_number",
    "active_weapon", "weapon",
    "X", "Y", "Z", "velocity", "m_vecVelocity",
    "is_bomb_planted", "is_freeze_period", "game_phase",
    "balance", "round", "total_rounds_played",
    "m_flFlashDuration", "flash_duration",
    "is_defusing", "m_bIsDefusing",
    "m_bIsLookingAtWeapon", "m_bIsHoldingLookAtWeapon",
    "inventory",
]


def hr(title: str) -> str:
    return "\n" + "=" * 64 + f"\n{title}\n" + "=" * 64


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python src/probe_input.py <demo.dem> [player]")
        sys.exit(1)

    demo = Path(sys.argv[1])
    player = sys.argv[2] if len(sys.argv) > 2 else None

    log: list[str] = []
    def L(s: str = "") -> None:
        log.append(str(s))

    L(f"demo: {demo}")
    L(f"exists: {demo.exists()}  size: {demo.stat().st_size/1e6:.1f} MB"
      if demo.exists() else "MISSING")

    if not demo.exists():
        (OUT_DIR / "probe_input.txt").write_text("\n".join(log), encoding="utf-8")
        print("\n".join(log))
        return

    from demoparser2 import DemoParser

    # ---------- 1. 事件清单 ----------
    L(hr("1. list_game_events()"))
    try:
        p = DemoParser(str(demo))
        events = p.list_game_events()
        L(f"共 {len(events)} 个事件")
        L(json.dumps(events, ensure_ascii=False, indent=1))
        # 找和输入有关的
        kw = [e for e in events if any(k in str(e).lower() for k in
              ("button", "input", "key", "fire", "move", "player"))]
        L(f"\n含 button/input/key/fire/move/player 关键字的事件: {kw}")
    except Exception as exc:
        L(f"[X] list_game_events 失败: {exc}")
        traceback.print_exc(file=sys.stdout)

    # ---------- 2. 可解析的 prop 名 ----------
    L(hr("2. list_updated_fields() 里的按键/视角相关字段"))
    try:
        fields = p.list_updated_fields()
        L(f"prop 总数: {len(fields)}")
        hits = [f for f in fields if any(
            k.lower() in str(f).lower() for k in
            ("button", "eyeangle", "yaw", "pitch", "velocity", "health",
             "lifestate", "teamnum", "weapon", "flash", "defus"))]
        L(f"\n相关字段({len(hits)}):")
        L(json.dumps(hits, ensure_ascii=False, indent=1))
    except Exception as exc:
        L(f"[X] list_updated_fields 失败: {exc}")

    # ---------- 3. parse_ticks 试拉 ----------
    L(hr("3. parse_ticks() 试拉目标字段"))
    # 先拿 steamid 索引（parse_ticks 的 players 要传 int 索引）
    wanted_ids: list[int] = []
    try:
        hdr = p.parse_header()
        L(f"parse_header(): {hdr}")
    except Exception as exc:
        L(f"parse_header 失败: {exc}")

    if player:
        try:
            allp = p.parse_event("player_death")
            ids = sorted(set(
                int(x) for x in allp["attacker_steamid"].dropna().tolist()
            ))
            L(f"全部 steamid 索引: {ids}")
            name_map = {}
            for _, r in allp.iterrows():
                name_map[str(r.get("attacker_name"))] = int(r["attacker_steamid"])
            L(f"name->id: {name_map}")
            if player in name_map:
                wanted_ids = [name_map[player]]
                L(f"目标 {player} -> id {wanted_ids}")
            else:
                L(f"[!] 没找到选手 {player}")
        except Exception as exc:
            L(f"[X] 取 id 失败: {exc}")

    # demoparser2 的 players 参数：[]=全部，[id...]=指定
    tick_df = None
    try:
        tick_df = p.parse_ticks(IFACE_KEYS, players=wanted_ids)
        L(f"shape: {tick_df.shape}")
        L(f"columns: {list(tick_df.columns)}")
        L(f"\n列名逐个校验：")
        for k in IFACE_KEYS:
            hit = [c for c in tick_df.columns
                   if c == k or c.lower() == k.lower()
                   or c.lower().endswith("." + k.lower())]
            L(f"  {'[OK]' if hit else '[--]'} {k:<22} {hit if hit else ''}")
        L(f"\n前 3 行:\n{tick_df.head(3).to_string()}")
    except Exception as exc:
        L(f"[X] 整批 parse_ticks 失败: {exc}")
        L("\n逐个字段试探：")
        for k in IFACE_KEYS:
            try:
                df = p.parse_ticks([k], players=wanted_ids)
                L(f"  [OK] {k:<22} -> cols={list(df.columns)} rows={len(df)}")
                if tick_df is None and "tick" in df.columns:
                    tick_df = df
            except Exception as e2:
                L(f"  [--] {k:<22} -> {str(e2)[:70]}")

    # ---------- 4. 玩家清单 ----------
    L(hr("4. 选手清单"))
    try:
        players_df = p.parse_event("player_death")
        if players_df is not None and "attacker_name" in players_df.columns:
            names = sorted(set(players_df["attacker_name"].dropna().tolist()))
            L(f"出现在击杀里的选手: {names}")
    except Exception as exc:
        L(f"[X] {exc}")

    # ---------- 5. weapon_fire 字段 ----------
    L(hr("5. weapon_fire 事件字段"))
    try:
        fires = p.parse_event("weapon_fire", player=["X", "Y"])
        L(f"shape: {fires.shape}")
        L(f"columns: {list(fires.columns)}")
        L(f"\n前 5 行:\n{fires.head(5).to_string()}")
        if player and "user_name" in fires.columns:
            sub = fires[fires["user_name"] == player]
            L(f"\n指定选手 {player} 的开火条数: {len(sub)}")
    except Exception as exc:
        L(f"[X] weapon_fire 失败: {exc}")

    # ---------- 6. 保存 tick 样本 ----------
    if tick_df is not None:
        L(hr("6. tick 数据样本（若含按键/视角字段，取 200 行存 csv）"))
        try:
            sub = tick_df.head(200)
            csv = OUT_DIR / "probe_ticks_sample.csv"
            sub.to_csv(csv, index=False)
            L(f"已存 {csv}")
            for c in sub.columns:
                if "utton" in c or "ngle" in c or "yaw" in c.lower() or "pitch" in c.lower():
                    vals = sub[c].dropna().unique()[:10]
                    L(f"  {c} 取值样例: {vals}")
        except Exception as exc:
            L(f"[X] {exc}")

    text = "\n".join(log)
    (OUT_DIR / "probe_input.txt").write_text(text, encoding="utf-8")
    print("WROTE logs/probe_input.txt")


if __name__ == "__main__":
    main()
