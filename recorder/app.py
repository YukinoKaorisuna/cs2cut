# -*- coding: utf-8 -*-
"""
CS2 击杀集锦录制前端后端 —— Flask 服务。

功能：
  1. 扫描 demo 目录，列出 .dem 文件
  2. demoparser2 解析 demo，列出选手 + 击杀列表
  3. 生成 CSDM 录制命令（POV 击杀集锦，4K）
  4. 检测 CSDM 是否安装
  5. 后台执行录制任务，实时回传日志

依赖：flask + demoparser2（都在 managed venv）
运行：
  python recorder/app.py
  然后浏览器打开 http://127.0.0.1:5000
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent

# 版本号：改功能时顺手 +1，方便一眼确认「跑的是不是新代码」
# （踩过坑：旧实例占着端口继续服务旧代码，改了半天不生效）
APP_VERSION = "1.6.0-batch"

sys.path.insert(0, str(ROOT / "src"))
from input_extractor import extract as extract_keys  # noqa: E402
import csdm_plugin as plug  # noqa: E402

app = Flask(__name__, static_folder="static", static_url_path="")

# 默认扫描的 demo 目录（按顺序，第一个存在且有 demo 的优先）
DEFAULT_DIRS = [
    r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo",
    str(ROOT / "demos"),
]

# 默认输出目录（分段 + 成片都落这里；前端可自定义并记忆）
DEFAULT_OUT_DIR = ROOT / "output" / "videos"

# CSDM 可能的安装位置（CLI 是 csdm.cmd，不是 exe）
# 用环境变量拼路径，而不是写死某台机器的用户名 —— 换人/换机能直接跑。
def _csdm_candidates() -> list[str]:
    local = os.environ.get("LOCALAPPDATA", "")
    roam = os.environ.get("APPDATA", "")
    prog = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    prog86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    out: list[str] = []
    for base in (local, roam):
        if not base:
            continue
        for rel in ("Programs\\cs-demo-manager", "Programs\\CS Demo Manager",
                    "cs-demo-manager"):
            d = os.path.join(base, rel)
            out += [os.path.join(d, "csdm.cmd"), os.path.join(d, "csdm.exe")]
    for base in (prog, prog86):
        out.append(os.path.join(base, "CS Demo Manager", "csdm.exe"))
    return out


CSDM_CANDIDATES = _csdm_candidates()

# 录制任务状态（内存）
TASKS: dict[str, dict] = {}
TASKS_LOCK = threading.Lock()


# ───────────────────────── 工具函数 ─────────────────────────

def find_csdm() -> str | None:
    """找 csdm.exe。"""
    for p in CSDM_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return shutil.which("csdm")


def scan_demos(directory: str) -> list[dict]:
    """扫描目录下的 .dem 文件（排除 .bak/.bz2/.zst 等）。"""
    d = Path(directory)
    if not d.is_dir():
        return []
    out = []
    for f in d.iterdir():
        if not f.is_file():
            continue
        name = f.name.lower()
        if not name.endswith(".dem"):
            continue
        out.append({
            "name": f.name,
            "path": str(f),
            "size_mb": round(f.stat().st_size / 1e6, 1),
            "mtime": time.strftime("%Y-%m-%d %H:%M",
                                   time.localtime(f.stat().st_mtime)),
        })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def _is_num(v) -> bool:
    """True 表示 v 是可用作数字的有限值（排除 None / NaN / NaT）。

    注意：bool(float('nan')) 是 True，所以不能直接 if sid:。
    """
    if v is None:
        return False
    try:
        return v == v  # NaN != NaN
    except Exception:
        return False


def _to_int(v, default=None):
    """安全转 int：NaN / None / 无法转换 -> default。"""
    if not _is_num(v):
        return default
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def list_players(demo_path: str) -> list[dict]:
    """解析 demo，返回选手列表（name + steamid）。"""
    from demoparser2 import DemoParser
    p = DemoParser(demo_path)
    deaths = p.parse_event("player_death")
    players: dict[str, str] = {}
    for _, r in deaths.iterrows():
        name = r.get("attacker_name")
        sid = r.get("attacker_steamid")
        sid_i = _to_int(sid)
        # 自杀 / 世界伤害的 attacker_steamid 是 NaN，必须剔除
        if name and sid_i is not None and str(name) != "nan":
            players[str(name)] = str(sid_i)
    return [{"name": n, "steamid": s} for n, s in sorted(players.items())]


def list_kills(demo_path: str, steamid: str) -> list[dict]:
    """解析 demo，返回指定选手的击杀列表（tick/武器/受害者/爆头）。"""
    from demoparser2 import DemoParser
    p = DemoParser(demo_path)
    deaths = p.parse_event("player_death")
    want = str(_to_int(steamid) or steamid)
    kills = []
    for _, r in deaths.iterrows():
        sid = _to_int(r.get("attacker_steamid"))
        if sid is None or str(sid) != want:
            continue
        tk = _to_int(r.get("tick"), 0)
        kills.append({
            "tick": tk,
            "weapon": str(r.get("weapon", "")).replace("weapon_", ""),
            "victim": str(r.get("user_name", "")),
            "headshot": bool(r.get("headshot", False)) if _is_num(r.get("headshot")) or isinstance(r.get("headshot"), bool) else False,
        })
    kills.sort(key=lambda k: k["tick"])
    return kills


# ───────────── 画质预设 ─────────────
# 每个档位绑定「分辨率 + 帧率 + CRF + 最大码率」，避免只调分辨率不调码率导致画质不对。
# maxrate：2K/4K 必须给足码率，否则即使 CRF 小也会被编码器压糊。
QUALITY_PRESETS: dict[str, dict] = {
    "4k-high":  {"width": 3840, "height": 2160, "fps": 60, "crf": 14, "maxrate": "80M",
                 "label": "4K 高画质（剪辑母版，体积很大）"},
    "4k":       {"width": 3840, "height": 2160, "fps": 60, "crf": 18, "maxrate": "50M",
                 "label": "4K 标准"},
    "2k-high":  {"width": 2560, "height": 1440, "fps": 60, "crf": 14, "maxrate": "45M",
                 "label": "2K 高画质（2560×1440，推荐剪辑用）"},
    "2k":       {"width": 2560, "height": 1440, "fps": 60, "crf": 18, "maxrate": "28M",
                 "label": "2K 标准"},
    "2k-30":    {"width": 2560, "height": 1440, "fps": 30, "crf": 16, "maxrate": "22M",
                 "label": "2K 30fps（体积更小）"},
    "1080p-high": {"width": 1920, "height": 1080, "fps": 60, "crf": 14, "maxrate": "25M",
                   "label": "1080p 高画质"},
    "1080p":    {"width": 1920, "height": 1080, "fps": 60, "crf": 18, "maxrate": "15M",
                 "label": "1080p 标准"},
    "preview":  {"width": 1280, "height": 720, "fps": 30, "crf": 23, "maxrate": "6M",
                 "label": "快速预览（720p，体积小）"},
}

DEFAULT_PRESET = "2k-high"

# 叠加环节（第 5 步）的 x264 preset。与上面的「录制画质」是两回事：
# 录制由 CSDM/ffmpeg 直出，这里只管「把键盘 overlay 压回去」。
# veryfast 实测比 medium 快 2.2 倍，22 段成片体积 +7.6%；medium 反之。
DEFAULT_OVERLAY_PRESET = "veryfast"


# ───────────────────── 用户偏好（记忆上次的选择） ─────────────────────
# 落盘到 recorder/prefs.json：页面每次打开都读回上次的 demo 目录、输出位置
# 和全部录制参数，避免「每次重进都得重调一遍」。
PREFS_PATH = BASE / "prefs.json"
PREFS_LOCK = threading.Lock()

# 白名单：只持久化这些键，前端塞别的进来一律丢弃。
# demo_dir = 上次扫描的 demo 目录；out_dir / out_name = 输出位置与文件名。
PREFS_KEYS = {
    "demo_dir", "out_dir", "out_name",
    "before", "after", "preset", "res", "fps", "mode",
    "pov", "xray", "key_pos", "key_scale", "key_opacity",
    "overlay_preset", "sky", "no_flash", "fov",
    # 批量模式
    "batch_dir", "batch_player", "batch_overwrite", "batch_prefix",
}

# 输出文件名里不允许出现的字符（Windows 保留字符 + 控制字符）
_BAD_FN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def load_prefs() -> dict:
    """读偏好。文件不存在 / 损坏 / 键不在白名单 → 返回干净的 dict，不抛异常。"""
    try:
        data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k in PREFS_KEYS}
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"读取 prefs 失败（忽略，用默认值）: {e}\n")
    return {}


def save_prefs(patch: dict) -> dict:
    """合并式保存：只覆盖 patch 里出现的键，其余保持原样。"""
    with PREFS_LOCK:
        cur = load_prefs()
        for k, v in (patch or {}).items():
            if k in PREFS_KEYS:
                cur[k] = v
        try:
            PREFS_PATH.write_text(
                json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"写入 prefs 失败: {e}\n")
        return cur


def sanitize_out_name(name: str | None, default: str) -> str:
    """把用户填的「输出视频名称」变成安全的文件名（不含目录成分）。

    - 空 → 用 default
    - 去掉任何路径成分（防 `..\\..\\x` 逃逸出输出目录）
    - 非法字符换成 `_`
    - 没写视频扩展名 → 自动补 `.mp4`
    """
    raw = (name or "").strip()
    if not raw:
        return default
    raw = Path(raw).name                      # 只保留最后一段，丢弃目录
    raw = _BAD_FN_CHARS.sub("_", raw).strip(" .")
    if not raw or raw in (".", ".."):
        return default
    if not raw.lower().endswith((".mp4", ".mov", ".mkv")):
        raw += ".mp4"
    return raw


def resolve_quality(cfg: dict) -> dict:
    """把 cfg 解析成最终的 (width, height, fps, crf, maxrate)。

    优先级：显式 width/height/fps（前端单独下拉）> 预设 > 默认。
    preset 为 "custom" 时完全用显式值。
    """
    preset = cfg.get("preset") or DEFAULT_PRESET
    p = QUALITY_PRESETS.get(preset, QUALITY_PRESETS[DEFAULT_PRESET])
    out = {
        "width": int(cfg.get("width") or p["width"]),
        "height": int(cfg.get("height") or p["height"]),
        "fps": int(cfg.get("fps") or p["fps"]),
        "crf": int(cfg.get("crf") or p["crf"]),
        "maxrate": cfg.get("maxrate") or p["maxrate"],
    }
    return out


def build_csdm_cmd(csdm_path: str, cfg: dict) -> list[str]:
    """生成 CSDM 录制命令（POV 击杀集锦）。

    ⚠️ 关键：`--event` 的值必须是**小写** `kills` / `deaths` / `rounds`。
    CSDM 内部枚举为 {Kills:"kills",Deaths:"deaths",Rounds:"rounds"}，
    校验函数 `Object.values(iu).includes(v)`，传 "Kills" 会直接
    throw `Invalid event. Supported values: kills, deaths, rounds`。
    """
    q = resolve_quality(cfg)
    cmd = [csdm_path, "video", cfg["demo"]]
    cmd += ["--mode", "player", "--steamids", cfg["steamid"],
            "--event", "kills"]              # ← 必须小写
    cmd += ["--start-seconds-before", str(cfg.get("before", 3))]
    cmd += ["--end-seconds-after", str(cfg.get("after", 3))]
    if cfg.get("pov", True):
        cmd += ["--perspective", "player"]
    if not cfg.get("xray", False):
        cmd += ["--no-show-x-ray"]
    cmd += ["--framerate", str(q["fps"])]
    cmd += ["--width", str(q["width"])]
    cmd += ["--height", str(q["height"])]
    cmd += ["--ffmpeg-video-container", "mp4"]
    cmd += ["--ffmpeg-crf", str(q["crf"])]
    # 码率上限：告诉 x264 别为了省体积把画面压糊。
    # ⚠️ 必须用 `=` 连接！值以 `-` 开头（-maxrate），用空格会被参数解析器
    #    当成另一个选项，报 "argument is ambiguous" 并静默失败（rc 仍是 0）。
    #    正确：--ffmpeg-output-parameters=-maxrate 45M -bufsize 45M
    mr = q["maxrate"]
    cmd += [f"--ffmpeg-output-parameters=-maxrate {mr} -bufsize {mr}"]
    # 编码器固定用 FFmpeg（HLAE 内置的 VirtualDub 输出质量差）
    cmd += ["--encoder-software", "FFmpeg"]
    # ⚠️ 故意**不加** `--concatenate-sequences`（CSDM CLI 默认值本就是 false，
    #    源码：`defaultSettings.video.concatenateSequences = false`）。
    #    原因：合并后产物统一叫 output.mp4，文件名丢失 `tick-A-to-B` 区间，
    #    键盘可视化（killviz_compose）无法把按键数据对齐到画面上，
    #    会报「文件名无 tick 范围，跳过」而整段失败。
    #    正确做法：让 CSDM 保持分段输出 `sequence-N-tick-A-to-B.avi`，
    #    由后端逐段叠键盘、再自己 concat 合并成一个成品。
    #    需要显式关闭时可用 `--no-concatenate-sequences`（源码已确认存在）。
    # 录完自动关游戏
    cmd += ["--close-game-after-recording"]
    # 画面模式：hud/full 显示完整 HUD（血量武器弹药），pure/keys 隐藏
    mode = cfg.get("mode", "full")
    if mode in ("hud", "full"):
        cmd += ["--no-show-only-death-notices"]
    else:
        cmd += ["--show-only-death-notices"]
    return cmd


def _link_batch(new_videos: list, out_dir: Path) -> Path:
    """把「本次录制新产的分段」聚到一个临时子目录，供 killviz_compose --dir 扫描。

    为什么需要：CSDM 每次录制的分段都落在同一个 out_dir，
    如果直接让 compose 扫 out_dir，上一次跑剩下的 sequence-* 也会被叠进成品。
    这里用硬链接（同盘、零拷贝、不改 mtime）把本批段聚到一起。
    硬链接失败（跨盘等）时退化为复制。
    """
    batch = Path(out_dir) / "_batch"
    if batch.exists():
        shutil.rmtree(batch, ignore_errors=True)
    batch.mkdir(parents=True, exist_ok=True)
    for v in new_videos:
        src = Path(v)
        dst = batch / src.name
        try:
            os.link(src, dst)                   # 硬链接：零拷贝
        except OSError:
            try:
                shutil.copy2(src, dst)
            except OSError:
                continue
    return batch


def merge_dir_videos(out_dir: Path, name: str = "final.mp4", parts: list | None = None):
    """把 CSDM 分段视频按 tick 顺序合并成一个成品。

    复用 killviz_compose 的 concat 能力，避免在 app.py 里再抄一份 ffmpeg 逻辑。
    `parts` 给定时只用这批（本次新产的分段）；否则扫整个 out_dir。
    ★ 建议总是传 parts：output 目录可能堆积多个 demo / 选手的旧段，
      扫全目录会把它们一起合进成品（静默出错，不报错）。
    返回成品 Path（失败返回 None）。
    """
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from killviz_compose import collect_videos, concat_videos, tick_sort_key  # noqa: E402
    except Exception as e:
        sys.stderr.write(f"import killviz_compose 失败: {e}\n")
        return None
    if parts:
        ps = [Path(p) for p in parts]
        try:
            ps.sort(key=tick_sort_key)      # 文件名带 tick 区间才能这样排
        except Exception:
            ps.sort()
    else:
        ps = collect_videos(Path(out_dir))
    if not ps:
        return None
    dst = Path(out_dir) / name
    return dst if concat_videos(ps, dst) else None


def diff_produced(before: dict, after: dict) -> list[str]:
    """挑出「本次运行新产出或被重写」的视频文件。

    ⚠️ 不能只比路径。CSDM 的分段文件名由「序号 + tick 区间」决定：
    同一个 demo + 同一个选手 + 同样的 `--start-seconds-before/--end-seconds-after`
    ⇒ **文件名完全相同**。重录时（例如换画质再跑一遍）路径全都已存在，
    只比路径会把 17 个成功产物误判成「没有生成任何视频」并中断流程，
    `[5/5]` 叠键盘合并因此永远不执行。2026-10-01 实测踩到。
    所以按 (mtime, size) 签名判断：新文件、或签名变了的旧文件，都算本次产出。
    """
    return [p for p, sig in after.items() if before.get(p) != sig]


def build_cfg_commands(cfg: dict) -> str:
    """CFG 区命令（天空/FOV/去闪光等视觉项，不含 HUD）。"""
    lines = ["mirv_endofmatch 0"]
    if cfg.get("no_flash"):
        lines.append("mirv_noflash 1")
    sky = cfg.get("sky", "default")
    if sky and sky != "default":
        lines.append(f"sv_cheats 1; sv_skyname {sky}")
    if cfg.get("fov"):
        lines.append(f"mirv_fov {cfg['fov']}")
    return "\n".join(lines)


# ───────────── 录制单个 demo（单场模式与批量模式共用同一条链路） ─────────────
# 抽出来是为了「批量」和「单场」行为完全一致：同样的参数、同样的失败判定、
# 同样在录制前自检插件。任何只改一遍另一个漏改的分叉都会变成静默不一致。

VIDEO_EXT = ("*.mp4", "*.avi", "*.mov")

# 批量模式里每个 demo 的临时工作子目录名（分段、overlay 中间件都放这里）
PARTS_DIRNAME = "_parts"


def make_logger(sink: list, cap: int = 2000):
    """返回 log(msg)：往 sink 追加并限制长度。"""
    def log(msg: str = "") -> None:
        sink.append(str(msg))
        if len(sink) > cap:
            del sink[: len(sink) - cap]
    return log


def snap_videos(d, exclude_names: set | None = None) -> dict:
    """目录里的视频文件 -> (mtime, size) 签名。

    排除项（按**相对扫描根**的路径判断）：
      `*_keyviz*`  —— 叠加中间件/成品，别当成本次的分段；
      自定义成品名  —— 用户可能不按 *_keyviz 命名，必须显式排除；
      `_batch/`    —— compose 的暂存硬链接目录；
      `_parts/`    —— 批量模式的每-demo 工作目录。

    ⚠️ 必须用 relative_to(root) 判断，不能直接看 p.parts：
       批量模式下扫描根本身就在 `_parts/<prefix-NN>/` 里，
       用绝对 parts 判断会把**本次要统计的分段全部排除**，导致
       「录制成功但判定为没有任何产出」→ 每个 demo 都被误判失败。
    """
    root = Path(d)
    excl = {str(n).lower() for n in (exclude_names or set())}
    out: dict[str, tuple] = {}
    for pat in VIDEO_EXT:
        for p in root.rglob(pat):
            if not p.is_file() or "_keyviz" in p.stem:
                continue
            try:
                rel = p.relative_to(root).parts
            except ValueError:
                rel = p.parts
            if "_batch" in rel or PARTS_DIRNAME in rel:
                continue
            if p.name.lower() in excl:
                continue
            st = p.stat()
            out[str(p)] = (st.st_mtime, st.st_size)
    return out


def wait_cs2_exit(timeout: float = 150.0, poll: float = 3.0, log=None) -> bool:
    """等 cs2.exe 完全退出，返回是否已退出。

    ★ 批量模式必需：上一个 demo 的 cs2.exe 没退干净就启动下一个，
      HLAE 挂钩会失败，而 CSDM 只报一个语焉不详的 `Game error`（rc 仍是 0）。
    """
    def alive() -> bool:
        try:
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq cs2.exe", "/NH"],
                               capture_output=True, text=True, errors="replace",
                               timeout=20)
            return "cs2.exe" in (r.stdout or "").lower()
        except Exception:
            return False

    t0 = time.time()
    warned = False
    while alive():
        if time.time() - t0 > timeout:
            return False
        if log and not warned:
            log("      ...等待上一个 CS2 进程退出（不退干净下个 demo 会 Game error）")
            warned = True
        time.sleep(poll)
    return True


def preflight() -> str | None:
    """批量开跑前的环境预检，返回错误说明（None = 通过）。"""
    import socket
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", 5432))
    except Exception:
        return ("连不上 PostgreSQL（127.0.0.1:5432）。CSDM 的 analyze 依赖它，"
                "批量会整批失败 —— 请先确认服务 postgresql-csdm 正在运行。")
    finally:
        s.close()
    return None


_STEAMID_RE = re.compile(r"^\d{15,20}$")


def looks_like_steamid(s: str) -> bool:
    return bool(_STEAMID_RE.match((s or "").strip()))


def sanitize_name_part(name: str | None, default: str = "clip") -> str:
    """净化「前缀」这类名字片段：剥目录、换非法字符、去掉视频扩展名。"""
    raw = (name or "").strip()
    if raw:
        raw = Path(raw).name
        raw = _BAD_FN_CHARS.sub("_", raw).strip(" .")
    for ext in (".mp4", ".mov", ".mkv"):
        if raw.lower().endswith(ext):
            raw = raw[: -len(ext)]
    return raw or default


def resolve_player(demo: str, spec: str) -> tuple[str | None, str | None]:
    """把「昵称 或 SteamID」解析成 (steamid, 该 demo 里的名字)。

    - 纯数字 → 当 SteamID；名字从本 demo 反查（找不到则名字为 None）。
    - 昵称   → 在本 demo 选手表里精确匹配（忽略大小写）。
    调用方可把解析到的 steamid **锁定**后给后续 demo 复用，避免同名假号。
    """
    spec = (spec or "").strip()
    players = list_players(demo)
    if looks_like_steamid(spec):
        want = re.sub(r"\D", "", spec)
        for p in players:
            if re.sub(r"\D", "", p["steamid"]) == want:
                return p["steamid"], p["name"]
        return spec, None
    low = spec.lower()
    hit = next((p for p in players if p["name"].lower() == low), None)
    return (hit["steamid"], hit["name"]) if hit else (None, None)


def record_one(csdm: str, cfg: dict, demo: str, steamid: str,
               player_name: str | None, out_dir, final_name: str,
               log, need_keys: bool, on_phase=None) -> tuple[str, str]:
    """把一个 demo 录成一条成品。返回 (status, reason)。

    status ∈ {"ok", "partial", "fail"}：
      ok      = 成品已生成
      partial = 有分段但成品没合成（叠键盘/合并失败，已保留分段）
      fail    = 什么都没产出
    单场模式把 ok/partial 都当成功（与改动前一致），批量模式据此记「部分成功」。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    # PostgreSQL 的 bin 目录要在 PATH 里（CSDM analyze 会调 psql）。
    # 优先读环境变量 CS2CUT_PG_BIN，没设就试几个常见安装位置，都没有就跳过。
    _pg = env.get("CS2CUT_PG_BIN", "")
    if not _pg:
        for cand in (r"E:\pgsql18\pgsql\bin", r"C:\Program Files\PostgreSQL\18\bin",
                     r"C:\Program Files\PostgreSQL\17\bin"):
            if os.path.isdir(cand):
                _pg = cand
                break
    if _pg:
        env["PATH"] = _pg + ";" + env.get("PATH", "")

    def phase(p: str) -> None:
        if on_phase:
            on_phase(p)

    def run_step(cmd_list, label=None):
        if label:
            log(label)
        proc = subprocess.Popen(
            cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            env=env, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for line in proc.stdout:
            log(line.rstrip())
        proc.wait()
        return proc.returncode

    # 1. analyze
    phase("分析 demo")
    rc = run_step([csdm, "analyze", demo, "--source", "valve"],
                  "[1/5] 分析 demo 进数据库...")
    if rc != 0:
        return "fail", "analyze 失败"

    # 2. 按键数据（keys / full 模式才需要）
    key_json = None
    if need_keys:
        phase("生成按键数据")
        log("[2/5] 生成按键数据...")
        try:
            name = player_name
            if not name:                       # 只给了 steamid 时反查一次名字
                _, name = resolve_player(demo, str(steamid))
            if name:
                data = extract_keys(demo, name, fps=60)
                key_json = ROOT / "output" / "inputs" / f"{Path(demo).stem}__{name}.json"
                key_json.parent.mkdir(parents=True, exist_ok=True)
                key_json.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                log("      按键数据已生成")
            else:
                log("[!] 本 demo 里找不到这位选手，按键数据无法生成")
        except Exception as e:  # noqa: BLE001
            log(f"[!] 按键数据生成失败：{e}")

    # 3. 插件自检（CS2 每次更新都会重置 gameinfo.gi 并清掉插件）
    phase("自检插件")
    log("[3/5] 自检 CS2 插件...")
    pk = plug.check_plugin()
    if pk["ok"]:
        log(f"      插件正常（CS2 {pk.get('version', {}).get('PatchVersion', '?')}）")
    else:
        log(f"      [!] {pk['reason']}")
        log("      → CS2 更新会重置 gameinfo.gi 并清掉插件，正在自动重装...")
        res = plug.install_plugin()
        for s in res.get("steps", []):
            log(f"        · {s}")
        if not res["ok"]:
            log(f"      [X] 插件修复失败：{res.get('error')}")
            return "fail", "插件修复失败（可打开 CSDM 图形界面 → Settings 手动重装）"
        log("      插件已修复 ✓")

    # 4. 录制（分段输出，刻意不合并 —— 见 build_csdm_cmd 的注释）
    phase("录制中")
    before = snap_videos(out_dir, {final_name})
    rc = run_step(build_csdm_cmd(csdm, {**cfg, "demo": demo, "steamid": str(steamid)})
                  + ["--output", str(out_dir)],
                  "[4/5] 录制 POV 击杀集锦（启动游戏，需几分钟）...")
    after = snap_videos(out_dir, {final_name})
    new_videos = diff_produced(before, after)

    if rc != 0:
        return "fail", f"录制失败（退出码 {rc}）"
    if not new_videos:
        # 退出码 0 但无产出 —— 最容易误报「成功」的情况
        return "fail", ("录制返回成功但没有产出（该选手本场无击杀 / demo 未 analyze 入库 / "
                        "Steam 未登录 / HLAE 未配置）")
    log(f"      已生成 {len(new_videos)} 个分段视频")

    # 5. 叠键盘 + 合并
    if need_keys and key_json and key_json.exists():
        phase("叠键盘")
        log("[5/5] 叠加键盘可视化 + 合并成品...")
        q = resolve_quality(cfg)
        batch_dir = _link_batch(new_videos, out_dir)
        rc = run_step([
            sys.executable, str(ROOT / "src" / "killviz_compose.py"),
            "--json", str(key_json),
            "--dir", str(batch_dir),
            "--out", str(out_dir),
            "--fps", str(q["fps"]),
            "--pos", cfg.get("key_pos", "bottom-left-high"),
            "--scale", str(cfg.get("key_scale", 0)),        # 0 = 按分辨率自适应
            "--opacity", str(cfg.get("key_opacity", 0.85)),
            "--dead-fade", str(cfg.get("dead_fade", 0.3)),
            "--supersample", str(cfg.get("supersample", 2)),
            "--blend-format", str(cfg.get("blend_format", "rgb")),
            "--preset", str(cfg.get("overlay_preset", "veryfast")),
            "--crf", str(cfg.get("overlay_crf", 18)),
            "--concat",
            "--name", final_name,
        ], "      逐段叠加键盘并合并...")
        final_path = out_dir / final_name
        ok = rc == 0 and final_path.exists()
        shutil.rmtree(batch_dir, ignore_errors=True)
        if ok:
            log("      键盘可视化已叠加并合并（%d 段 -> 1 个成品）" % len(new_videos))
        else:
            log("[!] 键盘叠加/合并失败，保留无键盘的分段原视频")
            return "partial", "键盘叠加/合并失败（已保留无键盘分段）"
    else:
        phase("合并")
        log("      合并分段成品...")
        if not merge_dir_videos(out_dir, final_name, new_videos):
            log("[!] 合并失败，保留分段原视频")
            return "partial", "合并失败（已保留分段）"
        log("      已合并为 1 个成品")

    phase("完成")
    return "ok", ""


# ───────────────────────── 路由 ─────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/config")
def get_config():
    try:
        plugin = plug.check_plugin()
    except Exception as e:  # noqa: BLE001
        plugin = {"ok": None, "reason": f"自检异常：{e}"}
    return jsonify({
        "version": APP_VERSION,
        "default_dirs": DEFAULT_DIRS,
        "default_out_dir": str(DEFAULT_OUT_DIR),
        "prefs": load_prefs(),          # 上次的选择（demo 目录 / 输出位置 / 全部参数）
        "csdm_found": find_csdm() is not None,
        "csdm_path": find_csdm(),
        "presets": [{"id": k, **v} for k, v in QUALITY_PRESETS.items()],
        "default_preset": DEFAULT_PRESET,
        "overlay_presets": [
            {"id": "veryfast", "label": "速度优先（推荐，叠加快 2.2 倍，成片约 +8% 体积）"},
            {"id": "medium", "label": "体积优先（叠加慢 2.2 倍，成片小约 46MB）"},
            {"id": "fast", "label": "均衡"},
        ],
        "default_overlay_preset": DEFAULT_OVERLAY_PRESET,
        "plugin": plugin,
    })


@app.route("/api/prefs", methods=["GET", "POST"])
def api_prefs():
    """GET 读回上次的选择；POST 增量保存（只覆盖传来的键）。"""
    if request.method == "GET":
        return jsonify(load_prefs())
    try:
        return jsonify(save_prefs(request.json or {}))
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/plugin/check")
def api_plugin_check():
    """只查不改：CS2 插件状态。"""
    try:
        return jsonify(plug.check_plugin())
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "reason": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/plugin/repair", methods=["POST"])
def api_plugin_repair():
    """重装 CS2 插件（CS2 更新后必需）。"""
    try:
        res = plug.install_plugin()
        return jsonify(res)
    except Exception as e:  # noqa: BLE001
        detail = traceback.format_exc(limit=3)
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}",
                        "trace": detail}), 500


@app.route("/api/demos")
def api_demos():
    directory = request.args.get("dir", DEFAULT_DIRS[0])
    return jsonify({"dir": directory, "demos": scan_demos(directory)})


@app.route("/api/players", methods=["POST"])
def api_players():
    demo = request.json.get("demo", "")
    if not demo or not os.path.exists(demo):
        return jsonify({"error": "demo 文件不存在"}), 400
    try:
        players = list_players(demo)
        return jsonify({"players": players})
    except Exception as e:
        app.logger.exception("list_players 失败: %s", demo)
        detail = traceback.format_exc(limit=3)
        sys.stderr.write(detail)
        return jsonify({"error": f"解析失败：{e}", "trace": detail}), 500


@app.route("/api/kills", methods=["POST"])
def api_kills():
    demo = request.json.get("demo", "")
    steamid = request.json.get("steamid", "")
    try:
        kills = list_kills(demo, steamid)
        return jsonify({"kills": kills, "count": len(kills)})
    except Exception as e:
        app.logger.exception("list_kills 失败: %s", demo)
        detail = traceback.format_exc(limit=3)
        sys.stderr.write(detail)
        return jsonify({"error": f"解析失败：{e}", "trace": detail}), 500


@app.route("/api/record", methods=["POST"])
def api_record():
    """单场录制：一个 demo + 一个选手 → 一条成品。"""
    cfg = request.json
    csdm = find_csdm()
    if not csdm:
        return jsonify({
            "error": "未检测到 CS Demo Manager。请先安装（需要 PostgreSQL + CSDM + HLAE）。",
            "csdm_missing": True,
        }), 400

    need_keys = cfg.get("mode", "full") in ("keys", "full")
    out_dir = Path(cfg.get("out_dir") or DEFAULT_OUT_DIR)
    name_default = "keyviz_final.mp4" if need_keys else "final.mp4"
    final_name = sanitize_out_name(cfg.get("out_name"), name_default)
    cmd = build_csdm_cmd(csdm, cfg)
    cfg_cmds = build_cfg_commands(cfg)

    task_id = uuid.uuid4().hex[:12]
    with TASKS_LOCK:
        TASKS[task_id] = {
            "id": task_id, "status": "running", "kind": "single",
            "cmd": " ".join(cmd), "cfg": cfg_cmds,
            "log": [], "started": time.time(),
            "output_dir": str(out_dir), "final_name": final_name,
        }

    def run():
        task = TASKS[task_id]
        log = make_logger(task["log"])
        try:
            if cfg.get("out_name") and final_name != cfg["out_name"].strip():
                log(f"[!] 输出名已规范化为：{final_name}")
            if sanitize_name_part(final_name, "").lower().startswith("sequence"):
                log("[!] 输出名以 sequence 开头，可能与分段命名冲突，建议改一个。")
            status, reason = record_one(
                csdm, cfg, cfg["demo"], str(cfg["steamid"]), None,
                out_dir, final_name, log, need_keys)
            if status == "fail":
                task["status"] = "error"
                log(f"[X] {reason}")
                return
            if status == "partial":
                log(f"[!] {reason}")
            task["status"] = "done"
            task["videos"] = [str(out_dir / final_name)]
            log("")
            log(f"[OK] 完成！输出目录：{out_dir}")
            log(f"     {final_name}")
        except Exception as e:  # noqa: BLE001
            task["status"] = "error"
            log(f"[X] 异常：{type(e).__name__}: {e}")

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"task_id": task_id, "cmd": " ".join(cmd), "cfg": cfg_cmds})


@app.route("/api/batch", methods=["POST"])
def api_batch():
    """批量录制：把一个目录里的所有 demo 依次录成「前缀-NN.mp4」。

    - 串行执行（同一时间只允许一个 CS2）
    - 每个 demo 用独立工作子目录（分段名 sequence-N-tick-A-B 会跨 demo 撞名）
    - 成品默认跳过已存在的（断点续录），分段成功后自动删
    """
    cfg = request.json or {}
    csdm = find_csdm()
    if not csdm:
        return jsonify({
            "error": "未检测到 CS Demo Manager。请先安装（需要 PostgreSQL + CSDM + HLAE）。",
            "csdm_missing": True,
        }), 400

    demo_dir = (cfg.get("batch_dir") or "").strip()
    if not demo_dir or not os.path.isdir(demo_dir):
        return jsonify({"error": f"demo 目录不存在：{demo_dir}"}), 400

    # 编号必须和「排序后的 demo 列表」一一对应，断点续录时编号才稳定。
    # 按文件名升序（不用 scan_demos 的 mtime 倒序），保证多次运行顺序一致。
    demos = sorted((d["path"] for d in scan_demos(demo_dir)),
                   key=lambda p: Path(p).name.lower())
    if not demos:
        return jsonify({"error": f"该目录没有 .dem 文件：{demo_dir}"}), 400

    player_spec = (cfg.get("batch_player") or "").strip()
    if not player_spec:
        return jsonify({"error": "请先填要录的选手（昵称或 SteamID）"}), 400

    prefix = sanitize_name_part(cfg.get("out_name"), "clip")
    out_root = Path(cfg.get("out_dir") or DEFAULT_OUT_DIR)
    out_root.mkdir(parents=True, exist_ok=True)
    need_keys = cfg.get("mode", "full") in ("keys", "full")
    width = max(2, len(str(len(demos))))

    task_id = uuid.uuid4().hex[:12]
    with TASKS_LOCK:
        TASKS[task_id] = {
            "id": task_id, "status": "running", "kind": "batch",
            "cmd": "", "cfg": build_cfg_commands(cfg),
            "log": [], "started": time.time(), "output_dir": str(out_root),
            "batch": {
                "total": len(demos), "index": 0, "current": "", "phase": "准备",
                "done": 0, "ok": 0, "partial": 0, "skipped": 0, "failed": 0,
                "prefix": prefix, "out_root": str(out_root),
                "results": [], "eta_seconds": None,
            },
        }

    def run():
        task = TASKS[task_id]
        b = task["batch"]
        log = make_logger(task["log"], cap=4000)

        def on_phase(p: str) -> None:
            b["phase"] = p

        def push(i, demo, status, reason, seconds, out_name=""):
            b["results"].append({
                "index": i, "demo": Path(demo).name, "status": status,
                "reason": reason, "seconds": round(seconds, 1), "output": out_name,
            })

        try:
            err = preflight()
            if err:
                task["status"] = "error"
                b["phase"] = "预检失败"
                log(f"[X] {err}")
                return

            log(f"批量开始：{len(demos)} 个 demo，前缀「{prefix}」，输出到 {out_root}")
            tag = "（SteamID）" if looks_like_steamid(player_spec) else "（昵称，将逐场解析）"
            log(f"选手：{player_spec}{tag}")
            log("")

            steamid = player_spec if looks_like_steamid(player_spec) else None
            player_name = None
            locked = steamid is not None
            durations: list[float] = []

            for i, demo in enumerate(demos, start=1):
                nn = str(i).zfill(width)
                final_name = f"{prefix}-{nn}.mp4"
                target = out_root / final_name
                b["index"] = i
                b["current"] = Path(demo).name
                b["phase"] = "准备"
                t0 = time.time()

                if target.exists() and not cfg.get("batch_overwrite"):
                    b["done"] += 1
                    b["skipped"] += 1
                    push(i, demo, "skip", "成品已存在（断点续录跳过）", 0, final_name)
                    log(f"===== [{i}/{len(demos)}] {Path(demo).name} → {final_name} 已存在，跳过 =====")
                    continue

                log(f"===== [{i}/{len(demos)}] {Path(demo).name} → {final_name} =====")

                # 选手解析：昵称模式下，第一场解到谁就锁定它的 SteamID 给后面复用
                if steamid is None:
                    sid, nm = resolve_player(demo, player_spec)
                    if not sid:
                        b["done"] += 1
                        b["failed"] += 1
                        push(i, demo, "fail", f"本 demo 里没有「{player_spec}」", time.time() - t0)
                        log(f"[X] 本 demo 里没有「{player_spec}」，跳过")
                        continue
                    steamid, player_name = sid, nm
                    if not locked:
                        locked = True
                        log(f"      已锁定选手 SteamID {steamid}（{player_name}），后续 demo 沿用")
                else:
                    _, player_name = resolve_player(demo, str(steamid))
                    if player_name is None:
                        b["done"] += 1
                        b["failed"] += 1
                        push(i, demo, "fail", "本 demo 里没有该 SteamID", time.time() - t0)
                        log("[X] 本 demo 里没有该 SteamID，跳过")
                        continue

                # 每个 demo 一个独立工作目录：CSDM 分段名是 sequence-N-tick-A-B，
                # 序号每场从 1 重启 → 共用目录会互相覆盖且**不报错**。
                parts_dir = out_root / PARTS_DIRNAME / f"{prefix}-{nn}"
                parts_dir.mkdir(parents=True, exist_ok=True)

                status, reason = record_one(csdm, cfg, demo, str(steamid), player_name,
                                            parts_dir, final_name, log, need_keys, on_phase)

                dt = time.time() - t0
                if status in ("ok", "partial"):
                    produced = parts_dir / final_name
                    if produced.exists():
                        try:
                            os.replace(produced, target)        # 同盘移动，瞬时
                        except OSError as e:
                            status, reason = "fail", f"成品移动失败：{e}"

                if status == "ok":
                    b["ok"] += 1
                    push(i, demo, "ok", "", dt, final_name)
                    shutil.rmtree(parts_dir, ignore_errors=True)   # 分段用完即删
                    durations.append(dt)
                    log(f"[OK] {final_name}（{dt:.0f}s）")
                elif status == "partial":
                    b["partial"] += 1
                    # partial = 叠键盘/合并失败，成品可能**根本没生成**（分段还留在
                    # _parts/）。只有成品真的存在才把名字报给前端，否则别列一个
                    # 点开找不到的文件。
                    push(i, demo, "partial", reason, dt,
                         final_name if target.exists() else "")
                    durations.append(dt)
                    log(f"[!] {final_name} 部分成功：{reason}")
                else:
                    b["failed"] += 1
                    push(i, demo, "fail", reason, dt)
                    log(f"[X] 失败：{reason}")
                    log(f"    中途产物保留在 {parts_dir}（可人工检查）")

                b["done"] += 1
                if durations:
                    left = len(demos) - b["done"]
                    b["eta_seconds"] = int(sum(durations) / len(durations) * left)

                # 等 CS2 退干净再开下一个，否则 HLAE 挂钩失败 → 语焉不详的 Game error
                if i < len(demos) and not wait_cs2_exit(log=log):
                    log("[!] cs2.exe 长时间未退出，后面几个可能失败")

            used = time.time() - task["started"]
            b["phase"] = "全部结束"
            log("")
            log("========== 批量汇总 ==========")
            log(f"共 {len(demos)} 个：成功 {b['ok']}，部分成功 {b['partial']}，"
                f"跳过 {b['skipped']}，失败 {b['failed']}")
            log(f"总耗时 {used / 60:.1f} 分钟")
            for r in b["results"]:
                if r["status"] != "ok":
                    tag = {"skip": "跳过", "fail": "失败", "partial": "部分"}.get(r["status"], r["status"])
                    log(f"  [{tag}] {r['output'] or r['demo']} —— {r['reason']}")
            log(f"输出目录：{out_root}")
            task["status"] = "done"
            task["videos"] = [str(out_root / r["output"]) for r in b["results"]
                              if r["status"] in ("ok", "partial") and r["output"]]
        except Exception as e:  # noqa: BLE001
            task["status"] = "error"
            log(f"[X] 批量异常：{type(e).__name__}: {e}")

    threading.Thread(target=run, daemon=True).start()
    return jsonify({
        "task_id": task_id, "total": len(demos), "prefix": prefix,
        "out_root": str(out_root),
        "names": [f"{prefix}-{str(i).zfill(width)}.mp4" for i in range(1, len(demos) + 1)],
    })


@app.route("/api/open-dir", methods=["POST"])
def api_open_dir():
    """在资源管理器里打开目录。"""
    d = (request.json or {}).get("dir", "")
    if not d or not os.path.isdir(d):
        return jsonify({"error": f"目录不存在：{d}"}), 400
    try:
        os.startfile(d)                     # Windows 专用
        return jsonify({"ok": True, "dir": d})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/task/<task_id>")
def api_task(task_id):
    with TASKS_LOCK:
        task = TASKS.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify({
        "id": task["id"],
        "status": task["status"],
        "kind": task.get("kind", "single"),
        "cmd": task["cmd"],
        "cfg": task["cfg"],
        "log": task["log"],
        "output_dir": task.get("output_dir"),
        "videos": task.get("videos", []),
        "final_name": task.get("final_name"),
        "batch": task.get("batch"),
        "elapsed": round(time.time() - task["started"], 1),
    })


def _open_browser(port: int, delay: float = 1.2) -> None:
    """服务起来后自动用系统默认浏览器打开独立窗口（不是内嵌预览面板）。"""
    import webbrowser

    time.sleep(delay)
    url = f"http://127.0.0.1:{port}/"
    try:
        # 优先用 Chrome / Edge 的 --new-window 开独立窗口
        for exe in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ):
            if os.path.exists(exe):
                subprocess.Popen([exe, f"--new-window", url])
                return
        # 兜底：系统默认浏览器
        webbrowser.open(url, new=1)
    except Exception:
        try:
            webbrowser.open(url, new=1)
        except Exception:
            pass


def _kill_port_holder(port: int = 5000) -> None:
    """启动前清掉占用该端口的旧实例。

    ⚠️ 这个坑很隐蔽：上次跑的服务没退，这次启动会 Bind 失败（或更糟——
    旧进程继续服务，用户看到的是**旧代码**的页面，改了半天不生效还以为改错了）。
    """
    try:
        r = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                           capture_output=True, text=True, errors="replace")
    except Exception:
        return
    pids = set()
    for ln in r.stdout.splitlines():
        if f":{port} " in ln and "LISTENING" in ln:
            pids.add(ln.split()[-1])
    for pid in pids:
        if pid.isdigit() and int(pid) != os.getpid():
            subprocess.run(["taskkill", "/F", "/PID", pid],
                           capture_output=True, text=True)
            print(f"[*] 已停掉占用 {port} 端口的旧实例（PID {pid}）")


if __name__ == "__main__":
    _no_browser = "--no-browser" in sys.argv
    # 可选 --port N：方便自动化测试起在临时端口，不去碰用户正在用的 5000 实例。
    _port = 5000
    for _i, _a in enumerate(sys.argv):
        if _a == "--port" and _i + 1 < len(sys.argv):
            try:
                _port = int(sys.argv[_i + 1])
            except ValueError:
                pass
        elif _a.startswith("--port="):
            try:
                _port = int(_a.split("=", 1)[1])
            except ValueError:
                pass
    if "--no-kill" not in sys.argv:
        _kill_port_holder(_port)
    if not _no_browser:
        print("[*] 服务启动后会自动打开浏览器窗口…")
        threading.Thread(target=_open_browser, args=(_port,), daemon=True).start()
    print(f"[*] CS2 击杀集锦录制工作台 -> http://127.0.0.1:{_port}/")
    app.run(host="127.0.0.1", port=_port, debug=False, threaded=True)
