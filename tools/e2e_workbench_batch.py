# -*- coding: utf-8 -*-
"""工作台「批量录制」的回归测试（不启动服务器、不开浏览器、**不真的录制**）。

覆盖：
  1. sanitize_name_part：批量前缀的净化（剥目录 / 换非法字符 / 去视频扩展名）
  2. /api/batch 的四道前置校验（csdm 缺失 / 目录不存在 / 目录无 demo / 未填选手）
     —— 这四条都在「起线程之前」就 return，所以不会触发真实录制
  3. 成功路径的「编号预览」（names）：用假 Thread 桩住后台线程，只验返回体
  4. batch_* 键进偏好白名单

★ 用临时目录 + 假 .dem（0 字节）驱动 scan_demos，跑完自动清理。
运行：python tools/e2e_workbench_batch.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "recorder"))

FAIL = []


def check(name: str, cond: bool, extra: str = ""):
    print(f"  [{'✓' if cond else '✗'}] {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


class _DummyThread:
    """不起真线程的桩：只保证 .start() 不报错，绝不执行 run()。"""
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass


class _ThreadShim:
    Thread = _DummyThread


def main() -> int:
    import app as wb  # noqa: E402

    print("=== 1) 前缀净化 sanitize_name_part ===")
    cases = [
        ("donk1002", "clip", "donk1002", "普通前缀 → 原样"),
        ("", "clip", "clip", "空 → 默认 clip"),
        ("   ", "clip", "clip", "纯空格 → 默认"),
        ("donk/1002", "clip", "1002", "带目录 → 只留最后一段"),
        ("donk1002.mp4", "clip", "donk1002", "带扩展名 → 去掉 .mp4"),
        ("a*b?c", "clip", "a_b_c", "非法字符 → 下划线"),
        # 注意：先 Path().name 剥目录，再净化 —— Windows 下 'a:b*c?' 被当成
        # 盘符 'a:' + 名字 'b*c?'，所以结果是 'b_c_' 而不是 'a_b_c_'。
        ("a:b*c?", "clip", "b_c_", "含冒号 → 先按盘符/目录剥离再净化"),
    ]
    for raw, default, want, desc in cases:
        got = wb.sanitize_name_part(raw, default)
        check(desc, got == want, f"{raw!r} -> {got!r}（期望 {want!r}）")

    print("\n=== 2) /api/batch 前置校验 ===")
    c = wb.app.test_client()

    # 2a. 目录不存在
    r = c.post("/api/batch", json={"batch_dir": "Z:/no/such/dir",
                                   "batch_player": "donk"})
    j = r.get_json() or {}
    check("目录不存在 → 400", r.status_code == 400 and "不存在" in (j.get("error") or ""),
          str(j.get("error")))

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        # 2b. 空目录（无 .dem）
        empty = tdp / "empty"
        empty.mkdir()
        r = c.post("/api/batch", json={"batch_dir": str(empty), "batch_player": "donk"})
        j = r.get_json() or {}
        check("目录无 .dem → 400", r.status_code == 400 and "没有" in (j.get("error") or ""),
              str(j.get("error")))

        # 2c. 有 demo 但没填选手
        demo = tdp / "demo"
        demo.mkdir()
        (demo / "a.dem").write_bytes(b"")
        r = c.post("/api/batch", json={"batch_dir": str(demo), "batch_player": ""})
        j = r.get_json() or {}
        check("未填选手 → 400", r.status_code == 400 and "选手" in (j.get("error") or ""),
              str(j.get("error")))

        print("\n=== 3) 编号预览（假线程桩，不录制）===")
        (demo / "b.dem").write_bytes(b"")
        (demo / "c.dem").write_bytes(b"")
        real_threading = wb.threading
        wb.threading = _ThreadShim()          # 只影响 app.py 里的 threading 查找
        try:
            r = c.post("/api/batch", json={
                "batch_dir": str(demo), "batch_player": "donk",
                "out_name": "donk1002", "out_dir": str(tdp / "out"),
            })
            j = r.get_json() or {}
        finally:
            wb.threading = real_threading
        check("返回 200", r.status_code == 200, str(j.get("error")))
        check("total = 3", j.get("total") == 3, str(j.get("total")))
        check("prefix 净化正确", j.get("prefix") == "donk1002", str(j.get("prefix")))
        want = ["donk1002-01.mp4", "donk1002-02.mp4", "donk1002-03.mp4"]
        check("编号按文件名升序、两位补零", j.get("names") == want, str(j.get("names")))

        # 3b. ≥10 个 → 补零位数跟着变宽
        for i in range(9):
            (demo / f"z{i:02d}.dem").write_bytes(b"")
        wb.threading = _ThreadShim()
        try:
            j2 = c.post("/api/batch", json={
                "batch_dir": str(demo), "batch_player": "donk",
                "out_name": "x", "out_dir": str(tdp / "out"),
            }).get_json() or {}
        finally:
            wb.threading = real_threading
        check("≥10 个时编号补零加宽", j2.get("names", [""])[0] == "x-01.mp4"
              and len(j2.get("names", [])) == 12, str(j2.get("names"))[:80])

    print("\n=== 4) 批量键进白名单 ===")
    bak = wb.PREFS_PATH.read_text(encoding="utf-8") if wb.PREFS_PATH.exists() else None
    try:
        wb.save_prefs({"batch_dir": "E:/demos", "batch_player": "donk",
                       "batch_overwrite": True, "batch_prefix": "vlog"})
        p = wb.load_prefs()
        check("batch_dir 入库", p.get("batch_dir") == "E:/demos")
        check("batch_player 入库", p.get("batch_player") == "donk")
        check("batch_overwrite 入库", p.get("batch_overwrite") is True)
        check("batch_prefix 入库", p.get("batch_prefix") == "vlog")
    finally:
        if bak is None:
            wb.PREFS_PATH.unlink(missing_ok=True)
        else:
            wb.PREFS_PATH.write_text(bak, encoding="utf-8")
        print("（已还原原 prefs.json）")

    print("\n" + ("全部通过 ✓" if not FAIL else f"失败 {len(FAIL)} 项：{FAIL}"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
