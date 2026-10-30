# -*- coding: utf-8 -*-
"""批量循环（/api/batch 的编排逻辑）集成测试 —— 不碰 CSDM、不开 CS2。

把 record_one / resolve_player / preflight / wait_cs2_exit 全部换成桩，
真实跑一遍后台线程 + 轮询 /api/task，验证这些「只有多场连跑才会暴露」的行为：

  1. 编号前缀-NN 严格对应「文件名升序」
  2. 成品已存在 → 跳过（断点续录），编号不漂移
  3. ok / partial / fail 三种结果都被正确计数并写进 results
  4. 成功场次的 _parts 工作目录被清掉；失败场次的保留
  5. 结束后 status=done、videos 只含 ok/partial、汇总计数自洽

跑法：python tools/e2e_batch_loop.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "recorder"))

FAIL = []


def check(name: str, cond: bool, extra: str = ""):
    print(f"  [{'✓' if cond else '✗'}] {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


# 每个 demo 想模拟的行为（按 demo 文件名）
# ⚠️ b.dem 排序后是 02，被预置的 donk-02.mp4 命中 → 走「跳过」，永远不会进
#    record_one，所以不能把 partial 放它身上。partial 放 d.dem（04）。
BEHAVIOR = {"a.dem": "ok", "c.dem": "fail", "d.dem": "partial"}


def main() -> int:
    import app as wb  # noqa: E402

    real = (wb.record_one, wb.resolve_player, wb.preflight, wb.wait_cs2_exit)
    bak = wb.PREFS_PATH.read_text(encoding="utf-8") if wb.PREFS_PATH.exists() else None

    def stub_record_one(csdm, cfg, demo, steamid, player_name,
                        out_dir, final_name, log, need_keys, on_phase=None):
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        # 永远留一个「分段」垃圾，用来验证成功后被清理
        (out / "sequence-1-tick-1-to-100.avi").write_bytes(b"seg")
        behavior = BEHAVIOR.get(Path(demo).name, "ok")
        if behavior == "fail":
            (out / "sequence-1-tick-1-to-100.avi").unlink()
            return "fail", "模拟：本场无击杀"
        if behavior == "partial":
            # 真实语义：叠键盘/合并失败 → **没有成品**，分段留在 _parts 里
            return "partial", "模拟：仅部分成功（已保留无键盘分段）"
        (out / final_name).write_bytes(b"x" * 32)          # 产出成品
        if on_phase:
            on_phase("模拟完成")
        return "ok", ""

    try:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            demo_dir = tdp / "demos"
            demo_dir.mkdir()
            for nm in ["b.dem", "a.dem", "d.dem", "c.dem"]:   # 故意乱序落盘
                (demo_dir / nm).write_bytes(b"")
            out_root = tdp / "out"
            out_root.mkdir()
            # 预置一个成品：排序后 a,b,c,d → b=02，所以 'donk-02.mp4' 应被跳过
            (out_root / "donk-02.mp4").write_bytes(b"preexisting")

            wb.record_one = stub_record_one
            wb.resolve_player = lambda demo, spec: ("76561198000000001", "donk")
            wb.preflight = lambda: None
            wb.wait_cs2_exit = lambda *a, **k: True

            c = wb.app.test_client()
            r = c.post("/api/batch", json={
                "batch_dir": str(demo_dir), "batch_player": "donk",
                "out_name": "donk", "out_dir": str(out_root),
                "mode": "keys", "preset": "2k-high",
            })
            j = r.get_json() or {}
            check("POST /api/batch → 200", r.status_code == 200, str(j.get("error")))
            check("编号预览 4 条", j.get("names") ==
                  ["donk-01.mp4", "donk-02.mp4", "donk-03.mp4", "donk-04.mp4"],
                  str(j.get("names")))

            tid = j.get("task_id")
            t = None
            for _ in range(120):                      # 最多等 12s（桩很快）
                t = c.get(f"/api/task/{tid}").get_json() or {}
                if t.get("status") != "running":
                    break
                time.sleep(0.1)

            print("\n=== 循环结果 ===")
            b = t.get("batch") or {}
            check("kind=batch", t.get("kind") == "batch")
            check("status=done", t.get("status") == "done", str(t.get("status")))
            check("total=4 / done=4", b.get("total") == 4 and b.get("done") == 4,
                  f"total={b.get('total')} done={b.get('done')}")
            check("ok=1（a）", b.get("ok") == 1, str(b.get("ok")))
            check("partial=1（d）", b.get("partial") == 1, str(b.get("partial")))
            check("skipped=1（b，成品已存在）", b.get("skipped") == 1, str(b.get("skipped")))
            check("failed=1（c）", b.get("failed") == 1, str(b.get("failed")))

            print("\n=== 逐条结果（按执行顺序）===")
            res = b.get("results") or []
            got = [(x["index"], x["demo"], x["status"], x.get("output", "")) for x in res]
            for g in got:
                print("   ", g)
            check("结果条数=4", len(res) == 4, str(len(res)))
            # 升序：a=01(ok) b=02(skip) c=03(fail) d=04(partial，无成品→output 空)
            check("[01]=a ok", got[0] == (1, "a.dem", "ok", "donk-01.mp4"), str(got[0]))
            check("[02]=b skip", got[1][2] == "skip" and got[1][1] == "b.dem", str(got[1]))
            check("[03]=c fail", got[2] == (3, "c.dem", "fail", ""), str(got[2]))
            check("[04]=d partial 且不虚报文件名",
                  got[3] == (4, "d.dem", "partial", ""), str(got[3]))

            print("\n=== 产出与清理 ===")
            produced = sorted(p.name for p in out_root.glob("donk-*.mp4"))
            check("成品平铺在输出根目录（partial 无成品）",
                  produced == ["donk-01.mp4", "donk-02.mp4"], str(produced))
            check("跳过的 02 未被覆盖（内容仍是预置的）",
                  (out_root / "donk-02.mp4").read_bytes() == b"preexisting")
            parts = out_root / "_parts"
            left = sorted(p.name for p in parts.iterdir()) if parts.exists() else []
            check("成功的 01 分段已清理", "donk-01" not in left, str(left))
            check("失败的 03 / 部分的 04 保留现场",
                  "donk-03" in left and "donk-04" in left, str(left))

            vs = [Path(v).name for v in (t.get("videos") or [])]
            check("videos 只含真正落盘的 ok 成品（partial 无成品→不入列）",
                  vs == ["donk-01.mp4"], str(vs))

            log_txt = "\n".join(t.get("log") or [])
            check("日志含批量汇总", "批量汇总" in log_txt and "跳过" in log_txt)
    finally:
        (wb.record_one, wb.resolve_player, wb.preflight, wb.wait_cs2_exit) = real
        if bak is None:
            wb.PREFS_PATH.unlink(missing_ok=True)
        else:
            wb.PREFS_PATH.write_text(bak, encoding="utf-8")

    print("\n" + ("全部通过 ✓" if not FAIL else f"失败 {len(FAIL)} 项：{FAIL}"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
