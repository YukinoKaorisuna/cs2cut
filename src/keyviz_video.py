# -*- coding: utf-8 -*-
"""
键盘可视化视频导出 —— 把选手的键鼠操作渲染成带透明通道的按键叠加视频。

产物：一个背景透明（或纯色）的 MP4 / MOV，时间是 1:1 对齐 demo。
      可以叠加到录屏上，也可以单独作为按键演示。

实现要点（踩过的坑写在这里）：
  1. 不用 opencv —— 用 PIL 逐帧画，直接 pipe 给 ffmpeg 编码。
     省一个重依赖，而且 PIL 画圆角矩形/文字比 cv2 好写。
  2. 帧率固定 60，数据侧已经重采样好（见 input_extractor.py），
     所以视频时长和游戏时间严格 1:1，不会漂移。
  3. 透明背景用 PNG 序列 pipe 给 ffmpeg 的 qtrle（QuickTime RLE）
     或 vp9；如果用 libx264 则透明会变黑 —— 这里提供两种输出：
        --bg none  → .mov (qtrle) 带 alpha，可叠加
        --bg dark  → .mp4 (libx264) 深色背景，直接看

键盘布局（仿 CS2 默认）：
      Q  W  E  R
      A  S  D
         SPACE
  外加：左/右键、Shift(静步)、Ctrl(蹲)

用法：
    python src/keyviz_video.py <input.json> [-o out.mp4]
        [--bg dark|none] [--round 3] [--from-sec 10 --to-sec 20]
        [--scale 1.0] [--fps 60]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = ROOT / "tools" / "ffmpeg-9.0.1-essentials_build" / "bin" / "ffmpeg.exe"
OUT_DIR = ROOT / "output" / "videos"

# ───── 视觉参数 ─────
PAD = 22
KEY_W, KEY_H = 68, 68
GAP = 7
FONT_SZ = 22
FONT_SZ_S = 15

C_BG_DARK = (22, 22, 24)
C_KEY_OFF = (52, 52, 56)
C_KEY_ON = (216, 90, 48)          # 品牌橙
C_KEY_ON2 = (153, 60, 29)
C_TXT_OFF = (150, 150, 155)
C_TXT_ON = (255, 255, 255)
C_TXT_HINT = (110, 110, 116)


def find_font(size: int):
    """找一个能显示中英文的字体。"""
    cands = [
        r"C:\Windows\Fonts\msyhbd.ttc",     # 微软雅黑 Bold
        r"C:\Windows\Fonts\msyh.ttc",       # 微软雅黑
        r"C:\Windows\Fonts\simhei.ttf",     # 黑体
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for c in cands:
        if Path(c).exists():
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                continue
    return ImageFont.load_default()


class Layout:
    """键盘布局：算出每块的位置，画的时候按名字取。"""

    def __init__(self, scale: float = 1.0):
        self.s = scale
        self.kw = int(KEY_W * scale)
        self.kh = int(KEY_H * scale)
        self.gap = int(GAP * scale)
        self.pad = int(PAD * scale)
        self.f_keys = find_font(int(FONT_SZ * scale))
        self.f_small = find_font(int(FONT_SZ_S * scale))
        self.boxes: dict[str, tuple[int, int, int, int]] = {}
        self._build()

    def _build(self):
        kw, kh, g, p = self.kw, self.kh, self.gap, self.pad
        col = kw + g

        # 鼠标（上半身形状，W 左边）
        mw, mh = int(74 * self.s), int(62 * self.s)

        # W 行、A 行坐标
        wy = p
        ax = p + col
        ay = wy + col

        # W 在 S 正上方，R 在 W 右边
        self.boxes["W"] = (ax + col, wy, ax + col + kw, wy + kh)
        self.boxes["R"] = (ax + 2 * col, wy, ax + 2 * col + kw, wy + kh)

        # 鼠标（W 左边，垂直居中于 W）
        mx = ax + col - mw - g
        my = wy + (kh - mh) // 2
        self.mouse = (mx, my, mx + mw, my + mh)
        self.m_l = (mx, my, mx + mw // 2, my + mh)
        self.m_r = (mx + mw // 2, my, mx + mw, my + mh)

        # A S D
        self.boxes["A"] = (ax, ay, ax + kw, ay + kh)
        self.boxes["S"] = (ax + col, ay, ax + col + kw, ay + kh)
        self.boxes["D"] = (ax + 2 * col, ay, ax + 2 * col + kw, ay + kh)

        # Shift 贴 A 左，Ctrl 在 Shift 下
        self.boxes["SHIFT"] = (ax - col, ay, ax - col + kw, ay + kh)
        self.boxes["CTRL"] = (ax - col, ay + col, ax - col + kw, ay + col + kh)

        # Space 在 A/S/D 下
        self.boxes["SPACE"] = (ax, ay + col, ax + 2 * col - g, ay + col + kh)

        # 画布尺寸
        self.width = (ax + 2 * col + kw + p) // 2 * 2
        self.height = (ay + 2 * col + kh + p) // 2 * 2

    def rr(self, dr, box, rad, fill, outline=None, wpx=1):
        x0, y0, x1, y1 = box
        dr.rounded_rectangle([x0, y0, x1, y1], radius=rad,
                             fill=fill, outline=outline, width=wpx)

    def ctext(self, dr, box, text, font, fill):
        x0, y0, x1, y1 = box
        bb = dr.textbbox((0, 0), text, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        dr.text((x0 + (x1 - x0 - tw) / 2 - bb[0],
                 y0 + (y1 - y0 - th) / 2 - bb[1]), text, font=font, fill=fill)


KEY_HINT = {
    "W": "前", "R": "换弹",
    "A": "左", "S": "后", "D": "右",
    "SPACE": "跳", "SHIFT": "静步", "CTRL": "蹲",
}
KEY_LABEL = {
    "SHIFT": "Shift", "CTRL": "Ctrl", "SPACE": "Space",
}


def render_frame(L: Layout, fr: dict, t_sec: float, bg, info: str):
    """画一帧。fr 是 {key: 0/1}，bg 为 None 表示透明。"""
    im = Image.new("RGBA", (L.width, L.height), bg if bg else (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)

    def on(name):
        return bool(fr.get(name, 0))

    def draw_key(box, active, amber=False, label="", hint=""):
        x0, y0, x1, y1 = box
        rad = int(10 * L.s)
        # 底部厚度（键帽立体感）
        dr.rounded_rectangle([x0, y0 + int(3 * L.s), x1, y1 + int(3 * L.s)],
                             radius=rad, fill=(15, 15, 18))
        # 主体
        fill = (C_KEY_ON2 if amber else C_KEY_ON) if active else C_KEY_OFF
        edge = (C_KEY_ON2 if amber else C_KEY_ON2) if active else (66, 66, 74)
        dr.rounded_rectangle([x0, y0, x1, y1 - int(2 * L.s)], radius=rad,
                             fill=fill, outline=edge, width=max(1, int(1.2 * L.s)))
        # 文字
        tx = C_TXT_ON if active else C_TXT_OFF
        font = L.f_small if len(label) > 1 else L.f_keys
        if hint:
            L.ctext(dr, (x0, y0 + int(4 * L.s), x1, y0 + int(30 * L.s)),
                    label, font, tx)
            L.ctext(dr, (x0, y0 + int(30 * L.s), x1, y1 - int(9 * L.s)),
                    hint, L.f_small, (255, 255, 255) if active else C_TXT_HINT)
        else:
            L.ctext(dr, (x0, y0, x1, y1 - int(2 * L.s)), label, font, tx)

    # ── 按键 ──
    draw_key(L.boxes["R"], on("RELOAD"), False, "R", KEY_HINT.get("R", ""))
    draw_key(L.boxes["W"], on("W"), False, "W", KEY_HINT.get("W", ""))
    draw_key(L.boxes["A"], on("A"), False, "A", KEY_HINT.get("A", ""))
    draw_key(L.boxes["S"], on("S"), False, "S", KEY_HINT.get("S", ""))
    draw_key(L.boxes["D"], on("D"), False, "D", KEY_HINT.get("D", ""))
    draw_key(L.boxes["SHIFT"], on("WALK"), True, "Shift", KEY_HINT.get("SHIFT", ""))
    draw_key(L.boxes["CTRL"], on("DUCK"), True, "Ctrl", KEY_HINT.get("CTRL", ""))
    draw_key(L.boxes["SPACE"], on("JUMP"), True, "Space", KEY_HINT.get("SPACE", ""))

    # ── 鼠标（上半身轮廓）──
    mx0, my0, mx1, my1 = L.mouse
    l_on, r_on = on("FIRE"), on("RCLICK")
    lx0, ly0, lx1, ly1 = L.m_l
    rx0, ry0, rx1, ry1 = L.m_r
    rad = int(26 * L.s)
    # 鼠标壳
    dr.rounded_rectangle([mx0, my0, mx1, my1], radius=rad,
                         fill=C_KEY_OFF, outline=(66, 66, 74),
                         width=max(1, int(1.2 * L.s)))
    # 左键
    dr.rounded_rectangle([lx0, ly0, lx1, ly1], radius=rad,
                         fill=C_KEY_ON if l_on else (58, 58, 64))
    # 右键
    dr.rounded_rectangle([rx0, ry0, rx1, ry1], radius=rad,
                         fill=C_KEY_ON if r_on else (58, 58, 64))
    # 中间分隔线
    mid = (lx1 + rx0) // 2
    dr.line([mid, my0 + int(6 * L.s), mid, my1], fill=(40, 40, 46),
            width=max(1, int(1.5 * L.s)))

    # ── 右下角信息 ──
    if info:
        bb = dr.textbbox((0, 0), info, font=L.f_small)
        tw = bb[2] - bb[0]
        dr.text((L.width - L.pad - tw, L.height - L.pad - (bb[3] - bb[1])),
                info, font=L.f_small, fill=(120, 120, 126))

    return im


def main():
    ap = argparse.ArgumentParser(description="导出键盘可视化叠加视频")
    ap.add_argument("json", help="input_extractor.py 产出的 json")
    ap.add_argument("-o", "--out")
    ap.add_argument("--bg", choices=["dark", "none"], default="dark",
                    help="dark=mp4 深色底；none=mov 透明通道")
    ap.add_argument("--round", type=int, help="只导出某一回合")
    ap.add_argument("--from-sec", type=float, help="起始秒")
    ap.add_argument("--to-sec", type=float, help="结束秒")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--crf", type=int, default=18)
    args = ap.parse_args()

    src = Path(args.json)
    data = json.loads(src.read_text(encoding="utf-8"))
    M, F = data["meta"], data["frames"]
    fps = M["fps"]
    total = M["frames"]

    # 时间范围
    f0, f1 = 0, total
    if args.round is not None:
        hit = [r for r in data["rounds"] if r["round"] == args.round]
        if not hit:
            print(f"[X] 没有回合 {args.round}，可选 "
                  f"{[r['round'] for r in data['rounds']]}")
            return
        f0, f1 = hit[0]["f0"], hit[0]["f1"]
    if args.from_sec is not None:
        f0 = max(f0, int(args.from_sec * fps))
    if args.to_sec is not None:
        f1 = min(f1, int(args.to_sec * fps))
    f1 = min(f1, total)
    n = f1 - f0
    if n <= 0:
        print("[X] 时间范围为空")
        return

    L = Layout(args.scale)
    bg = C_BG_DARK + (255,) if args.bg == "dark" else None

    if args.out:
        out = Path(args.out)
    else:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        tag = f"_r{args.round}" if args.round is not None else ""
        tag += f"_{args.from_sec:g}-{args.to_sec:g}s" if (
            args.from_sec is not None or args.to_sec is not None) else ""
        ext = "mp4" if args.bg == "dark" else "mov"
        out = OUT_DIR / f"{Path(M['demo']).stem}__{M['player']}{tag}.{ext}"
    out.parent.mkdir(parents=True, exist_ok=True)

    # 编码参数
    if args.bg == "dark":
        vcodec = ["-c:v", "libx264", "-preset", "veryfast",
                  "-crf", str(args.crf), "-pix_fmt", "yuv420p"]
    else:
        vcodec = ["-c:v", "qtrle", "-pix_fmt", "argb"]

    cmd = [
        str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgba",
        "-s", f"{L.width}x{L.height}", "-r", str(fps),
        "-i", "-", *vcodec, "-r", str(fps), str(out),
    ]

    print(f"渲染 {n} 帧 @{fps}fps = {n/fps:.1f}s  ->  {out.name}")
    print(f"画布 {L.width}x{L.height}  bg={args.bg}")

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)

    shot_by_frame = {}
    for s in data["shots"]:
        shot_by_frame.setdefault(s["f"], s["weapon"].replace("weapon_", ""))

    # 每 2 秒刷新一行进度
    step = max(1, fps * 2)
    alive_arr = F.get("ALIVE")
    dead_frames = 0
    try:
        for i in range(n):
            gi = f0 + i
            # ⚠️ 死亡帧必须遮罩：`is_alive` 早就被提取了，但早期这里没消费它，
            #    导致玩家死后键盘照常渲染（用户实测：人物死了键鼠还在动）。
            #    死亡时整帧只留背景（bg=None 则全透明），不画任何键盘。
            if (alive_arr is not None and gi < len(alive_arr)
                    and not alive_arr[gi]):
                dead_frames += 1
                proc.stdin.write(
                    Image.new("RGBA", (L.width, L.height),
                              bg if bg else (0, 0, 0, 0)).tobytes())
                continue
            fr = {k: (v[gi] if gi < len(v) else 0)
                  for k, v in F.items()
                  if k not in ("YAW", "PITCH", "VELZ", "DUCK_AMT",
                               "HP", "ROUND", "ALIVE")}
            # ROUND 现在与帧同长（input_extractor 已修重采样）；
            # 旧 JSON 若长度不符，靠下面这个边界判断兜底不崩。
            rno = F["ROUND"][gi] + 1 if gi < len(F["ROUND"]) else 0
            gun = shot_by_frame.get(gi, "")
            info = f"回合 {rno}   {i/fps:6.1f}s"
            if gun:
                info += f"   [{gun}]"
            im = render_frame(L, fr, gi / fps, bg, info)
            proc.stdin.write(im.tobytes())
            if i % step == 0 or i == n - 1:
                pc = (i + 1) / n * 100
                bar = "#" * int(pc / 4) + "-" * (25 - int(pc / 4))
                sys.stdout.write(f"\r  [{bar}] {pc:5.1f}%  "
                                 f"{i+1}/{n}帧")
                sys.stdout.flush()
    finally:
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        rc = proc.wait()

    print()
    if dead_frames:
        print(f"[i] 死亡遮罩 {dead_frames}/{n} 帧（人物死亡期间不显示键盘）")
    if rc != 0:
        print("[X] ffmpeg 失败:")
        print(err[-2000:])
        return
    if err.strip():
        print("[ffmpeg]", err.strip()[-800:])
    sz = out.stat().st_size / 1e6
    print(f"[OK] {out}  ({sz:.1f} MB)")


if __name__ == "__main__":
    main()
