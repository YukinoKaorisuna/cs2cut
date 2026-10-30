# -*- coding: utf-8 -*-
"""生成「多杀清单」页面：只列有 4K/5K 的重点场次 + 3K 密集场次。

用法：
  python src/multikill_report.py                 # data/ 下全部选手
  python src/multikill_report.py donk666
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def collect(nick: str) -> dict | None:
    p = DATA_DIR / f"{nick}.json"
    if not p.exists():
        return None
    d = json.load(open(p, encoding="utf-8"))
    rows = []
    for m in d.get("matches", []):
        mk = m.get("multikills")
        if not isinstance(mk, dict):
            continue
        t3 = int(mk.get("triples", 0) or 0)
        t4 = int(mk.get("quadras", 0) or 0)
        t5 = int(mk.get("aces", 0) or 0)
        if t3 + t4 + t5 == 0:
            continue
        rows.append({
            "date": m.get("started_at_str", ""),
            "map": m.get("map", ""),
            "result": m.get("result", ""),
            "score": m.get("score", ""),
            "kills": m.get("kills", 0),
            "deaths": m.get("deaths", 0),
            "adr": m.get("adr", 0),
            "t3": t3, "t4": t4, "t5": t5,
            "total": t3 + t4 + t5,
            "url": (m.get("demo_urls") or [""])[0],
        })
    d["_rows"] = rows
    return d


def row_html(r: dict, hl: str = "") -> str:
    mk = []
    if r["t5"]:
        mk.append(f'<span class="mk mk-5">5K×{r["t5"]}</span>')
    if r["t4"]:
        mk.append(f'<span class="mk mk-4">4K×{r["t4"]}</span>')
    if r["t3"]:
        mk.append(f'<span class="mk mk-3">3K×{r["t3"]}</span>')
    res = {"win": '<span class="rw">胜</span>', "loss": '<span class="rl">负</span>'}.get(
        r["result"], '<span class="dim">—</span>')
    demo = (f'<a class="dl" href="{escape(r["url"])}" target="_blank" rel="noopener">下载</a>'
            if r["url"] else '<span class="dim">—</span>')
    return f"""
    <tr class="{hl}">
      <td class="t">{escape(r['date'])}</td>
      <td class="map">{escape(r['map'])}</td>
      <td class="mid">{res}</td>
      <td class="mid">{escape(r['score'])}</td>
      <td class="num"><span class="kv">{r['kills']}</span></td>
      <td class="num">{r['deaths']}</td>
      <td class="num">{r['adr']:.0f}</td>
      <td class="mkcell">{' '.join(mk)}</td>
      <td class="num strong">{r['total']}</td>
      <td class="mid">{demo}</td>
    </tr>"""


def build() -> str:
    nicks = [p.stem for p in sorted(DATA_DIR.glob("*.json"))]
    sections = []
    nav = []
    overview_rows = []

    for nick in nicks:
        d = collect(nick)
        if not d:
            continue
        rows = d["_rows"]
        if not rows:
            continue
        aces = [r for r in rows if r["t5"]]
        quads = [r for r in rows if r["t4"] and not r["t5"]]
        triple_heavy = [r for r in rows if not r["t4"] and not r["t5"]]
        triple_heavy.sort(key=lambda r: (r["t3"], r["kills"]), reverse=True)
        triple_heavy = triple_heavy[:15]

        t3 = sum(r["t3"] for r in rows)
        t4 = sum(r["t4"] for r in rows)
        t5 = sum(r["t5"] for r in rows)

        sid = "s-" + nick.replace(".", "_")
        nav.append(f'<a href="#{escape(sid)}">{escape(nick)}'
                   f'<b>{t5}</b></a>')
        overview_rows.append(f"""
        <tr onclick="location.hash='{escape(sid)}'" style="cursor:pointer">
          <td class="pk">{escape(nick)}</td>
          <td class="num">{d.get('elo','')}</td>
          <td class="num">{len(rows)}</td>
          <td class="num c3">{t3}</td>
          <td class="num c4">{t4}</td>
          <td class="num c5">{t5}</td>
        </tr>""")

        def block(title, items, cls, empty):
            if not items:
                return f'<h3>{title} <span class="n">0</span></h3><div class="none">{empty}</div>'
            body = "".join(row_html(r, cls) for r in items)
            return (f'<h3>{title} <span class="n">{len(items)}</span></h3>'
                    f'<table class="tbl"><thead><tr><th>时间(UTC)</th><th>地图</th><th>结果</th>'
                    f'<th>比分</th><th>击杀</th><th>死亡</th><th>ADR</th><th>多杀</th>'
                    f'<th>次数</th><th>Demo</th></tr></thead><tbody>{body}</tbody></table>')

        sections.append(f"""
        <section class="sec" id="{escape(sid)}">
          <div class="shead">
            <h2>{escape(nick)} <span class="elo">#{d.get('elo','')}</span></h2>
            <div class="sums">
              <span class="s s3">3K {t3}</span>
              <span class="s s4">4K {t4}</span>
              <span class="s s5">5K {t5}</span>
              <span class="s">共 {len(rows)} 场有多杀</span>
            </div>
          </div>
          {block('★ 5K / ACE 场次', aces, 'ace', '半年内没有 ACE')}
          {block('★ 4K 场次（无 5K）', quads, 'quad', '无')}
          {block('3K 密集场（Top 15）', triple_heavy, '', '无')}
        </section>""")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CS2 多杀场次清单</title><style>
 *{{box-sizing:border-box}}
 body{{margin:0;padding:30px 26px 60px;font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:#f5f6f8;color:#1a1d24}}
 h1{{font-size:23px;margin:0 0 5px;letter-spacing:-.5px}}
 .meta{{color:#6b7280;font-size:12.5px}}
 .sec{{margin-top:30px}}
 .shead{{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:14px}}
 .shead h2{{margin:0;font-size:18px}}
 .elo{{font-size:13px;color:#b54708;font-weight:600}}
 .sums{{display:flex;gap:8px;flex-wrap:wrap}}
 .s{{font-size:11.5px;font-weight:600;padding:4px 10px;border-radius:7px;background:#fff;border:1px solid #e5e7eb;color:#4b5563}}
 .s3{{background:#e0e7ff;color:#3730a3;border-color:#c7d2fe}}
 .s4{{background:#fce7f3;color:#9d174d;border-color:#fbcfe8}}
 .s5{{background:#fef0c7;color:#b54708;border-color:#fde68a}}
 h3{{font-size:14px;margin:22px 0 9px;color:#374151}}
 h3 .n{{font-size:11.5px;background:#e5e7eb;color:#4b5563;padding:1px 8px;border-radius:9px;margin-left:5px}}
 .none{{color:#9ca3af;font-size:12.5px;padding:10px 0}}
 .tbl{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
 .tbl th{{background:#fafbfc;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.4px;padding:9px 10px;text-align:left;border-bottom:1px solid #e5e7eb;white-space:nowrap}}
 .tbl td{{padding:8px 10px;font-size:13px;border-bottom:1px solid #f0f1f3}}
 .tbl tr:last-child td{{border-bottom:none}}
 .tbl tbody tr:hover{{background:#fafbfc}}
 .num{{text-align:right;font-variant-numeric:tabular-nums}}
 .num.strong{{font-weight:700}}
 .t{{color:#6b7280;white-space:nowrap;font-size:12px}}
 .map{{font-family:"SF Mono",Consolas,monospace;font-size:12px}}
 .mid{{text-align:center}}
 .kv{{font-weight:700}}
 tr.ace{{background:#fffbeb}}
 tr.ace .kv{{color:#b42318}}
 tr.quad{{background:#fdf4ff}}
 .rw{{color:#067647;font-weight:600;font-size:12px}}
 .rl{{color:#b42318;font-weight:600;font-size:12px}}
 .mk{{display:inline-block;padding:2px 7px;border-radius:5px;font-size:11px;font-weight:700;margin-right:3px;font-variant-numeric:tabular-nums}}
 .mk-5{{background:#fef0c7;color:#b54708}}
 .mk-4{{background:#fce7f3;color:#9d174d}}
 .mk-3{{background:#e0e7ff;color:#3730a3}}
 .dim{{color:#d1d5db}}
 .dl{{display:inline-block;padding:3px 10px;background:#1a1d24;color:#fff;border-radius:6px;font-size:11.5px;font-weight:600;text-decoration:none}}
 .dl:hover{{background:#374151}}
 .nav{{display:flex;gap:7px;flex-wrap:wrap;margin:16px 0 6px}}
 .nav a{{display:inline-flex;align-items:center;gap:6px;padding:6px 11px;background:#fff;border:1px solid #dcdfe4;border-radius:8px;font-size:12.5px;font-weight:600;color:#374151;text-decoration:none}}
 .nav a:hover{{border-color:#9aa3af}}
 .nav a b{{background:#fef0c7;color:#b54708;padding:1px 6px;border-radius:8px;font-size:10.5px;font-variant-numeric:tabular-nums}}
 .ovh{{font-size:14px;margin:26px 0 10px;color:#374151}}
 .pk{{font-weight:700}}
 .c3{{color:#3730a3}} .c4{{color:#9d174d}} .c5{{color:#b54708;font-weight:700}}
</style></head><body>
<h1>CS2 多杀场次清单</h1>
<div class="meta">生成于 {now} · 数据源 FACEIT 天梯 · 3K/4K/5K 取自比赛统计接口（无需 demo）</div>
<div class="nav">{''.join(nav)}</div>
<h3 class="ovh">总览（点击行跳转）</h3>
<table class="tbl"><thead><tr><th>选手</th><th>Elo</th><th>有多杀场次</th>
<th>3K 合计</th><th>4K 合计</th><th>5K(ACE) 合计</th></tr></thead>
<tbody>{''.join(overview_rows)}</tbody></table>
{''.join(sections)}
</body></html>"""


def main():
    out = ROOT / "output" / "multikills.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(), encoding="utf-8")
    print(f"OK {out}")


if __name__ == "__main__":
    main()
