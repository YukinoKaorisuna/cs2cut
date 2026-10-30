# -*- coding: utf-8 -*-
"""多杀排行榜：把所有已完成选手汇总排序，输出文本 + HTML。"""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
OUT = Path(__file__).resolve().parent.parent / "output"

REAL = {
    "donk666": "donk", "dem0nnnnnnnn": "dem0n", "nipl": "npl",
    "z1Nny": "z1Nny", "CEMEN_BAKIN": "kyousuke", "m0NESY": "m0NESY",
    "w0nderful": "w0nderful", "b1t": "b1t", "niko": "NiKo",
    "sh1ro": "sh1ro", "ZywOo": "ZywOo", "broky": "broky",
    "karrigan": "karrigan", "Twistzz": "Twistzz", "ropz": "ropz",
}

rows = []
for p in sorted(DATA.glob("*.json")):
    nick = p.stem
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        continue
    mt = d.get("multikills_total") or {}
    ms = d.get("matches", [])
    ok = sum(1 for m in ms if isinstance(m.get("multikills"), dict))
    if not ok:
        continue
    complete = bool(mt)
    rows.append({
        "nick": nick, "real": REAL.get(nick, nick),
        "elo": d.get("elo", 0), "lvl": d.get("skill_level", 0),
        "games": len(ms), "ok": ok,
        "t3": mt.get("triples", 0), "t4": mt.get("quadras", 0),
        "t5": mt.get("aces", 0), "complete": complete,
    })

# 完整的排前，按 5K/4K/3K 降序；未完成的排后面
rows.sort(key=lambda r: (r["complete"], r["t5"], r["t4"], r["t3"]),
          reverse=True)

# 计算场均率（每 100 场），便于不同样本量间公平比较
for r in rows:
    n = max(r["ok"], 1)
    r["r3"] = r["t3"] / n * 100
    r["r4"] = r["t4"] / n * 100
    r["r5"] = r["t5"] / n * 100

lines = [f"{'选手':<14}{'Elo':>5}{'有效场':>6}{'3K':>6}{'4K':>6}{'5K':>5}"
         f"{'5K/百场':>9}"]
lines.append("-" * 58)
for r in rows:
    if r["complete"]:
        lines.append(f"{r['real']:<14}{r['elo']:>5}{r['ok']:>6}"
                     f"{r['t3']:>6}{r['t4']:>6}{r['t5']:>5}{r['r5']:>9.1f}")
    else:
        lines.append(f"{r['real']:<14}{r['elo']:>5}{r['ok']:>6}"
                     f"{'—':>6}{'—':>6}{'—':>5}{'—':>9}  (补全中)")
(Path(__file__).resolve().parent.parent / "_rank.txt").write_text("\n".join(lines), encoding="utf-8")

# 按场均 5K 排序的归一化榜
by_rate = sorted([r for r in rows if r["complete"]],
                 key=lambda r: (r["r5"], r["r4"], r["r3"]), reverse=True)

# HTML
def row_html(r, rank=None):
    rk = f'<td class="rk">{rank}</td>' if rank is not None else ""
    if not r["complete"]:
        return (f'<tr class="pending">{rk}<td class="pk">{r["real"]}'
                f'<span class="fk">{r["nick"]}</span></td>'
                f'<td class="num">{r["elo"]}</td><td class="num">{r["ok"]}</td>'
                f'<td class="num dim" colspan="4">补全中…</td></tr>')
    return (f'<tr>{rk}<td class="pk">{r["real"]}<span class="fk">{r["nick"]}</span></td>'
            f'<td class="num">{r["elo"]}</td><td class="num">{r["ok"]}</td>'
            f'<td class="num c3">{r["t3"]}</td><td class="num c4">{r["t4"]}</td>'
            f'<td class="num c5">{r["t5"]}</td>'
            f'<td class="num rk5">{r["r5"]:.1f}</td></tr>')

trs = "".join(row_html(r) for r in rows)
trs_rate = "".join(row_html(r, i) for i, r in enumerate(by_rate, 1))

html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CS2 明星选手多杀排行榜</title>
<style>
 *{{box-sizing:border-box}}
 body{{margin:0;padding:34px 26px 60px;background:#f5f6f8;color:#1a1d24;
   font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
 h1{{font-size:23px;margin:0 0 6px;letter-spacing:-.5px}}
 .meta{{color:#6b7280;font-size:12.5px;margin-bottom:20px}}
 .tbl{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;
   overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06);max-width:760px}}
 th{{background:#fafbfc;font-size:11px;font-weight:600;color:#6b7280;
   text-transform:uppercase;letter-spacing:.4px;padding:11px 12px;text-align:left;
   border-bottom:1px solid #e5e7eb}}
 td{{padding:10px 12px;font-size:13.5px;border-bottom:1px solid #f0f1f3}}
 tr:last-child td{{border-bottom:none}}
 tbody tr:hover{{background:#fafbfc}}
 .num{{text-align:right;font-variant-numeric:tabular-nums}}
 .pk{{font-weight:700}}
 .fk{{color:#9ca3af;font-size:11px;margin-left:7px;font-weight:400}}
 .c3{{color:#3730a3}} .c4{{color:#9d174d;font-weight:600}}
 .c5{{color:#b54708;font-weight:700}}
 tr.pending td{{opacity:.55}}
 .dim{{color:#9ca3af;text-align:right;font-size:12.5px}}
 .rk{{color:#9ca3af;font-weight:700;font-size:12px;width:34px}}
 .rk5{{color:#b54708;font-weight:600}}
 h2{{font-size:15px;margin:30px 0 10px;color:#374151}}
 .hint{{font-size:12px;color:#6b7280;margin-bottom:10px;line-height:1.6}}
 .hint b{{color:#374151}}
</style></head><body>
<h1>CS2 明星选手多杀排行榜</h1>
<div class="meta">FACEIT 天梯 · 各选手近期最多 300 场 · 多杀取自比赛统计接口 · 无需 demo</div>

<h2>① 总量榜（按 5K 降序）</h2>
<div class="hint">注意：各选手样本量不同（ropz 仅 70 场，ZywOo 有 275 场），
总量直接比会偏袒打得多的人 —— <b>看下面的场均率榜更公平</b>。</div>
<table class="tbl"><thead><tr>
<th>选手</th><th>Elo</th><th>有效场次</th><th>3K 合计</th><th>4K 合计</th><th>5K(ACE) 合计</th><th>5K/百场</th>
</tr></thead><tbody>{trs}</tbody></table>

<h2>② 场均率榜（每 100 场出了多少 5K，消除样本量差异）</h2>
<div class="hint">这个榜回答「谁的多杀<b>效率</b>最高」，与打了多少场无关。</div>
<table class="tbl"><thead><tr>
<th>#</th><th>选手</th><th>Elo</th><th>有效场次</th><th>3K 合计</th><th>4K 合计</th><th>5K(ACE) 合计</th><th>5K/百场</th>
</tr></thead><tbody>{trs_rate}</tbody></table>
</body></html>"""
(OUT / "multikill_ranking.html").write_text(html, encoding="utf-8")
print("done")
