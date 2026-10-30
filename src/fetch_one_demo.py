# -*- coding: utf-8 -*-
"""
按「单场比赛」下载 FACEIT demo —— 一条命令搞定。

适用场景：你手上只有一场比赛的链接（或 match_id），想立刻把 demo 拉下来，
不想跑整套选手采集流程。

用法：
  # 1) 直接给 FACEIT 比赛页面链接（最省事）
  python src/fetch_one_demo.py --match "https://www.faceit.com/en/cs2/room/1-xxxxxxxx-...."

  # 2) 只给 match_id
  python src/fetch_one_demo.py --match 1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

  # 3) 只要 demo 直链，不下载（拿去给别的下载器/浏览器）
  python src/fetch_one_demo.py --match <...> --list-only

  # 4) 你已经在浏览器里拿到了直链，直接下
  python src/fetch_one_demo.py --url "https://demos-eu.backblaze.faceit-cdn.net/cs2/xxx-1-1.dem.zst"

  # 5) 若走代理下载 502 / DNS 失败 → 强制直连
  python src/fetch_one_demo.py --match <...> --no-proxy

关于代理（重要）：
  demo CDN 域名 *.backblaze.faceit-cdn.net 走代理常 502、直连又可能 DNS 失败。
  本脚本会先用当前设置探测，失败自动换另一种方式重试，并打印到底哪种通了。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import requests

try:  # 自检走 verify=False，静音 urllib3 警告
    requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))

from demo_downloader import DemoDownloader  # noqa: E402
from faceit_client import FaceitClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# FACEIT match_id 形态：1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
_MATCH_ID_RE = re.compile(
    r"((?:\d-)?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def extract_match_id(text: str) -> str | None:
    """从链接或纯文本里抠出 match_id。"""
    m = _MATCH_ID_RE.search(text or "")
    return m.group(1) if m else None


def resolve_urls(match_id: str, use_proxy: bool) -> list[str]:
    client = FaceitClient(use_proxy=use_proxy)
    urls = client.get_demo_urls(match_id)
    if not urls:
        # 兜底：有些场次 link 在别处，直接抓原始详情打印关键字段
        detail = client.get_match_detail(match_id)
        payload = detail.get("payload", detail) if isinstance(detail, dict) else {}
        keys = [k for k in payload.keys() if "demo" in k.lower()] if payload else []
        print(f"[!] 该场没有 demoURLs。payload 里跟 demo 有关的键：{keys or '无'}")
        print("    可能原因：比赛超过 30 天 / 未打完 / 非 CS2 / match_id 不对。")
    return urls


def diagnose_host(url: str) -> tuple[bool, str]:
    """用独立 DNS（直连 AliDNS DoH）判断 demo 主机名能否在公网解析。

    这一步是为了把「你本机网络问题」和「FACEIT 给的域名本身不存在」区分开 ——
    后者换任何网络都下不了，别再折腾代理。
    """
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    if not host:
        return False, "URL 不含主机名"
    try:
        s = requests.Session()
        s.trust_env = False  # 绕开本机代理，直接问公共 DNS
        r = s.get(
            "https://223.5.5.5/resolve",
            params={"name": host, "type": "A"},
            timeout=8,
            verify=False,
        )
        j = r.json()
        ips = [a["data"] for a in (j.get("Answer") or []) if a.get("type") == 1]
    except Exception as exc:  # 网络不通时给中性结论，别误判
        return False, f"DNS 自检未完成（{type(exc).__name__}），无法区分原因"
    if ips:
        return True, f"{host} → {ips}"
    return False, f"{host} 在公网**没有 A 记录**"


def download_with_fallback(
    match_id: str, urls: list[str], out_dir: Path, force_no_proxy: bool
) -> int:
    """下载，若代理方式失败则自动换直连重试。返回成功个数。"""
    # force_no_proxy 时只试直连；否则先代理后直连
    modes = [("直连", False)] if force_no_proxy else [("代理", True), ("直连", False)]

    saved = 0
    for url in urls:
        # 先探测，用能通的方式下
        chosen: tuple[str, bool] | None = None
        for label, use_proxy in modes:
            dd = DemoDownloader(output_dir=out_dir, use_proxy=use_proxy)
            ok, msg = dd.probe(url)
            print(f"  [探测/{label}] {msg}")
            if ok:
                chosen = (label, use_proxy)
                break
        if chosen is None:
            print("  [X] 代理和直连都探测失败，做 DNS 自检：")
            resolvable, detail = diagnose_host(url)
            print(f"      {detail}")
            if not resolvable:
                print("      → 结论：**这是 FACEIT 侧的域名问题，不是你本机网络问题**，")
                print("        换代理 / 换网络 / 用浏览器都下不了。只能等 FACEIT 修复，")
                print("        或换其它来源（HLTV / 队友分享的 demo 文件）。")
            else:
                print("      → 域名本身正常，是本机到 CDN 的链路问题：")
                print("        请把 *.faceit-cdn.net 加入代理放行规则，或关掉代理重试。")
            continue

        label, use_proxy = chosen
        print(f"  → 用【{label}】下载")
        dd = DemoDownloader(output_dir=out_dir, use_proxy=use_proxy)
        if dd.download(match_id, url):
            saved += 1
    return saved


def main() -> None:
    ap = argparse.ArgumentParser(description="按单场比赛下载 FACEIT demo")
    ap.add_argument("--match", help="FACEIT 比赛链接 或 match_id")
    ap.add_argument("--url", action="append", default=[], help="demo 直链（可重复）")
    ap.add_argument("--dir", default=None, help="保存目录（默认取 config.json 的 demo_download.output_dir）")
    ap.add_argument("--list-only", action="store_true", help="只打印直链，不下载")
    ap.add_argument("--no-proxy", action="store_true", help="强制直连（代理 502 时用）")
    ap.add_argument("--diagnose", action="store_true", help="只做 DNS 自检并给结论，不下载")
    args = ap.parse_args()

    if not args.match and not args.url:
        ap.print_help()
        print("\n[!] 至少要给 --match 或 --url")
        sys.exit(2)

    import json
    out_dir = ROOT / "demos"
    cfg_path = ROOT / "config.json"
    if args.dir:
        out_dir = Path(args.dir)
    elif cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as fh:
            out_dir = ROOT / json.load(fh).get("demo_download", {}).get("output_dir", "demos")

    urls: list[str] = list(args.url)

    if args.match:
        mid = extract_match_id(args.match) or args.match.strip()
        print(f"match_id: {mid}\n")
        found = resolve_urls(mid, use_proxy=not args.no_proxy)
        for u in found:
            print(f"  demo: {u}")
        print()
        urls.extend(found)
        match_id = mid
    else:
        # 从直链里尽量抠出 match_id 命名用
        match_id = extract_match_id(urls[0]) or "manual"

    if not urls:
        sys.exit(1)

    if args.list_only:
        print("（--list-only：仅列出，未下载）")
        return

    if args.diagnose:
        print("=== DNS 自检 ===")
        for u in urls:
            ok, detail = diagnose_host(u)
            print(f"  [{'✓' if ok else '✗'}] {detail}")
        return

    print(f"保存到：{out_dir}\n")
    n = download_with_fallback(match_id, urls, out_dir, args.no_proxy)
    print(f"\n完成 {n}/{len(urls)} 个 demo")


if __name__ == "__main__":
    main()
