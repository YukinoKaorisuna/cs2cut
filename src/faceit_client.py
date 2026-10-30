# -*- coding: utf-8 -*-
"""
FACEIT 数据抓取（内部接口方案）—— 无需 API Key、无需登录。

## 重要背景

人脸 FACEIT 的网页前端使用一组内部接口，这些接口**公开可访问**，
不需要 API Key、不需要登录 cookie。

这绕开了官方 Data API（open.faceit.com）的准入限制
（官方要求第三方注册 + 2FA + 身份验证，审核约 30 天）。

## 实测验证过的接口（2026-10-01 验证，全部 200）

1. 昵称 → 档案
   GET https://www.faceit.com/api/users/v1/nicknames/{nickname}
   返回 payload.id / nickname / country / avatar / games

2. 比赛历史（含每场击杀/死亡/KD/ADR/地图/结果）
   GET https://www.faceit.com/api/stats/v1/stats/time/users/{player_id}/games/cs2?size=N&page=P
   返回数组，字段是编码形式（i6=击杀、i8=死亡、c2=KD...）

3. 比赛详情（含 demo 直链）
   GET https://www.faceit.com/api/match/v2/match/{match_id}
   返回 demoURLs: ["https://demos-xxx.backblaze.faceit-cdn.net/cs2/xxx-1-1.dem.zst"]

备用域名：api.faceit.com 与 www.faceit.com/api 等价。

## ⚠️ 风险提示

- 内部接口**无官方文档**，FACEIT 可能随时变更或加风控。
- 请控制请求频率，避免给服务器造成压力。
- 仅用于个人学习/研究。

## ⚠️ 代理注意

demo CDN 域名（*.backblaze.faceit-cdn.net）需代理放行。
若本机走代理且该域名被拦，下载会 502。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import requests

# ------------------------------------------------------------------ #
# 接口常量
# ------------------------------------------------------------------ #
BASE = "https://www.faceit.com/api"
BASE_ALT = "https://api.faceit.com"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.faceit.com/",
    "Origin": "https://www.faceit.com",
}

# FACEIT 比赛历史里的编码字段 → 可读含义
# 实测对照得出，见 docs/FACEIT零门槛链路验证.md
FIELD_MAP = {
    "i6": "kills",          # 击杀
    "i8": "deaths",         # 死亡
    "i9": "assists",        # 助攻
    "i12": "rounds",        # 回合数
    "c2": "kd",             # KD 比
    "c4": "adr",            # ADR
    "c10": "hs_pct",        # 爆头率
    "i13": "score",         # 得分
    "i14": "result_code",   # 结果代码
    "i18": "score_str",     # 比分 "13 / 11"
    "i1": "map",            # 地图
    "i0": "region",         # 地区
    "i40": "streak",        # 连胜/连败
    "i20": "weapon_stat",   # 用枪统计
    "i3": "elo_before",     # 赛前 Elo 段
    "i4": "elo_after",
    "i5": "team_name",
    "i2": "map_entity_id",
}

RESULT_MAP = {
    "1": "win",
    "2": "loss",
    "3": "loss",
    "0": "unknown",
}


class FaceitError(Exception):
    """FACEIT 抓取异常。"""


def ts_ms_to_str(ts: Any) -> str:
    """毫秒时间戳 → 可读时间。"""
    try:
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M"
        )
    except (ValueError, TypeError, OSError):
        return "-"


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return default


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return default


class FaceitClient:
    """FACEIT 内部接口客户端（无需 Key）。

    Args:
        rps: 每秒最大请求数（限速，保护对方服务器也保护自己）。
        retry_times: 重试次数。
        retry_delay: 重试基础延迟（秒）。
        use_proxy: True 走系统代理；False 强制直连。
            注意：demo CDN 可能需要特定代理设置。
    """

    def __init__(
        self,
        rps: float = 2.0,
        retry_times: int = 3,
        retry_delay: float = 5.0,
        use_proxy: bool = True,
    ) -> None:
        self.retry_times = retry_times
        self.retry_delay = retry_delay
        self._min_interval = 1.0 / max(rps, 0.1)
        self._last_ts = 0.0

        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        if not use_proxy:
            self.session.trust_env = False

    # ------------------------------------------------------------------ #
    # 底层
    # ------------------------------------------------------------------ #
    def _throttle(self) -> None:
        wait = self._min_interval - (time.time() - self._last_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_ts = time.time()

    def _get(self, url: str, params: dict | None = None) -> Any:
        last_err: Exception | None = None

        for attempt in range(self.retry_times + 1):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, timeout=25)
            except requests.RequestException as exc:
                last_err = exc
                if attempt < self.retry_times:
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                raise FaceitError(f"请求失败：{url} — {exc}") from exc

            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise FaceitError(f"响应不是 JSON：{url}") from exc

            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt < self.retry_times:
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue

            if resp.status_code == 404:
                raise FaceitError(f"不存在（404）：{url}")
            if resp.status_code == 403:
                raise FaceitError(
                    f"被拒绝（403）：{url}\n"
                    "  可能是频率过高触发风控，请降低 rate limit 后重试。"
                )

            raise FaceitError(f"HTTP {resp.status_code}：{url}")

        raise FaceitError(f"重试失败：{url} — {last_err}")

    # ------------------------------------------------------------------ #
    # 业务接口
    # ------------------------------------------------------------------ #
    def get_player(self, nickname: str) -> dict[str, Any]:
        """昵称 → 玩家档案。

        返回：{player_id, nickname, country, avatar, elo, ...}
        """
        data = self._get(f"{BASE}/users/v1/nicknames/{nickname}")
        if data.get("result") != "OK":
            raise FaceitError(f"查询失败：{data}")

        payload = data.get("payload", {})
        games = payload.get("games", {}) or {}
        cs2 = games.get("cs2", {}) or {}

        return {
            "player_id": payload.get("id", ""),
            "nickname": payload.get("nickname", nickname),
            "country": payload.get("country", ""),
            "avatar": payload.get("avatar", ""),
            "activated_at": payload.get("activated_at", ""),
            "verified": payload.get("verified", False),
            "elo": cs2.get("faceit_elo", 0),
            "skill_level": cs2.get("skill_level", 0),
            "region": cs2.get("region", ""),
            "game_player_id": cs2.get("game_player_id", ""),
            "game_player_name": cs2.get("game_player_name", ""),
        }

    def get_match_history(
        self,
        player_id: str,
        game: str = "cs2",
        size: int = 20,
        page: int = 0,
    ) -> list[dict[str, Any]]:
        """比赛历史原始数据（编码字段）。"""
        data = self._get(
            f"{BASE}/stats/v1/stats/time/users/{player_id}/games/{game}",
            params={"size": size, "page": page},
        )
        return data if isinstance(data, list) else []

    def get_all_match_history(
        self,
        player_id: str,
        game: str = "cs2",
        max_matches: int = 30,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """翻页拿满指定数量。

        ⚠️ 实测硬上限：**offset 上限 300**（不论每页多大）——
        - size=20  → 能翻到 page=10，共 220 条（page 11 起 HTTP 400）
        - size=100 → 能翻到 page=2，共 300 条（page 3 起 HTTP 400）

        所以「最多能回溯 300 场」（donk666 实测约回溯 6 个月到 2026-03-20）。
        用 page_size=100 只需 3 次请求就能拿满 300 条，比 size=20 翻 11 页更省。
        """
        out: list[dict[str, Any]] = []
        page = 0
        # offset 硬上限，避免必然 400 的无效请求
        max_pages = max(1, (300 + page_size - 1) // page_size)
        while len(out) < max_matches and page < max_pages:
            batch = self.get_match_history(
                player_id, game=game, size=page_size, page=page
            )
            if not batch:
                break
            out.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
        return out[:max_matches]

    def get_match_detail(self, match_id: str) -> dict[str, Any]:
        """比赛详情原始响应（顶层含 code/message/payload）。"""
        return self._get(f"{BASE}/match/v2/match/{match_id}")

    def get_demo_urls(self, match_id: str) -> list[str]:
        """取该场所有 demo 直链。

        注意：demoURLs 位于响应的 payload 之下，不在顶层。
        """
        resp = self.get_match_detail(match_id)
        payload = resp.get("payload", resp) if isinstance(resp, dict) else {}

        urls = payload.get("demoURLs") or payload.get("demo_urls") or []
        if not urls:
            # 兜底：遍历 instances
            for inst in payload.get("instances", []) or []:
                if isinstance(inst, dict):
                    urls = urls + (inst.get("demos") or [])
        return [u for u in urls if isinstance(u, str) and u]

    # ------------------------------------------------------------------ #
    # 解析
    # ------------------------------------------------------------------ #
    @staticmethod
    def parse_match_record(raw: dict[str, Any]) -> dict[str, Any]:
        """把编码的比赛记录解析成可读结构。"""
        rec: dict[str, Any] = {
            "match_id": raw.get("matchId", ""),
            "game": raw.get("game", ""),
            "game_mode": raw.get("gameMode", ""),
            "region": raw.get("i0", ""),
            "map": raw.get("i1", ""),
            "kills": _to_int(raw.get("i6")),
            "deaths": _to_int(raw.get("i8")),
            "assists": _to_int(raw.get("i9")),
            "rounds": _to_int(raw.get("i12")),
            "kd": _to_float(raw.get("c2")),
            "adr": _to_float(raw.get("c4")),
            "hs_pct": _to_float(raw.get("c10")),
            "score": raw.get("i18", ""),
            "elo": _to_int(raw.get("elo")),
            "elo_delta": raw.get("elo_delta", ""),
            "team_name": raw.get("i5", ""),
            "started_at": raw.get("created_at", 0),
            "started_at_str": ts_ms_to_str(raw.get("created_at")),
            "demo_urls": [],
            "multikills": [],
        }
        # 结果：i14=3 / elo_delta 负数 → 负场
        delta = raw.get("elo_delta", "")
        try:
            d = int(delta)
            rec["result"] = "win" if d > 0 else ("loss" if d < 0 else "draw")
        except (ValueError, TypeError):
            rec["result"] = RESULT_MAP.get(str(raw.get("i14")), "unknown")
        return rec
