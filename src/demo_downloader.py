# -*- coding: utf-8 -*-
"""
demo 下载模块。

## 好消息

FACEIT 内部接口直接返回 demo 直链（`demoURLs` 字段），
**不需要 signed URL、不需要 cookie、不需要申请审批**。

格式：
  https://demos-{region}.backblaze.faceit-cdn.net/cs2/{match_id}-1-1.dem.zst

## ⚠️ 代理注意

实测本机环境：demo CDN 域名（*.backblaze.faceit-cdn.net）走代理会 502，
直连则 DNS 解析失败。**需要把该域名加入代理放行规则**。

若下载失败，先检查代理设置。

## demo 特性

- 格式：`.dem.zst`（zstd 压缩），解析前需解压
- 有效期：官方标注 **30 天**
- 大小：通常 100~300 MB
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class DemoDownloadError(Exception):
    """demo 下载异常。"""


class DemoDownloader:
    """FACEIT demo 下载器。

    Args:
        output_dir: demo 保存目录。
        timeout: 单次请求超时（秒）。
        use_proxy: True 走系统代理；False 强制直连。
    """

    def __init__(
        self,
        output_dir: str | Path = "demos",
        timeout: int = 60,
        use_proxy: bool = True,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": DEFAULT_UA,
                "Referer": "https://www.faceit.com/",
            }
        )
        if not use_proxy:
            self.session.trust_env = False

    # ------------------------------------------------------------------ #
    @staticmethod
    def _safe_filename(match_id: str, url: str) -> str:
        name = url.split("?")[0].rstrip("/").split("/")[-1]
        name = re.sub(r'[<>:"/\\|?*]', "_", name) or f"{match_id}.dem.zst"
        return f"{match_id}_{name}"

    def probe(self, url: str) -> tuple[bool, str]:
        """探测 demo 链接是否可下载（只发 HEAD/Range 请求）。

        Returns:
            (是否可下载, 描述信息)
        """
        try:
            resp = self.session.get(
                url, headers={"Range": "bytes=0-1023"}, stream=True,
                timeout=self.timeout,
            )
            if resp.status_code in (200, 206):
                size = resp.headers.get("Content-Length", "?")
                resp.close()
                return True, f"可下载（片段 {size} bytes）"
            return False, f"HTTP {resp.status_code}"
        except requests.RequestException as exc:
            return False, f"连接失败：{type(exc).__name__} — {exc}"

    # ------------------------------------------------------------------ #
    def download(
        self,
        match_id: str,
        url: str,
        resume: bool = True,
    ) -> Path | None:
        """下载单个 demo，支持断点续传。

        Returns:
            保存路径；失败返回 None。
        """
        filename = self._safe_filename(match_id, url)
        target = self.output_dir / filename
        part = target.with_suffix(target.suffix + ".part")

        if target.exists() and target.stat().st_size > 0:
            print(f"  [=] 已存在，跳过：{target.name}")
            return target

        headers: dict[str, str] = {}
        mode = "wb"
        existing = 0
        if resume and part.exists():
            existing = part.stat().st_size
            headers["Range"] = f"bytes={existing}-"
            mode = "ab"

        try:
            with self.session.get(
                url, headers=headers, stream=True, timeout=self.timeout
            ) as resp:
                if resp.status_code not in (200, 206):
                    print(f"  [X] {match_id} 下载失败：HTTP {resp.status_code}")
                    if resp.status_code == 502:
                        print("      → 502 通常是代理拦截了 CDN 域名，"
                              "请把 *.backblaze.faceit-cdn.net 加入代理放行")
                    return None

                if existing and resp.status_code == 200:
                    mode, existing = "wb", 0
                    part.unlink(missing_ok=True)

                total = int(resp.headers.get("Content-Length", 0)) + existing
                done = existing
                last = time.time()
                t_start = time.time()

                with open(part, mode) as fh:
                    for chunk in resp.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        done += len(chunk)
                        now = time.time()
                        if now - last >= 2.0:
                            self._progress(match_id, done, total, t_start)
                            last = now

                part.replace(target)
                mb = done / 1024 / 1024
                el = time.time() - t_start
                print(f"  [OK] {target.name}  {mb:.1f} MB  耗时 {el:.1f}s")
                return target

        except requests.RequestException as exc:
            print(f"  [X] {match_id} 下载异常：{exc}")
            print("      （.part 已保留，可重试续传）")
            return None

    @staticmethod
    def _progress(match_id: str, done: int, total: int, t0: float) -> None:
        el = max(time.time() - t0, 0.1)
        speed = done / el / 1024 / 1024
        if total > 0:
            pct = done / total * 100
            left = (total - done) / max(speed * 1024 * 1024, 1)
            print(
                f"  [{match_id[:16]}] {done/1048576:>6.1f}/{total/1048576:.1f} MB "
                f"({pct:>5.1f}%)  {speed:>5.2f} MB/s  剩余 ~{left:>4.0f}s",
                flush=True,
            )
        else:
            print(f"  [{match_id[:16]}] {done/1048576:.1f} MB  {speed:.2f} MB/s",
                  flush=True)
