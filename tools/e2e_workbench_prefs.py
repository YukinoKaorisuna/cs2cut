# -*- coding: utf-8 -*-
"""工作台「偏好记忆 / 输出自定义」的回归测试（不启动服务器、不开浏览器、不录制）。

用 Flask test_client 直接打接口，覆盖：
  1. /api/config 是否带 version / default_out_dir / prefs
  2. POST /api/prefs 增量保存 + GET 读回
  3. 白名单：垃圾键不入库
  4. sanitize_out_name：空值 / 非法字符 / 路径穿越 / 自动补 .mp4 / 以 sequence 开头
  5. 首页能正常返回

★ 会临时改写 recorder/prefs.json —— 跑完自动还原，不影响你的真实记忆。
运行：python tools/e2e_workbench_prefs.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "recorder"))

FAIL = []


def check(name: str, cond: bool, extra: str = ""):
    print(f"  [{'✓' if cond else '✗'}] {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAIL.append(name)


def main() -> int:
    import app as wb  # noqa: E402

    # 备份真实 prefs
    bak = None
    if wb.PREFS_PATH.exists():
        bak = wb.PREFS_PATH.read_text(encoding="utf-8")
    try:
        print("=== 1) 输出名规范化 sanitize_out_name ===")
        cases = [
            ("", "final.mp4", "final.mp4", "空值 → 用默认名"),
            ("   ", "final.mp4", "final.mp4", "纯空格 → 默认名"),
            ("我的集锦", "x.mp4", "我的集锦.mp4", "无扩展名 → 自动补 .mp4"),
            ("clip.MOV", "x.mp4", "clip.MOV", "已有扩展名 → 不动"),
            # 注意：先剥掉目录成分（Path().name），再替换非法字符 ——
            # 所以 'a/b\c:d*e?f' 会先取最后一段 'c:d*e?f' 再净化成 'c_d_e_f.mp4'。
            ("a/b\\c:d*e?f", "x.mp4", "c_d_e_f.mp4", "目录先剥离，非法字符再换下划线"),
            ("..\\..\\evil.mp4", "x.mp4", "evil.mp4", "路径穿越 → 只剩文件名"),
            ("  ..  ", "x.mp4", "x.mp4", "纯点点 → 默认名"),
        ]
        for raw, default, want, desc in cases:
            got = wb.sanitize_out_name(raw, default)
            check(desc, got == want, f"{raw!r} -> {got!r}（期望 {want!r}）")

        print("\n=== 2) 偏好白名单 ===")
        wb.save_prefs({"demo_dir": "E:/x", "out_name": "A.mp4", "hack_me": 1, "__proto__": {}})
        p = wb.load_prefs()
        check("白名单外的键被丢弃", "hack_me" not in p and "__proto__" not in p, str(sorted(p)))
        check("合法键已保存", p.get("demo_dir") == "E:/x" and p.get("out_name") == "A.mp4")

        print("\n=== 3) 增量合并（不覆盖未提交的键）===")
        wb.save_prefs({"preset": "4k-high", "fov": 100})
        p = wb.load_prefs()
        check("旧键保留", p.get("demo_dir") == "E:/x", str(p))
        check("新键写入", p.get("preset") == "4k-high" and p.get("fov") == 100)

        print("\n=== 4) HTTP 接口 ===")
        c = wb.app.test_client()
        r = c.get("/")
        check("GET / 返回页面", r.status_code == 200 and b"<html" in r.data.lower())

        r = c.get("/api/config")
        j = r.get_json() or {}
        check("GET /api/config 200", r.status_code == 200)
        check("config 带 version", j.get("version") == wb.APP_VERSION, str(j.get("version")))
        check("config 带 default_out_dir", bool(j.get("default_out_dir")), str(j.get("default_out_dir")))
        check("config 带 prefs", isinstance(j.get("prefs"), dict) and j["prefs"].get("preset") == "4k-high",
              str(j.get("prefs")))

        r = c.post("/api/prefs", json={"demo_dir": "E:/cs2cut/demos", "out_dir": "E:/out",
                                       "out_name": "highlight", "mode": "keys"})
        check("POST /api/prefs 200", r.status_code == 200)
        j = r.get_json() or {}
        check("POST 返回值含新值", j.get("demo_dir") == "E:/cs2cut/demos" and j.get("out_name") == "highlight")

        j = c.get("/api/prefs").get_json() or {}
        check("GET /api/prefs 读回", j.get("out_dir") == "E:/out" and j.get("mode") == "keys",
              str(j))

        print("\n=== 5) 损坏文件容错 ===")
        wb.PREFS_PATH.write_text("{ 这不是 json", encoding="utf-8")
        check("坏 prefs.json 不抛异常", wb.load_prefs() == {})

        print("\n=== 6) 落盘位置 ===")
        check("prefs.json 在 recorder/ 下", wb.PREFS_PATH.parent.name == "recorder",
              str(wb.PREFS_PATH))
    finally:
        # 还原真实 prefs
        if bak is None:
            wb.PREFS_PATH.unlink(missing_ok=True)
        else:
            wb.PREFS_PATH.write_text(bak, encoding="utf-8")
        print("\n（已还原原 prefs.json）")

    print("\n" + ("全部通过 ✓" if not FAIL else f"失败 {len(FAIL)} 项：{FAIL}"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
