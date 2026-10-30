# -*- coding: utf-8 -*-
"""
CS2 demo 输入提取器 —— 还原选手的键盘 / 鼠标操作，供可视化与视频导出使用。

═══════════════════════════════════════════════════════════════════════
【字段选型：全部实测验证过，见 logs/verify_official.txt】
═══════════════════════════════════════════════════════════════════════

■ 按键 —— 用 demoparser2 的【官方布尔字段】，不要手写位掩码
    官方 README 列出的字段，parse_ticks 直接返回 dtype=bool：
        FORWARD / BACK / LEFT / RIGHT      ← W / S / A / D
        FIRE / RIGHTCLICK                  ← 鼠标左键 / 右键
        RELOAD / USE / INSPECT / ZOOM      ← R / E / F / 右键开镜
        SCOREBOARD                          ← Tab（噪音，默认排除）
    实测数值与手写位掩码完全一致（FORWARD=45442、LEFT=23463…），
    所以位掩码那套是多余的。

    ⚠️ 陷阱：WALK/JUMP/DUCK/SPRINT 这几个名字 **返回的是假数据**
       —— dtype=uint64 且全为 True（137177/137177），是 parser 对
       未知字段的静默兜底。大小写都一样假。绝不能用。

■ 走 / 蹲 / 跳 —— 必须用【状态属性】，不能靠按键
    is_walking    : bool   —— 按 Shift 静步
    duck_amount   : 0~1    —— 蹲的深度，>0.5 视为蹲下
    is_airborne   : bool   —— 是否腾空（跳）
    velocity_Z    : float  —— Z 轴速度，正=上升 负=下落

■ 左键开火 —— 用 FIRE 字段
    实测 FIRE 对 weapon_fire 事件命中率 100%（255/255）。
    （早期我用手算上升沿只有 31%，那是因为做了差分；
      官方字段是每 tick 状态，直接对上。）

■ 武器名 —— 只能用 weapon_fire 事件的 weapon 字段
    ⚠️ active_weapon 返回的是【带皮肤的实体句柄】不是武器 ID：
       同一个 AK 会出现十几个不同数值（2425653、10142214…），
       因为皮肤/磨损/StatTrak 不同就是不同实例。不能用来判断武器。

═══════════════════════════════════════════════════════════════════════

用法：
    python src/input_extractor.py <demo.dem> --list
    python src/input_extractor.py <demo.dem> <player> [-o out.json]
    python src/input_extractor.py <demo.dem> <player> --fps 60
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "output"

DEFAULT_FPS = 60


def list_players(demo: str) -> dict[str, int]:
    from demoparser2 import DemoParser
    p = DemoParser(demo)
    d = p.parse_event("player_death")
    m: dict[str, int] = {}
    for _, r in d.iterrows():
        n = r.get("attacker_name")
        sid = r.get("attacker_steamid")
        # 自杀/世界伤害没有 attacker（NaN），跳过。
        # 注意 bool(float('nan')) 为 True，必须用 sid == sid 判断。
        if not n or str(n) == "nan":
            continue
        if sid is None or sid != sid:
            continue
        try:
            m[str(n)] = int(sid)
        except (ValueError, TypeError):
            continue
    return m


# CS2 竞技的冻结时间是固定值（竞技 20s / 部分赛事 15s）。用
# 「round_start → round_freeze_end」的 tick 差反推 demo 的真实 tickrate ——
# 这是唯一能独立于 header 拿到 tickrate 的可靠办法：CS2 的 `PBDEMS2` 头里
# **没有** playback_ticks / playback_time，按 CSGO 的固定偏移去读全是垃圾值。
TICKRATE_CANDIDATES = (64, 128)

# 最近一次 tickrate 判定的证据（写进 meta，方便事后核对，不用重新解析 demo）
LAST_TICKRATE_INFO: dict = {}


def detect_tickrate(p, default: int = 64) -> int:
    """用冻结时间常量反推 tickrate；判不出来就回退 default。

    ⚠️ 为什么必须判：`killviz_compose.render_overlay` 直接信任
    `meta["tickrate"]` 来把按键数据对齐到画面。若 demo 是 128 tick 而这里
    仍写 64，键盘层会**静默错位 2 倍** —— 不报错，只是键盘跟画面对不上。
    """
    global LAST_TICKRATE_INFO
    LAST_TICKRATE_INFO = {"source": "default", "freeze_ticks": None,
                          "freeze_sec": None, "samples": 0}
    try:
        rs = sorted(int(t) for t in p.parse_event("round_start")["tick"].tolist())
        fe = sorted(int(t) for t in p.parse_event("round_freeze_end")["tick"].tolist())
    except Exception:
        return default
    gaps = []
    for t0 in rs:
        nxt = [t for t in fe if t > t0]
        if nxt:
            gaps.append(nxt[0] - t0)
    if not gaps:
        return default
    med = float(np.median(gaps))
    for tr in TICKRATE_CANDIDATES:      # 冻结时间应落在 14~21 秒
        if 14.0 <= med / tr <= 21.0:
            LAST_TICKRATE_INFO = {"source": "freezetime", "freeze_ticks": med,
                                  "freeze_sec": round(med / tr, 3),
                                  "samples": len(gaps)}
            return tr
    return default


def extract(demo: str, player: str, fps: int = DEFAULT_FPS) -> dict:
    """提取选手输入，重采样到固定 fps。

    Returns:
        可 json.dump 的 dict。
    """
    from demoparser2 import DemoParser

    p = DemoParser(demo)
    players = list_players(demo)
    if player not in players:
        for k in players:
            if k.lower() == player.lower():
                player = k
                break
        else:
            raise SystemExit(f"选手 {player!r} 不在 demo。可选：{sorted(players)}")
    pid = players[player]
    header = p.parse_header()

    # ───── 逐 tick 拉数据（官方字段 + 状态属性）─────
    BTN = ["FORWARD", "BACK", "LEFT", "RIGHT", "FIRE", "RIGHTCLICK",
           "RELOAD", "USE", "INSPECT", "ZOOM", "SCOREBOARD"]
    STATE = ["is_walking", "duck_amount", "is_airborne", "velocity_Z",
             "health", "is_alive", "total_rounds_played",
             "yaw", "pitch", "X", "Y"]

    df = p.parse_ticks(BTN + STATE, players=[pid])
    n = len(df)
    tick = df["tick"].to_numpy().astype(np.int64)
    tick0 = int(tick[0])

    def b(name: str) -> np.ndarray:
        if name not in df.columns:
            return np.zeros(n, dtype=bool)
        return df[name].fillna(False).to_numpy().astype(bool)

    def f(name: str, dflt: float = 0.0) -> np.ndarray:
        if name not in df.columns:
            return np.full(n, dflt, dtype=np.float32)
        return df[name].to_numpy(dtype=np.float32)

    def i16(name: str, dflt: int = 0) -> np.ndarray:
        """数值列 -> **重采样到目标 fps** 的 int16，NaN 填 dflt。

        ⚠️ 必须过 `idx` 重采样。早期漏了这一步，返回的是**原始 tick 长度**
        （例 246724）的数组，而其他按键字段是重采样后的帧数（例 231304）——
        两者长度不一致，消费方按「帧号」去索引 ROUND/HP 会读到错误的 tick 值
        （回合号偏慢、血量对不上），**而且完全不报错**。2026-10-01 实测踩到。
        """
        if name not in df.columns:
            return np.full(n_out, dflt, dtype=np.int16)
        arr = df[name].to_numpy(dtype=np.float32)
        arr = np.nan_to_num(arr, nan=float(dflt), posinf=float(dflt),
                            neginf=float(dflt))
        return np.round(arr).astype(np.int16)[idx]

    # ───── 重采样到固定 fps ─────
    # demo tick -> 时间(秒) -> 目标帧号
    tickrate = detect_tickrate(p)
    src_t = (tick - tick0) / tickrate            # 秒
    dur = float(src_t[-1]) if n else 0.0
    n_out = max(1, int(round(dur * fps)) + 1)
    dst_t = np.arange(n_out, dtype=np.float64) / fps
    # 每个目标帧对应源数组下标
    idx = np.clip(np.searchsorted(src_t, dst_t, side="right") - 1, 0, n - 1)

    def rb(name: str) -> np.ndarray:
        return b(name)[idx]

    def rf(name: str) -> np.ndarray:
        return f(name)[idx]

    yaw_raw = rf("yaw")
    # yaw 展开（去 0-360 环绕），方便画轨迹
    yaw_uw = np.empty_like(yaw_raw)
    acc = 0.0
    prev = float(yaw_raw[0])
    for i in range(len(yaw_raw)):
        cur = float(yaw_raw[i])
        d = cur - prev
        if d > 180:
            d -= 360
        elif d < -180:
            d += 360
        acc += d
        yaw_uw[i] = acc
        prev = cur

    duck = rf("duck_amount")
    frames = {
        "W": rb("FORWARD").astype(np.uint8),
        "S": rb("BACK").astype(np.uint8),
        "A": rb("LEFT").astype(np.uint8),
        "D": rb("RIGHT").astype(np.uint8),
        "WALK": rb("is_walking").astype(np.uint8),          # Shift 静步
        "DUCK": (duck > 0.5).astype(np.uint8),               # 蹲
        "DUCK_AMT": np.round(duck, 2),                       # 蹲的深度
        "JUMP": rb("is_airborne").astype(np.uint8),          # 跳
        "FIRE": rb("FIRE").astype(np.uint8),                 # 鼠标左键
        "RCLICK": rb("RIGHTCLICK").astype(np.uint8),         # 鼠标右键
        "RELOAD": rb("RELOAD").astype(np.uint8),
        "USE": rb("USE").astype(np.uint8),
        "INSPECT": rb("INSPECT").astype(np.uint8),
        "SCOREBOARD": rb("SCOREBOARD").astype(np.uint8),     # Tab（噪音，标灰）
        "YAW": np.round(yaw_uw, 2),
        "PITCH": np.round(rf("pitch"), 2),
        "VELZ": np.round(rf("velocity_Z"), 1),
        "ALIVE": rb("is_alive").astype(np.uint8),
        "HP": i16("health"),                       # NaN -> 0（未出生/观战）
        "ROUND": i16("total_rounds_played"),
    }

    # ───── 长度自检 ─────
    # 所有字段必须与 n_out 等长。不等长时消费方按帧号索引会**静默读到错值**
    # （本项目最忌讳的失败模式），所以这里显式报警而不是放过去。
    _bad = {k: len(v) for k, v in frames.items() if len(v) != n_out}
    if _bad:
        print(f"[!] 字段长度与帧数不一致（n_out={n_out}）：{_bad}"
              f" —— 消费方按帧号索引会错位，请检查重采样逻辑。")

    # ───── 开火事件（带武器名）─────
    PRIMARY = {
        "weapon_ak47", "weapon_m4a1", "weapon_m4a1_silencer", "weapon_galilar",
        "weapon_famas", "weapon_aug", "weapon_sg556", "weapon_awp",
        "weapon_ssg08", "weapon_scar20", "weapon_g3sg1", "weapon_glock",
        "weapon_usp_silencer", "weapon_hkp2000", "weapon_p250",
        "weapon_fiveseven", "weapon_tec9", "weapon_cz75a", "weapon_deagle",
        "weapon_revolver", "weapon_elite", "weapon_p90", "weapon_mp9",
        "weapon_mac10", "weapon_mp7", "weapon_mp5sd", "weapon_ump45",
        "weapon_bizon", "weapon_nova", "weapon_xm1014", "weapon_mag7",
        "weapon_sawedoff", "weapon_m249", "weapon_negev",
    }
    fires = p.parse_event("weapon_fire")
    fires = fires[fires["user_name"] == player]
    shots = []
    for _, r in fires.iterrows():
        w = str(r["weapon"])
        ft = int(r["tick"]) - tick0
        shots.append({
            "f": max(0, int(round(ft / tickrate * fps))),  # 帧号
            "tick": int(r["tick"]),
            "weapon": w,
            "primary": w in PRIMARY,
        })
    shots.sort(key=lambda s: s["f"])

    # ───── 回合边界（转成帧号）─────
    rounds = []
    rp = df["total_rounds_played"].to_numpy()
    rp = np.nan_to_num(rp, nan=-1.0)          # NaN -> -1，避免 int(nan) 崩溃
    for rno in sorted(set(rp.tolist())):
        if rno < 0:
            continue                          # 跳过无效回合
        sel = rp == rno
        if not sel.any():
            continue
        a = (tick[sel][0] - tick0) / tickrate
        e = (tick[sel][-1] - tick0) / tickrate
        rounds.append({
            "round": int(rno),
            "f0": int(round(a * fps)),
            "f1": int(round(e * fps)),
        })

    # ───── 统计 ─────
    stats = {}
    for k in ("W", "S", "A", "D", "WALK", "DUCK", "JUMP", "FIRE",
              "RCLICK", "RELOAD", "USE", "INSPECT", "SCOREBOARD"):
        arr = frames[k]
        stats[k] = int(arr.sum())
    stats["FRAMES"] = n_out
    stats["SHOTS"] = len(shots)
    stats["SHOTS_PRIMARY"] = sum(1 for s in shots if s["primary"])

    return {
        "meta": {
            "demo": Path(demo).name,
            "map": header.get("map_name", "?"),
            "server": header.get("server_name", "?"),
            "player": player,
            "steamid": str(pid),
            "tickrate": tickrate,
            "tickrate_info": dict(LAST_TICKRATE_INFO),
            "tick0": tick0,
            "fps": fps,
            "duration_s": round(n_out / fps, 2),
            "frames": n_out,
            "rounds": len(rounds),
        },
        "frames": {k: (v.tolist() if isinstance(v, np.ndarray) else v)
                   for k, v in frames.items()},
        "shots": shots,
        "rounds": rounds,
        "stats": stats,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="提取 CS2 demo 的键鼠输入")
    ap.add_argument("demo")
    ap.add_argument("player", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("-o", "--out")
    ap.add_argument("--fps", type=int, default=DEFAULT_FPS)
    args = ap.parse_args()

    if args.list or not args.player:
        ps = list_players(args.demo)
        print("demo:", Path(args.demo).name)
        for nm, sid in sorted(ps.items()):
            print(f"  {nm:<16} {sid}")
        if not args.player:
            return

    data = extract(args.demo, args.player, args.fps)
    out = Path(args.out) if args.out else (
        OUT_DIR / "inputs" / f"{Path(args.demo).stem}__{args.player}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")

    m, s = data["meta"], data["stats"]
    print(f"[OK] {m['player']} @ {m['map']}  {m['duration_s']}s / "
          f"{m['rounds']}回合 / {m['frames']}帧 @{m['fps']}fps")
    print(f"     W{s['W']} A{s['A']} S{s['S']} D{s['D']} | "
          f"静步{s['WALK']} 蹲{s['DUCK']} 跳{s['JUMP']}")
    print(f"     左键{s['FIRE']} 右键{s['RCLICK']} 换弹{s['RELOAD']} "
          f"使用{s['USE']} 检视{s['INSPECT']} | 开火事件{s['SHOTS']}"
          f"(枪械{s['SHOTS_PRIMARY']})")
    print(f"[OUT] {out}")


if __name__ == "__main__":
    main()
