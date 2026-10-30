# -*- coding: utf-8 -*-
"""
生成 HTML 看板（真实数据版）。

读取 data/*.json（由 collector.py 产出），生成自包含单文件 HTML：
  - 选手 tab 切换（含 Elo / 地区）
  - 统计卡（高击杀场次、最高击杀、3K/4K/5K 合计）
  - 月份筛选（按 yyyy-mm 过滤表格）
  - 场次表格按击杀降序
  - 击杀色阶高亮（≥20 橙 / ≥25 红 / ≥30 深红）
  - 多杀标签（3K/4K/5K，取自 FACEIT i14/i15/i16，无需 demo）
  - demo 直链可点击下载

用法：
  python src/dashboard.py
"""

from __future__ import annotations

import json
from datetime import datetime
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config.json"


def load_all_data() -> list[dict]:
    datasets = []
    if not DATA_DIR.exists():
        return datasets
    for path in sorted(DATA_DIR.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("matches") is not None:
                datasets.append(data)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[!] 跳过 {path.name}：{exc}")
    # 按 Elo 降序
    datasets.sort(key=lambda d: d.get("elo", 0), reverse=True)
    return datasets


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def kill_badge(count: int) -> str:
    if count >= 30:
        return "k30"
    if count >= 25:
        return "k25"
    if count >= 20:
        return "k20"
    return ""


def multi_badges(mk: dict | list | None) -> str:
    """多杀标签。数据直接来自 FACEIT 的 i14/i15/i16，无需解析 demo。

    兼容两种形态：
      - dict: {"triples": n, "quadras": n, "aces": n}  ← 当前使用
      - list: [{"kill_count": 3}, ...]                 ← 旧 demo 解析产物
    """
    counts = {"5K": 0, "4K": 0, "3K": 0}
    if isinstance(mk, dict):
        counts["3K"] = int(mk.get("triples", 0) or 0)
        counts["4K"] = int(mk.get("quadras", 0) or 0)
        counts["5K"] = int(mk.get("aces", 0) or 0)
    elif isinstance(mk, list):
        for item in mk:
            n = item.get("kill_count", 0)
            if n >= 5:
                counts["5K"] += 1
            elif n == 4:
                counts["4K"] += 1
            elif n == 3:
                counts["3K"] += 1
    else:
        return ""
    parts = []
    for label in ("5K", "4K", "3K"):
        if counts[label]:
            cls = label.lower().replace("k", "")
            parts.append(f'<span class="mk mk-{cls}">{label}×{counts[label]}</span>')
    return " ".join(parts)


def result_cell(result: str) -> str:
    if result == "win":
        return '<span class="rw">胜</span>'
    if result == "loss":
        return '<span class="rl">负</span>'
    return '<span class="dim">—</span>'


def elo_cell(delta: str) -> str:
    """Elo 变化，红涨绿跌（中国习惯）。"""
    try:
        d = int(delta)
    except (ValueError, TypeError):
        return '<span class="dim">—</span>'
    if d > 0:
        return f'<span class="up">+{d}</span>'
    if d < 0:
        return f'<span class="down">{d}</span>'
    return '<span class="dim">0</span>'


def month_of(m: dict) -> str:
    """从 started_at_str（yyyy-mm-dd HH:MM）取 yyyy-mm。"""
    s = m.get("started_at_str", "") or ""
    return s[:7] if len(s) >= 7 else "unknown"


def build_html(datasets: list[dict], cfg: dict) -> str:
    title = cfg.get("dashboard", {}).get("title", "CS2 选手高光挖掘看板")
    min_kills = cfg.get("min_kills", 25)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    tabs, panels = [], []

    for i, ds in enumerate(datasets):
        nick = ds.get("nickname", "?")
        active = "active" if i == 0 else ""

        if ds.get("error"):
            tabs.append(f'<button class="tab {active}" data-tab="p{i}">{escape(nick)}</button>')
            panels.append(
                f'<div class="panel {active}" id="p{i}">'
                f'<div class="empty">采集失败：{escape(ds["error"])}</div></div>'
            )
            continue

        matches = ds.get("matches", [])
        ms = sorted(matches, key=lambda m: m.get("kills", 0), reverse=True)

        high = [m for m in ms if m.get("kills", 0) >= min_kills]
        with_demo = [m for m in matches if m.get("demo_urls")]
        best = ms[0] if ms else {}
        elo = ds.get("elo", 0)
        country = (ds.get("country") or "").upper()

        # 多杀合计（来自 FACEIT i14/i15/i16，无需 demo）
        mk_total = ds.get("multikills_total") or {}
        t3 = int(mk_total.get("triples", 0) or 0)
        t4 = int(mk_total.get("quadras", 0) or 0)
        t5 = int(mk_total.get("aces", 0) or 0)

        cards = f"""
        <div class="cards">
          <div class="card"><div class="cv">{len(matches)}</div><div class="cl">采集场次</div></div>
          <div class="card"><div class="cv hl">{len(high)}</div><div class="cl">≥{min_kills} 击杀</div></div>
          <div class="card"><div class="cv gold">{len(with_demo)}</div><div class="cl">demo 可下</div></div>
          <div class="card"><div class="cv">{best.get("kills", 0)}</div><div class="cl">最高击杀</div></div>
          <div class="card"><div class="cv">{best.get("adr", 0):.0f}</div><div class="cl">最高 ADR</div></div>
          <div class="card"><div class="cv">{elo}</div><div class="cl">FACEIT Elo</div></div>
          <div class="card mk3"><div class="cv">{t3}</div><div class="cl">3K 合计</div></div>
          <div class="card mk4"><div class="cv">{t4}</div><div class="cl">4K 合计</div></div>
          <div class="card mk5"><div class="cv">{t5}</div><div class="cl">5K(ACE) 合计</div></div>
        </div>"""

        rows = []
        months: list[str] = []
        for m in ms:
            k = m.get("kills", 0)
            badge = kill_badge(k)
            mk_html = multi_badges(m.get("multikills"))
            urls = m.get("demo_urls", [])
            mo = month_of(m)
            if mo not in months:
                months.append(mo)

            if urls:
                demo_cell = (
                    f'<a class="dl" href="{escape(urls[0])}" target="_blank" '
                    f'rel="noopener" title="{escape(urls[0])}">下载</a>'
                )
            elif m.get("demo_checked"):
                demo_cell = '<span class="dim">过期</span>'
            else:
                demo_cell = '<span class="dim">—</span>'

            rows.append(f"""
            <tr class="{badge}{' ace' if (isinstance(m.get('multikills'), dict) and int(m['multikills'].get('aces', 0) or 0) > 0) else ''}" data-month="{escape(mo)}">
              <td class="t">{escape(m.get("started_at_str", "-"))}</td>
              <td class="map">{escape(m.get("map", "-"))}</td>
              <td class="mid">{result_cell(m.get("result", ""))}</td>
              <td class="num"><span class="kv">{k}</span></td>
              <td class="num">{m.get("deaths", 0)}</td>
              <td class="num">{m.get("kd", 0):.2f}</td>
              <td class="num">{m.get("adr", 0):.0f}</td>
              <td class="num">{m.get("hs_pct", 0):.0f}</td>
              <td class="num">{elo_cell(m.get("elo_delta", ""))}</td>
              <td class="mkcell">{mk_html or '<span class="dim">—</span>'}</td>
              <td class="mid">{demo_cell}</td>
            </tr>""")

        # 月份筛选下拉（按时间倒序）
        months_sorted = sorted([x for x in months if x != "unknown"], reverse=True)
        opts = ['<option value="all">全部月份</option>']
        for mo in months_sorted:
            cnt_m = sum(1 for m in ms if month_of(m) == mo)
            opts.append(f'<option value="{escape(mo)}">{escape(mo)}（{cnt_m} 场）</option>')
        month_select = (
            f'<div class="filterbar">'
            f'<label>月份筛选</label>'
            f'<select class="mfilter" data-panel="p{i}">{"".join(opts)}</select>'
            f'<span class="fcount" id="fc{i}"></span>'
            f'</div>'
        )

        tabs.append(
            f'<button class="tab {active}" data-tab="p{i}">'
            f'{escape(nick)} <span class="cnt">{elo}</span></button>'
        )
        panels.append(f"""
        <div class="panel {active}" id="p{i}">
          <div class="phead">
            <img class="ava" src="{escape(ds.get('avatar', ''))}" alt=""
                 onerror="this.style.visibility='hidden'">
            <div>
              <h2>{escape(nick)} <span class="elo">#{elo}</span></h2>
              <div class="sub">
                地区 {escape(country or '-')} ·
                等级 {ds.get('skill_level', '-')} ·
                ID {escape(ds.get('player_id', '-')[:18])} ·
                采集 {escape(ds.get('collected_at', '')[:16].replace('T', ' '))}
              </div>
            </div>
          </div>
          {cards}
          {month_select}
          <table class="tbl" id="tbl{i}">
            <thead><tr>
              <th>时间(UTC)</th><th>地图</th><th>结果</th><th>击杀</th><th>死亡</th>
              <th>KD</th><th>ADR</th><th>HS%</th><th>Elo</th><th>多杀</th><th>Demo</th>
            </tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </div>""")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(title)}</title><style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:30px 26px 60px;
    font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    background:#f5f6f8; color:#1a1d24; }}
  h1 {{ font-size:23px; margin:0 0 5px; letter-spacing:-.5px; }}
  .meta {{ color:#6b7280; font-size:12.5px; }}
  .tabs {{ display:flex; gap:8px; margin:20px 0 16px; flex-wrap:wrap; }}
  .search {{ padding:8px 12px; border:1px solid #dcdfe4; background:#fff;
    border-radius:8px; font-size:13px; font-family:inherit; min-width:180px;
    margin-bottom:12px; }}
  .search:focus {{ outline:none; border-color:#9aa3af; }}
  .tab {{ padding:9px 15px; border:1px solid #dcdfe4; background:#fff;
    border-radius:8px; cursor:pointer; font-size:13.5px; font-weight:600;
    color:#4b5563; transition:all .15s; font-family:inherit; }}
  .tab:hover {{ border-color:#9aa3af; }}
  .tab.active {{ background:#1a1d24; color:#fff; border-color:#1a1d24; }}
  .cnt {{ display:inline-block; margin-left:6px; padding:1px 7px;
    background:rgba(0,0,0,.07); border-radius:10px; font-size:11.5px;
    font-variant-numeric:tabular-nums; }}
  .tab.active .cnt {{ background:rgba(255,255,255,.2); }}
  .panel {{ display:none; }} .panel.active {{ display:block; }}
  .phead {{ display:flex; align-items:center; gap:14px; margin:8px 0 18px; }}
  .ava {{ width:50px; height:50px; border-radius:10px; object-fit:cover;
    background:#e5e7eb; }}
  .phead h2 {{ margin:0 0 3px; font-size:18px; }}
  .elo {{ font-size:13px; color:#b54708; font-weight:600; margin-left:4px; }}
  .sub {{ color:#6b7280; font-size:11.5px; }}
  .cards {{ display:grid; gap:10px; margin-bottom:18px;
    grid-template-columns:repeat(auto-fit,minmax(125px,1fr)); }}
  .card {{ background:#fff; border:1px solid #e5e7eb; border-radius:10px;
    padding:13px 15px; }}
  .cv {{ font-size:22px; font-weight:700; line-height:1.1;
    font-variant-numeric:tabular-nums; }}
  .cv.hl {{ color:#d92d20; }} .cv.gold {{ color:#b54708; }}
  .card.mk3 .cv {{ color:#3730a3; }}
  .card.mk4 .cv {{ color:#9d174d; }}
  .card.mk5 .cv {{ color:#b54708; }}
  .card.mk5 {{ background:#fffbeb; border-color:#fde68a; }}
  .cl {{ font-size:11.5px; color:#6b7280; margin-top:3px; }}
  .filterbar {{ display:flex; align-items:center; gap:10px; margin:0 0 12px; }}
  .filterbar label {{ font-size:12.5px; color:#6b7280; font-weight:600; }}
  .mfilter {{ padding:7px 11px; border:1px solid #dcdfe4; background:#fff;
    border-radius:8px; font-size:13px; color:#1a1d24; font-family:inherit;
    cursor:pointer; min-width:150px; }}
  .mfilter:hover {{ border-color:#9aa3af; }}
  .fcount {{ font-size:12px; color:#6b7280; font-variant-numeric:tabular-nums; }}
  .tbl {{ width:100%; border-collapse:collapse; background:#fff;
    border-radius:10px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,.06); }}
  .tbl th {{ background:#fafbfc; font-size:11px; font-weight:600;
    color:#6b7280; text-transform:uppercase; letter-spacing:.4px;
    padding:10px 10px; text-align:left; border-bottom:1px solid #e5e7eb;
    white-space:nowrap; }}
  .tbl td {{ padding:9px 10px; font-size:13px; border-bottom:1px solid #f0f1f3; }}
  .tbl tr:last-child td {{ border-bottom:none; }}
  .tbl tbody tr:hover {{ background:#fafbfc; }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .t {{ color:#6b7280; white-space:nowrap; font-size:12px; }}
  .map {{ font-family:"SF Mono",Consolas,monospace; font-size:12px; }}
  .mid {{ text-align:center; }}
  .kv {{ font-weight:700; }}
  tr.k20 .kv {{ color:#b54708; }}
  tr.k25 .kv {{ color:#d92d20; }}
  tr.k30 {{ background:#fff5f4; }}
  tr.k30 .kv {{ color:#b42318; font-size:14.5px; }}
  tr.ace {{ background:#fffbeb; }}
  .rw {{ color:#067647; font-weight:600; font-size:12px; }}
  .rl {{ color:#b42318; font-weight:600; font-size:12px; }}
  .up {{ color:#d92d20; font-weight:600; font-size:12px; font-variant-numeric:tabular-nums; }}
  .down {{ color:#067647; font-weight:600; font-size:12px; font-variant-numeric:tabular-nums; }}
  .mk {{ display:inline-block; padding:2px 7px; border-radius:5px;
    font-size:11px; font-weight:700; margin-right:3px;
    font-variant-numeric:tabular-nums; }}
  .mk-5 {{ background:#fef0c7; color:#b54708; }}
  .mk-4 {{ background:#fce7f3; color:#9d174d; }}
  .mk-3 {{ background:#e0e7ff; color:#3730a3; }}
  .dim {{ color:#d1d5db; }}
  .dl {{ display:inline-block; padding:3px 10px; background:#1a1d24; color:#fff;
    border-radius:6px; font-size:11.5px; font-weight:600; text-decoration:none; }}
  .dl:hover {{ background:#374151; }}
  .empty {{ background:#fff; border:1px solid #e5e7eb; border-radius:10px;
    padding:40px; text-align:center; color:#6b7280; }}
  .note {{ margin-top:26px; padding:14px 16px; background:#fffbeb;
    border:1px solid #fde68a; border-radius:10px; font-size:12.5px;
    color:#92400e; line-height:1.65; }}
  .note b {{ color:#78350f; }}
</style>
</head>
<body>
<header>
  <h1>{escape(title)}</h1>
  <div class="meta">生成于 {generated} · {len(datasets)} 位选手 ·
    数据源 FACEIT 公开接口</div>
</header>

<div class="searchbar">
  <input class="search" id="playerSearch" type="text"
         placeholder="搜索选手昵称…" autocomplete="off">
</div>
<div class="tabs">{''.join(tabs)}</div>
{''.join(panels)}

<div class="note">
  <b>数据说明：</b>击杀 / 死亡 / KD / ADR / HS% / 多杀（3K·4K·5K）均为 FACEIT 天梯<b>整场聚合数据</b>，
  直接取自 FACEIT 比赛统计接口，<b>无需下载与解析 demo</b>。
  <br>
  <b>采集范围：</b>FACEIT 历史接口有 offset 硬上限 <b>300 场</b>（本页约回溯半年），再往前的比赛取不到。
  可用左上角「月份筛选」按月查看。
  <br>
  <b>粒度边界：</b>FACEIT 只提供「整场出现了几次 3K / 4K / 5K」，
  <b>不提供「第几回合拿的」</b>。若要定位到具体回合（如"第 7 回合 5 杀"），
  仍需下载 demo 用 <code>parse_demos.py</code> 逐回合解析。
  <br>
  <b>Demo 列：</b>比赛录像直链（官方有效期约 30 天），可用于逐回合复盘或切片。
</div>

<script>
document.querySelectorAll('.tab').forEach(function (btn) {{
  btn.addEventListener('click', function () {{
    document.querySelectorAll('.tab').forEach(function (b) {{ b.classList.remove('active'); }});
    document.querySelectorAll('.panel').forEach(function (p) {{ p.classList.remove('active'); }});
    btn.classList.add('active');
    var t = document.getElementById(btn.dataset.tab);
    if (t) t.classList.add('active');
  }});
}});

// 月份筛选
function applyMonth(sel) {{
  var pid = sel.dataset.panel;
  var val = sel.value;
  var tbl = document.getElementById('tbl' + pid.replace('p', ''));
  if (!tbl) return;
  var rows = tbl.querySelectorAll('tbody tr');
  var shown = 0;
  rows.forEach(function (tr) {{
    var ok = (val === 'all') || (tr.dataset.month === val);
    tr.style.display = ok ? '' : 'none';
    if (ok) shown++;
  }});
  var fc = document.getElementById('fc' + pid.replace('p', ''));
  if (fc) fc.textContent = '显示 ' + shown + ' / ' + rows.length + ' 场';
}}
document.querySelectorAll('.mfilter').forEach(function (sel) {{
  sel.addEventListener('change', function () {{ applyMonth(sel); }});
  applyMonth(sel);
}});

// 选手搜索
var searchBox = document.getElementById('playerSearch');
if (searchBox) {{
  searchBox.addEventListener('input', function () {{
    var q = this.value.trim().toLowerCase();
    document.querySelectorAll('.tab').forEach(function (t) {{
      var name = (t.textContent || '').toLowerCase();
      t.style.display = (!q || name.indexOf(q) >= 0) ? '' : 'none';
    }});
  }});
}}
</script>
</body>
</html>"""


def main() -> None:
    cfg = load_config()
    datasets = load_all_data()
    if not datasets:
        print("[!] data/ 无数据。请先运行 collector.py")
        return

    html = build_html(datasets, cfg)
    out_rel = cfg.get("dashboard", {}).get("output", "output/dashboard.html")
    out_path = ROOT / out_rel
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"[OK] 看板已生成：{out_path}")


if __name__ == "__main__":
    main()
