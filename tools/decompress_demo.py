# -*- coding: utf-8 -*-
"""把 FACEIT 下载的 .dem.zst 解压成 .dem 并校验，丢进 CSDM 的 demo 目录。

用法：
  # 默认解压到 E:\\cs2cut\\demos，并做完整性校验
  python tools/decompress_demo.py "C:\\Users\\xxx\\Downloads\\1-xxxx-1-1.dem.zst"

  # 指定输出目录（也可直接指定 CS2 的 game\\csgo）
  python tools/decompress_demo.py xxx.dem.zst --out "<CS2根>\\game\\csgo"

  # 保留源文件（默认保留；加 --delete-source 才删）
  python tools/decompress_demo.py xxx.dem.zst --delete-source

为什么要有这个脚本（踩过的坑）：
  1) .dem.zst 的 stem 已经是 "xxx.dem"，自己拼 stem + ".dem" 会得到
     "xxx.dem.dem"（文件名难看，playdemo 也认不准）。
     本脚本用 src/demo_parser.decompress_demo()，它内部处理双扩展名。
  2) 解压后必须校验文件头 b"PBDEMS2"，否则可能是个坏包/HTML 错误页。
  3) zstandard 已装在 managed venv，无需另装 zstd CLI。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from demo_parser import decompress_demo  # noqa: E402

ZSTD_MAGIC = bytes.fromhex("28b52ffd")
DEMO_MAGIC = b"PBDEMS2"


def main() -> None:
    ap = argparse.ArgumentParser(description="解压 .dem.zst 到 CSDM demo 目录并校验")
    ap.add_argument("src", help="要解压的 .dem.zst / .dem.gz 文件")
    ap.add_argument("--out", default=str(ROOT / "demos"),
                    help="输出目录（默认 E:\\cs2cut\\demos，即 CSDM 默认目录之一）")
    ap.add_argument("--delete-source", action="store_true", help="解压后删除源压缩包")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.exists():
        print(f"[X] 源文件不存在: {src}")
        sys.exit(1)

    with open(src, "rb") as fh:
        head = fh.read(4)
    print(f"源: {src.name}  {src.stat().st_size:,} B")
    if src.suffix.lower() in (".zst", ".zstd") and head != ZSTD_MAGIC:
        print(f"[!] 魔数不是 zstd（{head.hex()}）—— 可能下载到错误页，仍尝试解压")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # decompress_demo 输出到「源文件同目录」，故先解压再搬到 --out
    dem = decompress_demo(src, delete_original=args.delete_source)
    target = out_dir / dem.name
    if dem.resolve() != target.resolve():
        if target.exists():
            target.unlink()
        dem.replace(target)

    size = target.stat().st_size
    with open(target, "rb") as fh:
        dh = fh.read(7)
    ok = dh.startswith(DEMO_MAGIC)
    print(f"  → {target}")
    print(f"    {size:,} B ({size / 1024 / 1024:.1f} MB)  头={dh!r}  {'✓ 有效 demo' if ok else '✗ 头异常'}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
