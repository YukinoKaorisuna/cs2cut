# -*- coding: utf-8 -*-
"""
FACEIT Data API 封装（官方公开接口）。

官方文档：https://docs.faceit.com/docs/data-api/data
Base URL：https://open.faceit.com/data/v4

注意：
  - 所有请求需要 API Key（Bearer 认证），Key 在 https://developers.faceit.com 申请。
  - 官方接口只提供「整场聚合数据」，不含逐回合多杀统计（3K/4K/5K）。
    多杀统计需要解析 demo 文件，见 demo_parser.py。
"""

from __future__ import annotations

import time
from typing import Any

import requests

BASE_URL = "https://open.faceit.com/data/v4"

# FACEIT game id 常量
GAME_CS2 = "cs2"
GAME_CSGO = "csgo"


class FaceitAPIError(Exception):
    """FACEIT API 调用异常。"""


class FaceitAPI:
    """FACEIT 官方 Data API 客户端。

    Args:
        api_key: FACEIT developer server-side API key。
        rps: 限速，每秒最大请求数（官方有 rate limit，建议 <= 3）。
        retry_times: 遇到 429/5xx 时的重试次数。
        retry_delay: 重试基础延迟（秒），按次数指数退避。
    """

    def __init__(
        self,
        api_key: str,
        rps: int = 3,
        retry_times: int = 3,
        retry_delay: float = 5.0,
    ) -> None:
        if not api_key:
            raise FaceitAPIError("缺少 FACEIT API Key，请先在 config.json 中填写 faceit_api_key")
        self.api_key = api_key
        self.retry_times = retry_times
        self.retry_delay = retry_delay
        self._min_interval = 1.0 / max(rps, 1)
        self._last_request_ts = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------ #
    # 底层请求
    # ------------------------------------------------------------------ #
    def _throttle(self) -> None:
        """简单的客户端限速，避免触发 429。"""
        elapsed = time.time() - self._last_request_ts
        wait = self._min_interval - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.time()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        last_err: Exception | None = None

        for attempt in range(self.retry_times + 1):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, timeout=20)
            except requests.RequestException as exc:
                last_err = exc
                if attempt < self.retry_times:
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                raise FaceitAPIError(f"请求失败：{url} — {exc}") from exc

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt < self.retry_times:
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue

            # 404 表示玩家/比赛不存在，单独给明确信息
            if resp.status_code == 404:
                raise FaceitAPIError(f"资源不存在（404）：{url}")

            if resp.status_code == 401:
                raise FaceitAPIError("API Key 无效或已过期（401），请检查 config.json 中的 faceit_api_key")

            if resp.status_code == 403:
                raise FaceitAPIError(f"无权访问（403）：{url} — 可能是 Key 权限不足")

            raise FaceitAPIError(
                f"HTTP {resp.status_code}：{url} — {resp.text[:200]}"
            )

        raise FaceitAPIError(f"重试 {self.retry_times} 次后仍失败：{url} — {last_err}")

    # ------------------------------------------------------------------ #
    # 玩家相关
    # ------------------------------------------------------------------ #
    def get_player_by_nickname(self, nickname: str) -> dict[str, Any]:
        """按昵称查玩家（昵称大小写敏感）。"""
        return self._get("/players", params={"nickname": nickname, "game": GAME_CS2})

    def get_player_by_id(self, player_id: str) -> dict[str, Any]:
        """按 player_id 查玩家。"""
        return self._get(f"/players/{player_id}")

    def get_player_stats(self, player_id: str, game: str = GAME_CS2) -> dict[str, Any]:
        """玩家生涯统计（KD、ADR、HS%、胜率等聚合数据）。"""
        return self._get(f"/players/{player_id}/stats/{game}")

    def get_match_history(
        self,
        player_id: str,
        game: str = GAME_CS2,
        limit: int = 30,
        offset: int = 0,
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> dict[str, Any]:
        """玩家比赛历史列表（分页）。

        Args:
            limit: 单页条数，官方上限 100。
            offset: 起始位置。
            from_ts / to_ts: Unix 时间戳过滤范围。
        """
        params: dict[str, Any] = {
            "game": game,
            "offset": offset,
            "limit": min(limit, 100),
        }
        if from_ts is not None:
            params["from"] = from_ts
        if to_ts is not None:
            params["to"] = to_ts
        return self._get(f"/players/{player_id}/history", params=params)

    def get_all_match_history(
        self,
        player_id: str,
        game: str = GAME_CS2,
        max_matches: int = 100,
    ) -> list[dict[str, Any]]:
        """翻页拿满指定数量的比赛历史。"""
        collected: list[dict[str, Any]] = []
        offset = 0
        page_size = 100

        while len(collected) < max_matches:
            data = self.get_match_history(
                player_id, game=game, limit=page_size, offset=offset
            )
            items = data.get("items", [])
            if not items:
                break
            collected.extend(items)
            if len(items) < page_size:
                break
            offset += page_size

        return collected[:max_matches]

    # ------------------------------------------------------------------ #
    # 比赛相关
    # ------------------------------------------------------------------ #
    def get_match_details(self, match_id: str) -> dict[str, Any]:
        """比赛详情（含队伍、比分、demo_url 列表等）。"""
        return self._get(f"/matches/{match_id}")

    def get_match_stats(self, match_id: str) -> dict[str, Any]:
        """比赛统计详情（每个选手整场的 K/D/A、ADR、HS% 等）。"""
        return self._get(f"/matches/{match_id}/stats")


def extract_player_stats(match_stats: dict[str, Any], player_id: str) -> dict[str, Any] | None:
    """从 match_stats 响应中抽取指定选手的统计。

    FACEIT 的 stats 结构较深，这里做一层稳健的提取：
    rounds[].teams[].players[]，每个 player 有 player_id 和 player_stats。
    """
    rounds = match_stats.get("rounds", [])
    for rnd in rounds:
        for team in rnd.get("teams", []):
            for player in team.get("players", []):
                if player.get("player_id") == player_id:
                    stats = {s.get("stat_name"): s.get("stat_value")
                             for s in player.get("player_stats", [])}
                    return stats
    return None


def safe_int(value: Any) -> int:
    """把 FACEIT 返回的字符串数字安全转 int。"""
    try:
        if value is None:
            return 0
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return 0
