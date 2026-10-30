# -*- coding: utf-8 -*-
"""
击杀集锦键盘可视化合成 —— 把按键 overlay 叠到 CSDM 录的 POV 画面上。

流程：
  1. CSDM 录出 POV 视频（文件名形如 sequence-N-tick-A-to-B.avi，含 tick 范围）
  2. 本脚本解析文件名里的 tick 范围，用 input_extractor 的按键数据
     渲染同区间的键盘 overlay（透明 MOV）
  3. ffmpeg 把 overlay 叠到 POV 视频上，输出 mp4

用法：
  python src/killviz_compose.py --json <按键JSON> --dir <CSDM输出目录> --out <输出目录>
  python src/killviz_compose.py --json <按键JSON> --video <单个视频> --out <输出目录>
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from keyviz_video import Layout, render_frame  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _find_ffmpeg() -> str:
    """自动探测 ffmpeg（本机有多个位置，硬编码路径会踩空）。"""
    import os
    import shutil

    # 1) 环境变量
    # 1) 环境变量：CS2CUT_FFMPEG（本项目专用）优先，其次兼容通用的 FFMPEG
    for var in ("CS2CUT_FFMPEG", "FFMPEG"):
        env_ff = os.environ.get(var)
        if env_ff and Path(env_ff).exists():
            return env_ff
    # 2) 项目自带 tools 目录（优先）
    tools = ROOT / "tools"
    if tools.is_dir():
        for d in sorted(tools.glob("ffmpeg*"), reverse=True):
            for name in ("ffmpeg.exe", "bin/ffmpeg.exe"):
                cand = d / name
                if cand.exists():
                    return str(cand)
    # 3) 常见安装位置
    for p in (
        r"E:\ffmpeg\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    ):
        if Path(p).exists():
            return p
    # 4) PATH
    w = shutil.which("ffmpeg")
    if w:
        return w
    return "ffmpeg"          # 交给系统解析


FFMPEG = _find_ffmpeg()

# CSDM 输出文件名里的 tick 范围：sequence-1-tick-5000-to-5100.avi
TICK_RE = re.compile(r"tick-?(\d+)-to-?(\d+)", re.IGNORECASE)

POS_EXPR = {
    "bottom-right": "W-w-24:H-h-24",
    "bottom-left": "24:H-h-24",
    "bottom-left-high": "24:H-h-150",       # 左下抬高，避开血量/弹药 HUD
    "bottom-center": "(W-w)/2:H-h-24",
    "top-right": "W-w-24:24",
    "top-left": "24:24",
    "center-left": "24:(H-h)/2",
    "center-right": "W-w-24:(H-h)/2",
    "top-center": "(W-w)/2:24",
}


def tick_to_frame(tick: int, fps: int, tickrate: int) -> int:
    return int(round(tick * fps / tickrate))


def render_overlay(data: dict, tick0: int, tick1: int, out_mov: Path,
                   fps: int, scale: float = 1.0, opacity: float = 1.0,
                   dead_fade: float = 0.3, supersample: int = 2) -> bool:
    """渲染 [tick0, tick1] 区间的键盘 overlay（透明 MOV）。

    参数 fps 是**输出视频帧率**。按键数据自带采样率（data.meta.fps），
    两者可能不同（例如按键 60fps、视频 30fps），必须按数据自身 fps
    换算索引，否则键盘动画会快/慢倍数。

    dead_fade：玩家死亡（`ALIVE == 0`）后，键盘层在多少秒内淡出到全透明。
      0 = 硬切（死亡瞬间直接消失）；默认 0.3s 更自然。

    supersample：超采样倍数。键盘层按 `scale * supersample` 的尺寸渲染，
      再由 `compose()` 的 scale 滤镜缩回 `scale` 的大小 —— 边缘和文字会先按
      高分辨率绘制、再降采样，抗锯齿质量明显更好（圆角不锯齿、小字不发毛）。
      1 = 关闭。**这是「键盘看着糊」的主要解法之一**：源图分辨率不足时，
      1:1 叠到 2K/4K 画面上，8px 的中文提示字会直接糊成墨团。

    ⚠️ 为什么必须消费 `ALIVE`：它早就被 input_extractor 提取并写进 JSON，
      但早期这里把 ALIVE 和其他内部字段一起**从渲染字段里排除了**，
      render_frame 也没有任何死亡遮罩 → 玩家死后键盘照常渲染。
      用户实测看到「人物死了键鼠还在动」，而且 CS2 没有 CS:GO 的
      `spec_lock_to_accountid` 锁视角（走的是 `spec_mode 1` + `spec_player`），
      玩家死后画面主体会脱离本人 → 看起来「键盘和人物对不上」。
      2026-10-01 修复。
    """
    from PIL import Image

    F = data["frames"]
    tickrate = data["meta"]["tickrate"]
    data_fps = int(data["meta"].get("fps", fps))     # ← 数据的采样率
    alive_arr = F.get("ALIVE")

    # 用「数据 fps」定位起止帧（不是输出 fps）
    f0 = tick_to_frame(tick0, data_fps, tickrate)
    f1 = tick_to_frame(tick1, data_fps, tickrate)
    span = max(1, f1 - f0)                            # 数据上的帧数

    # 输出帧数按输出 fps 算，保持与视频等长
    n = max(1, int(round((tick1 - tick0) / tickrate * fps)))

    # ── 输出帧 i → 数据帧 gi，并预计算键盘层每帧的 alpha ──
    gi_of = [f0 + (int(round(i * span / n)) if n > 1 else 0) for i in range(n)]

    def _alive(gi: int) -> bool:
        """该数据帧玩家是否存活。没有 ALIVE 字段（旧 JSON）时一律视为存活。"""
        if alive_arr is None:
            return True
        return bool(alive_arr[gi]) if 0 <= gi < len(alive_arr) else True

    fade_n = max(0, int(round(dead_fade * fps)))
    alphas = [1.0] * n
    dead_run = 0
    dead_frames = 0
    for i, gi in enumerate(gi_of):
        if _alive(gi):
            dead_run = 0
            alphas[i] = 1.0
        else:
            dead_frames += 1
            dead_run += 1
            alphas[i] = (max(0.0, 1.0 - dead_run / fade_n)
                         if fade_n > 0 else 0.0)

    # 超采样：按 scale*supersample 绘制，compose 里再缩回 scale 的尺寸
    L = Layout(scale * max(1, supersample))
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgba",
        "-s", f"{L.width}x{L.height}", "-r", str(fps),
        "-i", "-",
        "-c:v", "qtrle", "-pix_fmt", "argb", str(out_mov),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    blank = None
    try:
        for i, gi in enumerate(gi_of):
            al = alphas[i] * opacity
            if al <= 0.0:
                # 死亡期间（已过淡出）：输出全透明帧，连绘制都省掉
                if blank is None:
                    blank = Image.new("RGBA", (L.width, L.height), (0, 0, 0, 0))
                proc.stdin.write(blank.tobytes())
                continue
            # 把输出帧 i 映射回数据帧（两套 fps 之间插值取样）
            fr = {k: (v[gi] if 0 <= gi < len(v) else 0)
                  for k, v in F.items()
                  if k not in ("YAW", "PITCH", "VELZ", "DUCK_AMT", "HP", "ROUND", "ALIVE")}
            im = render_frame(L, fr, i / fps, None, "")
            if al < 1.0:
                a = im.getchannel("A").point(lambda x: int(x * al))
                im.putalpha(a)
            proc.stdin.write(im.tobytes())
    finally:
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        rc = proc.wait()
    if rc != 0:
        print(f"    [X] overlay 渲染失败: {err[-300:]}")
        return False
    if dead_frames:
        print(f"    [i] 死亡遮罩 {dead_frames}/{n} 帧（淡出 {dead_fade}s）")
    return True


def compose(pov: Path, overlay: Path, out: Path, pos: str = "bottom-right",
            preset: str = "veryfast", crf: int = 18, supersample: int = 2,
            blend_format: str = "rgb") -> bool:
    """ffmpeg 把 overlay 叠到 POV 视频上。

    preset 默认 veryfast（而非 medium）：这是「叠加」而非「原样转码」，
    同一 CRF 下画质差异肉眼不可辨，但 veryfast 快约 2.2 倍 —— 实测 2K60 单段
    6.46s → 2.94s。22 段 = 156 秒成片的实测代价：
      叠加环节 132s → 60s，全片体积 605MB → 651MB（+7.6% / +46MB）。
    注意：+7.6% 是 22 段累计值，单段抽测只有 +3.6% —— 别用单段数据外推整片。
    想要更小体积就把 preset 换成 medium（慢 2.2 倍，体积回落约 46MB）。

    supersample：overlay 由 `render_overlay` 按 N 倍尺寸绘制（例如 scale=1.1、N=2
      → 实际画布 364px），这里用 lanczos 缩回 1/N（→ 182px）。
      「高分辨率绘制 + 降采样」= 抗锯齿，圆角不毛、小字更实。1 = 关闭。

    blend_format：overlay 混合所用的像素格式。默认 `rgb`。
      ⚠️ 用 `auto`（= 跟随主视频的 yuv420p）会在**色度减半**的域里混合，
      橙色/红色键帽的边缘颜色会渗出去，看起来发糊；RGB 域混合则边缘干净。
      代价是每帧多一次色彩空间转换，实测代价很小。
    """
    expr = POS_EXPR.get(pos, POS_EXPR["bottom-right"])
    fmt = blend_format or "auto"
    ss = max(1, int(supersample))
    if ss > 1:
        fc = (f"[1:v]scale=iw/{ss}:ih/{ss}:flags=lanczos[ov];"
              f"[0:v][ov]overlay={expr}:format={fmt}")
    else:
        fc = f"[0:v][1:v]overlay={expr}:format={fmt}"
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(pov), "-i", str(overlay),
        "-filter_complex", fc,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    [X] 合成失败: {r.stderr[-300:]}")
        return False
    return True


def parse_tick_range(path: Path):
    """从文件名解析 tick 范围。"""
    m = TICK_RE.search(path.stem)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def seq_index(path: Path):
    """从文件名解析 sequence 序号（用于排序）。解析不出给一个很大的数。"""
    m = re.search(r"sequence[-_]?(\d+)", path.stem, re.IGNORECASE)
    return int(m.group(1)) if m else 10 ** 9


def tick_sort_key(path: Path):
    """排序键：优先按 tick 起点，其次按 sequence 序号。"""
    rng = parse_tick_range(path)
    return (0, rng[0], seq_index(path)) if rng else (1, 0, seq_index(path))


FFPROBE = str(Path(FFMPEG).with_name("ffprobe.exe")) if Path(FFMPEG).suffix else "ffprobe"


def probe_duration(path: Path) -> float:
    """用 ffprobe 读视频时长（秒）。失败返回 0.0。"""
    exe = FFPROBE if Path(FFPROBE).exists() else "ffprobe"
    try:
        r = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float((r.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def probe_size(path: Path):
    """用 ffprobe 取视频 (宽, 高)。失败返回 None。"""
    exe = FFPROBE if Path(FFPROBE).exists() else "ffprobe"
    try:
        r = subprocess.run(
            [exe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        vals = [int(x) for x in (r.stdout or "").split() if x.strip().isdigit()]
        return (vals[0], vals[1]) if len(vals) >= 2 else None
    except Exception:
        return None


def collect_videos(d: Path) -> list[Path]:
    """收集目录下的**分段**视频（sequence-*），按 tick 顺序排序。

    注意排除已合成的 *_keyviz.mp4，否则重复运行时会把成品当输入。
    """
    vids = []
    for pat in ("sequence-*.avi", "sequence-*.mp4", "sequence-*.mov"):
        vids += [p for p in d.glob(pat) if "_keyviz" not in p.stem]
    # 去重（同名不同扩展名的极端情况）+ 排序
    uniq = {p.resolve(): p for p in vids}
    return sorted(uniq.values(), key=tick_sort_key)


def _probe_stream_sig(path: Path) -> tuple:
    """取视频流的「编码签名」：编码器/分辨率/帧率/像素格式/时基。

    用于判断多段能否直接 `-c copy` 拼接 —— 签名一致才能安全 copy。
    """
    exe = FFPROBE if Path(FFPROBE).exists() else "ffprobe"
    try:
        r = subprocess.run(
            [exe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,width,height,r_frame_rate,pix_fmt,time_base",
             "-of", "default=nw=1:nk=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return tuple(sorted(
            ln.strip() for ln in r.stdout.splitlines() if "=" in ln))
    except Exception:
        return ()


def _concat_copy(parts: list[Path], out: Path) -> bool:
    """零重编码拼接（只改容器）。秒级完成、画质无损。

    前提：所有分段的编码参数一致。若签名不一致，调用方应改用重编码。
    """
    lst = out.parent / (out.stem + "_concat.txt")
    lines = []
    for p in parts:
        sp = str(p.resolve()).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{sp}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c", "copy",                               # ★ 不重编码
        "-movflags", "+faststart",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        try:
            lst.unlink()
        except OSError:
            pass
    if r.returncode != 0:
        return False

    # 抽检而非全片解码：
    #   全片解码要 ~8s（156s/20x），而 `-c copy` 本身只要 ~1s —— 校验比干活还慢。
    #   这里只解码「首段 / 中间段 / 末段」各 1 秒，足以发现拼接点损坏。
    #   （copy 失败通常会让 ffmpeg 直接非 0 退出，上一步已拦住大部分问题。）
    idxs = sorted({0, len(parts) // 2, len(parts) - 1})
    for i in idxs:
        p = parts[i]
        chk = subprocess.run(
            [FFMPEG, "-v", "error", "-i", str(p), "-t", "1", "-f", "null", "-"],
            capture_output=True, text=True)
        if chk.returncode != 0 or chk.stderr.strip():
            print(f"    [!] 抽检第 {i+1} 段失败: {chk.stderr.strip()[:150]}")
            return False
    # 产物本身至少要能被 ffprobe 读出正确时长（容器结构完好）
    dur = probe_duration(out)
    if dur <= 0:
        print("    [!] 产物时长读取失败")
        return False
    return True


def _concat_reencode(parts: list[Path], out: Path) -> bool:
    """重编码拼接（慢，但兼容性最好）—— copy 失败时的兜底。"""
    lst = out.parent / (out.stem + "_concat.txt")
    lines = []
    for p in parts:
        sp = str(p.resolve()).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{sp}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        try:
            lst.unlink()
        except OSError:
            pass
    if r.returncode != 0:
        print(f"    [X] 合并失败: {r.stderr[-300:]}")
        return False
    return True


def concat_videos(parts: list[Path], out: Path, prefer_copy: bool = True) -> bool:
    """把多段视频拼成一个成品。

    ★ 默认先试「零重编码 copy」——同一次 CSDM 录制出的分段编码参数完全一致，
      实测 22 段 / 156 秒 / 2K60 只需 **1 秒**（重编码要 366 秒，慢 366 倍），
      且画质无损、体积更小（省掉一次编码损失）。

      仅当各段编码签名不一致、或 copy 产物解码校验失败时，才回退重编码。
      `prefer_copy=False` 可强制走重编码（用于排查）。
    """
    if not parts:
        return False
    if len(parts) == 1:
        import shutil
        shutil.copy2(parts[0], out)
        return True

    if prefer_copy:
        # 签名一致才敢 copy（第一段当基准）
        base = _probe_stream_sig(parts[0])
        same = bool(base) and all(_probe_stream_sig(p) == base for p in parts[1:])
        if same:
            print(f"    拼接方式：零重编码 copy（{len(parts)} 段签名一致）")
            if _concat_copy(parts, out):
                return True
            print("    [!] copy 拼接校验未通过，回退重编码")
            try:
                out.unlink()
            except OSError:
                pass
        else:
            print("    拼接方式：重编码（各段编码签名不一致）")

    return _concat_reencode(parts, out)


def main():
    ap = argparse.ArgumentParser(description="合成带键盘可视化的击杀集锦")
    ap.add_argument("--json", required=True, help="按键 JSON（input_extractor 产出）")
    ap.add_argument("--dir", help="CSDM 输出目录（含 sequence-*.avi）")
    ap.add_argument("--video", help="单个 POV 视频（无 tick 区间时整段叠）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--fps", type=int, default=30, help="POV 视频帧率（CSDM 默认 30）")
    ap.add_argument("--pos", default="bottom-right",
                    choices=list(POS_EXPR.keys()))
    ap.add_argument("--scale", type=float, default=0.0,
                    help="键盘缩放；<=0 = 按视频高度自适应（推荐，清晰度更好）")
    ap.add_argument("--key-height-ratio", type=float, default=0.24,
                    help="自适应时键盘高度占视频高度的比例（默认 0.24）")
    ap.add_argument("--supersample", type=int, default=2,
                    help="键盘层超采样倍数（1=关；2~3 更锐利，默认 2）")
    ap.add_argument("--blend-format", default="rgb",
                    help="overlay 混合像素格式；rgb 最锐（默认），auto 跟随主视频")
    ap.add_argument("--opacity", type=float, default=0.7)
    ap.add_argument("--dead-fade", type=float, default=0.3,
                    help="玩家死亡后键盘层淡出秒数（0=死亡瞬间直接隐藏）")
    ap.add_argument("--preset", default="veryfast",
                    help="叠加时的 x264 preset（默认 veryfast；medium 画质略好但慢 2.2x）")
    ap.add_argument("--crf", type=int, default=18, help="叠加时的 x264 CRF")
    ap.add_argument("--no-copy-concat", action="store_true",
                    help="合并时强制重编码（默认优先零重编码 copy，快 15x）")
    ap.add_argument("--concat", action="store_true",
                    help="叠完后把各段合并成一个成品（默认关闭）")
    ap.add_argument("--name", default="keyviz_final.mp4",
                    help="合并后的成品文件名")
    ap.add_argument("--start-tick", type=int, default=None,
                    help="--video 模式下的起点 tick（合并后的单文件需要）")
    ap.add_argument("--keep-parts", action="store_true",
                    help="保留分段产物，不清理")
    args = ap.parse_args()

    import json
    data = json.loads(Path(args.json).read_text(encoding="utf-8"))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = out_dir / "_overlay"
    work_dir.mkdir(exist_ok=True)

    # 收集待处理视频
    videos = []
    forced_range = None
    if args.video:
        videos = [Path(args.video)]
        # 单文件模式：文件名可能没有 tick 区间（CSDM 合并后的 output.mp4）。
        # 这时若知道整段起点 tick，就用「起点 + 时长」推算区间。
        if not parse_tick_range(Path(args.video)) and args.start_tick is not None:
            forced_range = args.start_tick
    elif args.dir:
        videos = collect_videos(Path(args.dir))
    if not videos:
        print("[X] 没找到视频")
        return

    def scale_for(video: Path) -> float:
        """键盘缩放：显式 `--scale > 0` 优先；否则按视频高度自适应。

        自适应公式：scale = 视频高 × key_height_ratio / (scale=1 时的画布高)。

        为什么要自适应：键盘层是 **1:1 逐像素**叠到画面上的，固定 scale 在低分辨率
        下偏大、在 2K/4K 下偏小 —— 偏小会让 8px 的中文提示字（"前/换弹/静步"）
        直接糊成墨团。2K(1440p) 下 0.24 → scale≈1.03，中文才真正可读。
        """
        if args.scale and args.scale > 0:
            return args.scale
        sz = probe_size(video)
        if not sz:
            return 0.55                       # 探测失败 → 回退旧默认
        base = Layout(1.0).height             # scale=1 时的画布高
        return round(sz[1] * args.key_height_ratio / base, 3)

    print(f"共 {len(videos)} 段视频，fps={args.fps}")
    results = []
    for i, pov in enumerate(videos, 1):
        rng = parse_tick_range(pov)
        if not rng and forced_range is not None:
            # 用视频时长推终点 tick
            rng = (forced_range, forced_range)      # 占位，下面用时长修正
        if not rng:
            print(f"[{i}/{len(videos)}] {pov.name} 文件名无 tick 范围，跳过")
            continue
        t0, t1 = rng
        if t0 == t1:
            # 靠视频时长推算（无区间信息的单文件，如 CSDM 合并后的 output.mp4）
            dur = probe_duration(pov)
            tr = data["meta"]["tickrate"]
            t1 = t0 + int(round(dur * tr))
        sc = scale_for(pov)
        print(f"[{i}/{len(videos)}] {pov.name}  tick {t0}->{t1}  scale={sc}")
        ov = work_dir / (pov.stem + "_keys.mov")
        if not render_overlay(data, t0, t1, ov, args.fps, sc,
                              args.opacity, args.dead_fade, args.supersample):
            continue
        out = out_dir / (pov.stem + "_keyviz.mp4")
        if compose(pov, ov, out, args.pos, args.preset, args.crf,
                   args.supersample, args.blend_format):
            sz = out.stat().st_size / 1e6
            print(f"    [OK] {out.name} ({sz:.1f}MB)")
            results.append(out)
        if not args.keep_parts:
            try:
                ov.unlink()                         # overlay 中间件很占空间
            except OSError:
                pass

    print(f"\n完成 {len(results)}/{len(videos)} 段")

    # 合并成一个成品（可选）
    if args.concat and results:
        # 按 tick 顺序排（results 已按输入顺序，输入已排好序）
        final = out_dir / args.name
        print(f"正在合并 {len(results)} 段 -> {final.name} ...")
        if concat_videos(results, final, prefer_copy=not args.no_copy_concat):
            sz = final.stat().st_size / 1e6
            print(f"[OK] 成品：{final} ({sz:.1f}MB)")
            if not args.keep_parts:
                for p in results:
                    try:
                        p.unlink()
                    except OSError:
                        pass
        else:
            print("[X] 合并失败，保留分段产物")
    else:
        print(f"输出在 {out_dir}")


if __name__ == "__main__":
    main()
