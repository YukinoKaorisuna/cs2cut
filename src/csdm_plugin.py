# -*- coding: utf-8 -*-
"""CSDM CS2 插件自检与重装 —— 解决「CS2 更新后 Game error」。

## 为什么需要这个

CS2 每次大版本更新（Steam 覆盖安装）会**重置 `game/csgo/gameinfo.gi`**，
并把 CSDM 装进游戏目录的插件一起清掉。插件不在了，HLAE 挂钩后 CS2 会秒退，
CSDM 只报一个语焉不详的 `Game error`（rc 仍是 0），完全看不出是这个原因。

实测（2026-10-01）：CS2 更新到 1.41.8.8 后
- `game/csgo/csdm/bin/server.dll` 没了
- `gameinfo.gi` 里的 `Game csgo/csdm` 注入行没了
- 录制立刻变成 `Game error`

## CDSM 官方机制（拆 resources/app.asar 的 cli.js 得到）

装插件 `mTe()`：
```
1. 目标目录 = <CS2根>/game/csgo/csdm/bin        (Windows)
2. 删掉 <CS2根>/game/bin/win64/csdm.log          (清旧日志，可选)
3. 源文件   = <CSDM安装>/resources/static/cs2/server.dll
              （若配了 cs2PluginVersion=14030 之类则用 server_14030.dll）
4. 拷成     = <CS2根>/game/csgo/csdm/bin/server.dll
5. 改 gameinfo.gi：在 `Game\tcsgo` 前插入一行 `Game\tcsgo/csdm`
   （已含则跳过；改前先存 gameinfo.gi.backup）
```

插件版本表：`{14030,14088,14094,14103,14112,14152,14168, latest}`
—— **不在表里的 CS2 版本必须用 latest**。

卸插件 `HTe()`：删 `game/csgo/csdm` 目录 + 用 `.backup` 还原 gameinfo.gi。
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

# CS2 可能的安装根目录（含 game/csgo 的那层）
CS2_ROOTS = [
    r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive",
    r"C:\Program Files\Steam\steamapps\common\Counter-Strike Global Offensive",
    r"D:\Steam\steamapps\common\Counter-Strike Global Offensive",
    r"E:\Steam\steamapps\common\Counter-Strike Global Offensive",
    r"D:\SteamLibrary\steamapps\common\Counter-Strike Global Offensive",
    r"E:\SteamLibrary\steamapps\common\Counter-Strike Global Offensive",
]

# CSDM 安装目录（含 resources/static 的那层）
# 用环境变量拼，避免写死某台机器的 Windows 用户名。
def _csdm_installs() -> list[str]:
    local = os.environ.get("LOCALAPPDATA", "")
    roam = os.environ.get("APPDATA", "")
    out: list[str] = []
    for base in (local, roam):
        if not base:
            continue
        for rel in ("Programs\\cs-demo-manager", "Programs\\CS Demo Manager",
                    "cs-demo-manager"):
            out.append(os.path.join(base, rel))
    return out


CSDM_INSTALLS = _csdm_installs()

# CSDM 能认的插件版本（不在表里的 CS2 版本要用 latest = server.dll）
PLUGIN_VERSIONS = ["14030", "14088", "14094", "14103", "14112", "14152", "14168"]

# gameinfo.gi 里要注入的标记
INJECT_NEEDLE = "Game csgo/csdm"


def find_cs2_root() -> Path | None:
    """定位 CS2 根目录（里面应有 game/csgo）。"""
    for p in CS2_ROOTS:
        root = Path(p)
        if (root / "game" / "csgo" / "gameinfo.gi").exists():
            return root
    return None


def find_csdm_install() -> Path | None:
    """定位 CSDM 安装目录（里面应有 resources/static/cs2）。"""
    for p in CSDM_INSTALLS:
        inst = Path(p)
        if (inst / "resources" / "static" / "cs2").is_dir():
            return inst
    return None


def plugin_path(cs2_root: Path) -> Path:
    """CSDM 插件在游戏里的落点。"""
    return cs2_root / "game" / "csgo" / "csdm" / "bin" / "server.dll"


def gameinfo_path(cs2_root: Path) -> Path:
    return cs2_root / "game" / "csgo" / "gameinfo.gi"


def read_cs2_version(cs2_root: Path) -> dict:
    """读 CS2 版本信息（steam.inf）。"""
    info = {}
    f = cs2_root / "game" / "csgo" / "steam.inf"
    if f.exists():
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in ln:
                k, _, v = ln.partition("=")
                info[k.strip()] = v.strip()
    return info


def check_plugin(cs2_root: Path | None = None) -> dict:
    """自检：插件 dll 在不在？gameinfo.gi 注入了没？CS2 什么版本？

    返回 dict：
      ok            整体是否可用（两个条件都满足）
      cs2_root      CS2 根目录
      plugin_exists server.dll 是否存在
      gameinfo_patched gameinfo.gi 是否含 csgo/csdm
      version       steam.inf 解析出的版本信息
      reason        不 ok 时的可读原因
    """
    root = cs2_root or find_cs2_root()
    if not root:
        return {
            "ok": False, "cs2_root": None,
            "plugin_exists": False, "gameinfo_patched": False,
            "version": {}, "reason": "找不到 CS2 安装目录（找不到 game/csgo/gameinfo.gi）",
        }

    dll = plugin_path(root)
    gin = gameinfo_path(root)
    plugin_exists = dll.exists()
    gin_text = gin.read_bytes().decode("utf-8", errors="replace") if gin.exists() else ""
    # gameinfo.gi 用制表符分隔，两种写法都认（宽松匹配，避免误判）
    patched = ("Game\tcsgo/csdm" in gin_text) or ("Game csgo/csdm" in gin_text)

    ver = read_cs2_version(root)
    reasons = []
    if not plugin_exists:
        reasons.append(f"插件缺失：{dll}")
    if not patched:
        reasons.append("gameinfo.gi 未注入 csgo/csdm（CS2 更新后会被重置）")

    return {
        "ok": plugin_exists and patched,
        "cs2_root": str(root),
        "plugin_exists": plugin_exists,
        "plugin_path": str(dll),
        "gameinfo_patched": patched,
        "version": ver,
        "reason": "；".join(reasons),
    }


def _pick_plugin_source(csdm_install: Path, cs2_version: str = "") -> Path:
    """挑要装的插件源文件。

    CSDM 逻辑：配了 cs2PluginVersion 且不是 latest 就用 server_<ver>.dll，
    否则 server.dll。我们没有那个配置读入口，所以：
      能匹配到具体版本就用对应文件，否则回退 latest（server.dll）。
    """
    static_cs2 = csdm_install / "resources" / "static" / "cs2"
    # 从 PatchVersion 1.41.8.8 里抽版本段（去点）
    digits = "".join(ch for ch in cs2_version if ch.isdigit())
    for v in PLUGIN_VERSIONS:
        if v in digits:
            cand = static_cs2 / f"server_{v}.dll"
            if cand.exists():
                return cand
    return static_cs2 / "server.dll"


def install_plugin(cs2_root: Path | None = None, csdm_install: Path | None = None) -> dict:
    """重装 CS2 插件（照搬 CSDM mTe() 的逻辑）。

    返回 {ok, steps:[...], error}
    """
    steps: list[str] = []
    root = cs2_root or find_cs2_root()
    inst = csdm_install or find_csdm_install()
    if not root:
        return {"ok": False, "steps": steps, "error": "找不到 CS2 安装目录"}
    if not inst:
        return {"ok": False, "steps": steps, "error": "找不到 CSDM 安装目录（缺 resources/static/cs2）"}

    ver = read_cs2_version(root)
    src = _pick_plugin_source(inst, ver.get("PatchVersion", ""))
    steps.append(f"CS2 版本: {ver.get('PatchVersion', '?')} (build {ver.get('ClientVersion', '?')})")
    steps.append(f"插件源: {src.name}")

    if not src.exists():
        return {"ok": False, "steps": steps, "error": f"插件源文件不存在：{src}"}

    try:
        # 1) 建目录 + 拷 dll
        dst_dir = root / "game" / "csgo" / "csdm" / "bin"
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / "server.dll"
        # 备份已有（万一想回滚）
        if dst.exists():
            shutil.copy2(dst, dst.with_suffix(".dll.bak"))
            steps.append(f"已备份旧插件 -> {dst.with_suffix('.dll.bak').name}")
        shutil.copy2(src, dst)
        steps.append(f"已安装插件 -> {dst}")

        # 2) 清掉旧的 game/bin/win64/csdm.log（CSDM 也会做，避免旧日志混淆）
        old_log = root / "game" / "bin" / "win64" / "csdm.log"
        if old_log.exists():
            try:
                old_log.rename(old_log.with_suffix(".log.old"))
                steps.append("已归档旧的 bin/win64/csdm.log")
            except OSError:
                pass

        # 3) 注入 gameinfo.gi
        gin = gameinfo_path(root)
        if not gin.exists():
            return {"ok": False, "steps": steps, "error": f"gameinfo.gi 不存在：{gin}"}
        # ⚠️ 三个换行坑，一次性说明白：
        #   1) read_text() 默认 universal newlines，会把原文 CRLF 归一并成 LF；
        #      我们再 write_text(newline="") 写回，整份文件的行尾就全变 LF 了
        #      （和 CS2 原生 CRLF 不一致）。→ 必须用 read_bytes().decode() 保原文。
        #   2) 替换串里如果自己带 "\r\n"，但插入位置原本就没有换行，
        #      会少/多换行。→ 只在「新行」和「原行」之间插一个 \r\n，其余不动。
        #   3) 拼完后可能残留 "\r\r\n"。→ 做一次幂等归一。
        text = gin.read_bytes().decode("utf-8", errors="replace")
        import re as _re
        if _re.search(r"Game[ \t]+csgo/csdm", text):
            steps.append("gameinfo.gi 已含 csgo/csdm，跳过注入")
        else:
            # 先备份
            bak = Path(str(gin) + ".backup")
            if not bak.exists():
                shutil.copy2(gin, bak)
                steps.append(f"已备份 gameinfo.gi -> {bak.name}")

            indent = "\t\t\t"
            new, n = _re.subn(
                r"Game[ \t]+csgo",
                "Game\tcsgo/csdm\r\n" + indent + "Game\tcsgo",
                text, count=1,
            )
            if n == 0:
                return {"ok": False, "steps": steps,
                        "error": "gameinfo.gi 里找不到 `Game csgo` 行，无法注入"}
            # 幂等归一：\r\r\n -> \r\n；顺带把孤立的 \r\n\r\n 之类的重复收掉
            new = new.replace("\r\r\n", "\r\n")
            gin.write_bytes(new.encode("utf-8"))
            steps.append("已注入 `Game csgo/csdm` 到 gameinfo.gi")

        # 4) 复核
        after = check_plugin(root)
        steps.append("复核：" + ("通过 ✓" if after["ok"] else "仍不通过 ✗ " + after["reason"]))
        return {"ok": after["ok"], "steps": steps, "error": None if after["ok"] else after["reason"],
                "check": after}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "steps": steps, "error": f"{type(e).__name__}: {e}"}


def ensure_plugin(auto_install: bool = True) -> dict:
    """录制前调用：检查插件，缺了就（可选）自动重装。

    返回 {ok, installed, check, steps, error}
    """
    chk = check_plugin()
    if chk["ok"]:
        return {"ok": True, "installed": False, "check": chk, "steps": [], "error": None}
    if not auto_install:
        return {"ok": False, "installed": False, "check": chk, "steps": [],
                "error": chk["reason"]}

    # 要不要装：只有「找到了 CS2 目录」才尝试，且加一点延迟避免和游戏进程抢文件
    res = install_plugin()
    res["installed"] = True
    res["check"] = res.get("check") or check_plugin()
    if not res["ok"]:
        res["error"] = (res.get("error") or "重装后自检仍未通过")
    return res
