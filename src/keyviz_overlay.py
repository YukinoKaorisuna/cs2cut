# -*- coding: utf-8 -*-
"""
CS2 键鼠悬浮窗 —— 叠在游戏 demo 画面上，跟着 demo 进度同步亮键。

═══════════════════════════════════════════════════════════════════════
【同步原理（复用 Gtupxx/CS2DemoVisualize 已验证的机制，非我自创）】
═══════════════════════════════════════════════════════════════════════

三段式，靠的是 CS2 的 console.log：

  1. 游戏加 `-condebug` 启动项 → 游戏实时把控制台输出写到 console.log。
     当你在游戏里「跳转」demo（点进度条 / 下一回合 / demo_gototick），
     日志会出现：
         [Demo] Demo Skipping: skipping to demo tick 12345
         [Demo] Demo Skipping: skipping forward to demo tick 12345

  2. 本工具 tail 这个文件，正则抓出 tick 数字。

  3. 抓到 tick → 把按键数据定位到该 tick，暂停等 F9；
     按 F9 后「游戏 demo」和「悬浮窗」从同一起点、同一 tick 速率播放。

  因为不是闭环实时读 tick，而是「跳转时校准一次 + 之后同速开环播放」，
  长时间播放会有累积漂移；漂移大了再跳转一次即可重新对齐（原作者实测
  30 分钟 demo 漂移小到可忽略）。

【和 Gtupxx 的区别】
  - 它手写 buttons 位掩码；本工具用 demoparser2 官方布尔字段。
  - 它的「静步」用 IN_SPEED 位（不准）；本工具读 is_walking 状态字段。
  - 它的「蹲/跳」用掩码位；本工具读 duck_amount / is_airborne 状态字段。

【使用前提】
  1. 游戏要「无边框窗口化」，悬浮窗才能叠在画面上（全屏独占会盖住）。
     游戏内 设置→视频→显示模式→无边框窗口，或启动项加 -windowed -noborder。
  2. 游戏加 -condebug 启动项（Steam 右键 CS2→属性→启动项→加 -condebug）。
  3. 先用 input_extractor.py 生成按键 JSON（60fps、tick 对齐）。

【用法】
  python src/keyviz_overlay.py <input.json>
       [--console-log 路径] [--scale 1.0] [--selftest]

【热键】（全局，游戏里也生效）
  F9        播放 / 暂停（游戏里 F9 已绑 demo_pause/demo_resume，见下方 cfg）
  [          0.5x 慢放
  ]          1.0x 正常
  \\         2.0x 快放
  Esc        退出悬浮窗

  游戏里 F9 绑定，需要让游戏 exec 一份 cfg（内容见文件末尾注释），
  或用控制台手动输入：
      bind F9 "toggle demo_pause"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONSOLE_LOG = (
    r"C:\Program Files (x86)\Steam\steamapps\common"
    r"\Counter-Strike Global Offensive\game\csgo\console.log"
)

# console.log 里跳转 tick 的正则（两种格式都抓）
SKIP_RE = [
    re.compile(r"\[Demo\] Demo Skipping: skipping to demo tick (\d+)"),
    re.compile(r"\[Demo\] Demo Skipping: skipping forward to demo tick (\d+)"),
]

# 共享状态（跨线程）
pause_flag = threading.Event()          # set = 暂停
speed = [1.0]                           # 倍速
skip_tick = [None]                      # 待处理的跳转 tick
skip_lock = threading.Lock()


def tick_to_frame(tick: int, fps: int, tickrate: int) -> int:
    """demo tick -> 60fps 帧号（和 input_extractor 同一换算）。"""
    return int(round(tick * fps / tickrate))


def fmt_s(sec: float) -> str:
    m = int(sec // 60)
    return f"{m}:{sec - m * 60:04.1f}"


class Overlay:
    """Tkinter 悬浮窗。"""

    # 键位布局（每键 x, y, w, h），坐标在 render 里乘 scale
    KEYS = [
        ("SHIFT", 10, 12, 78, 52),
        ("W", 106, 12, 54, 52),
        ("E", 166, 12, 54, 52),
        ("R", 226, 12, 54, 52),
        ("A", 76, 70, 54, 52),
        ("S", 136, 70, 54, 52),
        ("D", 196, 70, 54, 52),
        ("CTRL", 10, 128, 78, 52),
        ("SPACE", 106, 128, 176, 52),
        ("M1", 300, 8, 56, 54),
        ("M2", 300, 70, 56, 54),
    ]
    HINTS = {
        "SHIFT": "静步", "W": "前", "E": "使用", "R": "换弹",
        "A": "左", "S": "后", "D": "右", "CTRL": "蹲", "SPACE": "跳",
        "M1": "左键", "M2": "右键",
    }
    # 数据字段名 -> 键
    FIELD = {
        "W": "W", "A": "A", "S": "S", "D": "D",
        "WALK": "SHIFT", "DUCK": "CTRL", "JUMP": "SPACE",
        "USE": "E", "RELOAD": "R",
        "FIRE": "M1", "RCLICK": "M2",
    }
    # 是否用琥珀色（静步/蹲/跳/右键），其余用橙色
    AMBER = {"SHIFT", "CTRL", "SPACE", "M2"}

    C_BG = "#17171a"
    C_OFF = "#333338"
    C_OFF_TXT = "#9a9aa0"
    C_ON = "#d85a30"        # 橙（WASD/左键/E/R）
    C_ON2 = "#ba7517"       # 琥珀（静步/蹲/跳/右键）
    C_ON_TXT = "#ffffff"

    def __init__(self, data: dict, scale: float = 1.0):
        import tkinter as tk

        self.data = data
        self.frames = data["frames"]
        self.meta = data["meta"]
        self.fps = self.meta["fps"]
        self.tickrate = self.meta["tickrate"]
        self.total = self.meta["frames"]
        self.s = scale
        self.cur = 0

        self.root = tk.Tk()
        self.root.title("CS2 键鼠")
        self.root.overrideredirect(True)                    # 无边框
        self.root.wm_attributes("-topmost", True)           # 置顶
        self.root.wm_attributes("-alpha", 0.93)             # 半透明
        self.root.configure(bg=self.C_BG)

        w = int(366 * scale)
        h = int(196 * scale)
        self.canvas = tk.Canvas(self.root, width=w, height=h,
                                bg=self.C_BG, highlightthickness=0, bd=0)
        self.canvas.pack()

        self._build_static()
        self._bind_drag()

        # 初始暂停，等第一次跳转
        pause_flag.set()

    def _k(self, x, y, w, h):
        return (x * self.s, y * self.s, (x + w) * self.s, (y + h) * self.s)

    def _build_static(self):
        c = self.canvas
        for name, x, y, w, h in self.KEYS:
            bx = self._k(x, y, w, h)
            self.canvas.create_rectangle(*bx, fill=self.C_OFF, outline="#000000",
                                         width=1, tags=(name, "key"))
            label = name if name not in ("M1", "M2") else ("左键" if name == "M1" else "右键")
            self.canvas.create_text((bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2,
                                    text=label, fill=self.C_OFF_TXT,
                                    font=("Microsoft YaHei", int(11 * self.s)),
                                    tags=(name + "_txt", "key"))
        # 底部信息栏
        self.info = c.create_text(
            8 * self.s, int(188 * self.s), anchor="w",
            fill="#8a8a90", font=("Consolas", int(9 * self.s)), text="等待跳转…")

    def _bind_drag(self):
        self._off = None

        def start(e):
            self._off = (e.x, e.y)

        def move(e):
            if self._off:
                self.root.geometry(f"+{e.x_root - self._off[0]}+{e.y_root - self._off[1]}")

        for w in (self.root, self.canvas):
            w.bind("<Button-1>", start)
            w.bind("<B1-Motion>", move)
            w.bind("<Button-3>", lambda e: self.close())

    def g(self, field, i):
        a = self.frames[field]
        return a[i] if i < len(a) else 0

    def render(self):
        c = self.canvas
        for name, *_ in self.KEYS:
            field = [f for f, k in self.FIELD.items() if k == name][0]
            on = self.g(field, self.cur)
            color = (self.C_ON2 if name in self.AMBER else self.C_ON) if on else self.C_OFF
            c.itemconfig(name, fill=color)
            c.itemconfig(name + "_txt", fill=self.C_ON_TXT if on else self.C_OFF_TXT)

        r = self.g("ROUND", self.cur) + 1
        t_sec = self.cur / self.fps
        tick = int(self.cur * self.tickrate / self.fps)
        st = "▶" if not pause_flag.is_set() else "⏸"
        self.canvas.itemconfig(self.info, text=(
            f"{st} 回合{r}  tick{tick}  {fmt_s(t_sec)}  {speed[0]:.1f}x"))

    def step(self):
        # 处理跳转
        with skip_lock:
            if skip_tick[0] is not None:
                f = tick_to_frame(skip_tick[0], self.fps, self.tickrate)
                self.cur = min(f, self.total - 1)
                skip_tick[0] = None
                pause_flag.set()          # 跳转后暂停，等 F9
                self.render()
                self.root.after(16, self.step)
                return
        # 播放前进
        if not pause_flag.is_set():
            self.cur += int(round(speed[0]))
            if self.cur >= self.total:
                self.cur = 0
            self.render()
        self.root.after(16, self.step)

    def refresh_topmost(self):
        try:
            self.root.wm_attributes("-topmost", True)
        except Exception:
            pass
        self.root.after(2000, self.refresh_topmost)

    def close(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    def run(self):
        self.root.after(16, self.step)
        self.root.after(2000, self.refresh_topmost)
        self.root.mainloop()


def tail_console_log(path: str):
    """监听 console.log 的跳转 tick。"""
    if not os.path.exists(path):
        print(f"[!] 未找到 console.log：{path}")
        print("    请确认游戏已加 -condebug 启动项，并已启动游戏。")
        return
    print(f"[监听] console.log: {path}")
    print("[提示] 请先在游戏里跳转一次 demo（点进度条 / 下一回合 / demo_gototick）")
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(0, 2)                 # 跳到文件末尾，只读新增
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                for pat in SKIP_RE:
                    m = pat.search(line)
                    if m:
                        t = int(m.group(1))
                        print(f"[同步] 跳到 tick {t}")
                        with skip_lock:
                            skip_tick[0] = t
                        break
    except Exception as e:
        print(f"[!] 日志读取异常：{e}")


def setup_hotkeys():
    try:
        import keyboard

        def toggle():
            if pause_flag.is_set():
                pause_flag.clear()
                print("[热键] 播放")
            else:
                pause_flag.set()
                print("[热键] 暂停")

        keyboard.add_hotkey("f9", toggle)
        keyboard.add_hotkey("[", lambda: (speed.__setitem__(0, 0.5),
                                          print("[热键] 0.5x")))
        keyboard.add_hotkey("]", lambda: (speed.__setitem__(0, 1.0),
                                          print("[热键] 1.0x")))
        keyboard.add_hotkey("backslash", lambda: (speed.__setitem__(0, 2.0),
                                                  print("[热键] 2.0x")))
        print("[热键] F9=播放暂停  [=0.5x  ]=1x  \\=2x  Esc=退出")
    except Exception as e:
        print(f"[!] 热键初始化失败（若热键不生效，请用管理员身份运行）：{e}")


def selftest(json_path: str):
    """不弹窗，只验证数据加载和 tick 换算。"""
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    meta, frames = data["meta"], data["frames"]
    print(f"[数据] {meta['player']} @ {meta['map']}  "
          f"{meta['duration_s']}s  {meta['frames']}帧 @{meta['fps']}fps")
    print(f"[数据] 字段: {list(frames.keys())}")
    # 抽查几个 tick 的换算
    for t in (0, 3200, 64000, 128000):
        f = tick_to_frame(t, meta["fps"], meta["tickrate"])
        if f < meta["frames"]:
            r = frames["ROUND"][f] + 1
            keys = [k for k, a in frames.items()
                    if k in ("W", "A", "S", "D", "WALK", "DUCK", "JUMP", "FIRE")
                    and f < len(a) and a[f]]
            print(f"  tick {t:>6} -> 帧 {f:>6}  回合{r}  按下={keys}")
    print("[OK] selftest 通过")


def main():
    ap = argparse.ArgumentParser(description="CS2 键鼠悬浮窗")
    ap.add_argument("json", help="input_extractor.py 产出的按键 JSON")
    ap.add_argument("--console-log", default=DEFAULT_CONSOLE_LOG)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--selftest", action="store_true",
                    help="只验证数据，不弹窗")
    args = ap.parse_args()

    if args.selftest:
        selftest(args.json)
        return

    data = json.loads(Path(args.json).read_text(encoding="utf-8"))

    # 启动同步线程 + 热键
    threading.Thread(target=tail_console_log, args=(args.console_log,),
                     daemon=True).start()
    setup_hotkeys()

    ov = Overlay(data, args.scale)
    ov.run()


if __name__ == "__main__":
    main()


# ─────────────────────────────────────────────────────────────────────
# 附：游戏内 F9 绑定（让 F9 同时控制 demo 播放/暂停）
# 把下面两行存成 game_config.cfg 放游戏 csgo 目录，进游戏控制台 exec 一次，
# 或直接控制台输入：
#   bind F9 "toggle demo_pause"
# 这样 F9 就同时控制游戏 demo 和本悬浮窗的播放/暂停，实现同步。
# ─────────────────────────────────────────────────────────────────────
