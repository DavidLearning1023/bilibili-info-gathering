#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-info-gathering 一键安装：建环境 + 装依赖 + 注册 MCP。

    python scripts/install_mcp.py                 # 自动检测客户端，装好并注册
    python scripts/install_mcp.py --client minimax  # 指定客户端
    python scripts/install_mcp.py --client all      # 全部已检测到的客户端
    python scripts/install_mcp.py --print           # 只打印配置 JSON，不动任何文件
    python scripts/install_mcp.py --check           # 只看现状，不改
    python scripts/install_mcp.py --link-skill     # 顺便把本目录做成 skills 目录的 junction

脚本做四件事，每一步都可以单独失败而不毁掉已有状态：
  1. 建 venv（本项目目录下的 .venv）
  2. pip install -e ".[images]"
  3. 把 MCP server 写进客户端配置（**先备份**，同名条目覆盖不重复）
  4. 跑一遍 check_setup.py 自检

设计约束
--------
* **输出字符必须 GBK 可编码**（中文 + ASCII 标点）：Windows 下 stdout 被管道接走时
  用系统 ANSI 代码页，打印 emoji / 非 GBK 符号会抛 UnicodeEncodeError，
  脚本以 exit 1 结束——看起来像"安装失败"，其实是编码问题。
* **不静默破坏已有配置**：改配置前先备份成 `mcp.json.bak-<时间戳>`。
* 退出码只反映安装结果，不被编码问题或输出异常污染。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL_NAME = "bilibili-info-gathering"
SERVER_NAME = "bilibili-info-gathering"
DESCRIPTION = "B站议题级信息获取 MCP（广扫/深挖/资产库，14 工具）——本仓库自带的 stdio server"

# MCP 客户端配置里 mcpServers 条目的位置与写入方式。
CLIENTS = {
    "minimax": {
        "label": "MiniMax Code",
        "config": lambda: pathlib.Path.home() / ".minimax" / "mcp.json",
        "skills": lambda: pathlib.Path.home() / ".minimax" / "skills",
        "note": "写好配置后重启客户端即可",
    },
    "workbuddy": {
        "label": "WorkBuddy",
        "config": lambda: pathlib.Path.home() / ".workbuddy" / "mcp.json",
        "skills": lambda: pathlib.Path.home() / ".workbuddy" / "skills",
        "note": "连接器管理页 -> 右上角「自定义连接器」-> 找到它 -> 点「信任」",
    },
    "claude": {
        "label": "Claude Code",
        "config": lambda: pathlib.Path.home() / ".claude.json",
        "skills": lambda: pathlib.Path.home() / ".claude" / "skills",
        "note": "写好配置后重启 Claude Code；也可用 `claude mcp add` 代替本脚本",
    },
}

# 客户端可能用别的键名存 mcpServers，这里按优先级试。
SERVERS_KEYS = ("mcpServers", "mcp_servers")


# ------------------------------------------------------------------ 输出底座

def _fix_stdout_encoding() -> None:
    """管道 / 重定向下强制 UTF-8（消费方几乎都按 UTF-8 读）。"""
    if getattr(sys.stdout, "isatty", lambda: False)():
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


def out(line: str = "") -> None:
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        data = line.encode(enc)
    except UnicodeEncodeError:
        data = line.encode(enc, "replace")
    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        buf.write(data + b"\n")
        buf.flush()
    else:
        print(line)


FAILS: list[str] = []


def step(msg: str) -> None:
    out(f"[--] {msg}")


def ok(msg: str) -> None:
    out(f"[OK] {msg}")


def warn(msg: str) -> None:
    out(f"[WARN] {msg}")


def fail(msg: str) -> None:
    FAILS.append(msg)
    out(f"[FAIL] {msg}")


# ------------------------------------------------------------------ 路径

def venv_python() -> pathlib.Path:
    if os.name == "nt":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def venv_dir() -> pathlib.Path:
    return ROOT / ".venv"


def mcp_entry(python: pathlib.Path) -> dict:
    """生成 MCP server 配置条目。

    env 里那五个变量不是可选项：
    * BIG_OUT_DIR / BIG_REGISTRY_DIR —— 客户端的 stdio server 未必带 cwd，
      而 big.media.OUT_ROOT 默认值依赖 cwd，不钉死就会把图片写到别处。
    * PYTHONPATH —— editable 安装坏掉时仍能 import big。
    * PYTHONIOENCODING / PYTHONUNBUFFERED —— Windows 管道下的编码与缓冲坑。
    """
    return {
        "type": "stdio",
        "enabled": True,
        "configured": True,
        "builtin": False,
        # 600s 而不是 300s：1080p 抽帧要下整条视频流。实测 2026-10-21 一条 15 分钟
        # 视频 1080p 流 667 MB / 431 秒（本机 1.5 MB/s）——300s 会让**客户端先超时**，
        # 服务端还在下载，用户看到的是"卡住"，而实际是 timeout 太短。
        "timeout": 600000,
        "description": DESCRIPTION,
        "command": str(python),
        "args": ["-m", "big.mcp_server"],
        "env": {
            "BIG_OUT_DIR": str(ROOT / ".bigout"),
            "BIG_REGISTRY_DIR": str(ROOT / ".registry"),
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        },
    }


# ------------------------------------------------------------------ venv

def find_base_python() -> str:
    """找一个 >= 3.10 的解释器来建 venv。"""
    for cand in ([sys.executable] if sys.executable else []) + \
                (shutil.which("python3.12"), shutil.which("python3.11"),
                 shutil.which("python3"), shutil.which("python")):
        if not cand:
            continue
        try:
            r = subprocess.run([cand, "-c", "import sys;print(sys.version_info[:2])"],
                               capture_output=True, encoding="utf-8", errors="replace", timeout=30)
            maj, mino = (int(x) for x in r.stdout.strip().split("."))
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        if (maj, mino) >= (3, 10):
            return cand
    return sys.executable


def ensure_venv() -> pathlib.Path | None:
    step("准备 Python 环境")
    vd, py = venv_dir(), venv_python()
    if py.exists():
        ok(f"venv 已存在：{vd}")
        return py
    base = find_base_python()
    try:
        r = subprocess.run([base, "-m", "venv", str(vd)],
                           capture_output=True, encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        fail(f"创建 venv 失败：{e}")
        return None
    if r.returncode != 0 or not py.exists():
        fail(f"创建 venv 失败：{(r.stderr or r.stdout or '').strip()[-300:]}")
        return None
    ok(f"已创建 venv：{vd}")
    return py


def install_deps(py: pathlib.Path) -> bool:
    step("安装依赖（pip install -e \".[images]\"）")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        r = subprocess.run([str(py), "-m", "pip", "install", "-e", ".[images]"],
                           cwd=str(ROOT), env=env, capture_output=True, encoding="utf-8", errors="replace",
                           timeout=900)
    except (OSError, subprocess.SubprocessError) as e:
        fail(f"安装依赖失败：{e}")
        return False
    if r.returncode != 0:
        fail(f"安装依赖失败：{(r.stderr or r.stdout or '').strip()[-400:]}")
        return False
    ok("依赖安装完成")
    return True


# ------------------------------------------------------------------ MCP 配置

def read_config(path: pathlib.Path) -> tuple[dict, str]:
    """返回 (配置, mcpServers 所在的键名)。文件不存在返回空配置。"""
    if not path.exists():
        return {}, SERVERS_KEYS[0]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {}, SERVERS_KEYS[0]
    if not isinstance(data, dict):
        return {}, SERVERS_KEYS[0]
    for k in SERVERS_KEYS:
        if isinstance(data.get(k), dict):
            return data, k
    return data, SERVERS_KEYS[0]


def backup(path: pathlib.Path) -> pathlib.Path | None:
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = path.with_name(f"{path.name}.bak-{stamp}")
    try:
        shutil.copy2(path, bak)
    except OSError as e:
        warn(f"备份失败（继续，但改动不可回退）：{e}")
        return None
    return bak


def register_mcp(client_key: str, py: pathlib.Path) -> bool:
    spec = CLIENTS[client_key]
    cfg_path: pathlib.Path = spec["config"]()
    step(f"注册 MCP 到 {spec['label']}（{cfg_path}）")

    data, key = read_config(cfg_path)
    servers = data.get(key)
    if not isinstance(servers, dict):
        servers = {}
        data[key] = servers
    existed = SERVER_NAME in servers
    servers[SERVER_NAME] = mcp_entry(py)

    # 幂等：内容没变就不重写、不备份（否则每次跑都留一个 .bak，噪音掩盖真问题）
    new_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if cfg_path.exists():
        try:
            if cfg_path.read_text(encoding="utf-8") == new_text:
                ok(f"配置已是最新，未改动（`{SERVER_NAME}`）")
                return True
        except OSError:
            pass

    bak = backup(cfg_path)
    if bak:
        ok(f"原配置已备份：{bak.name}")
    try:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(new_text, encoding="utf-8")
    except OSError as e:
        fail(f"写入 {cfg_path} 失败：{e}")
        return False
    ok(f"{'已更新' if existed else '已新增'} MCP 条目 `{SERVER_NAME}`"
       f"（{cfg_path.name}）")
    out(f"     {spec['note']}")
    return True


def link_skill(client_key: str) -> bool:
    """把本目录做成客户端 skills 目录下的 junction / 符号链接。"""
    spec = CLIENTS[client_key]
    skills: pathlib.Path = spec["skills"]()
    link = skills / SKILL_NAME
    step(f"在 {spec['label']} 的 skills 目录建立链接：{link}")
    try:
        skills.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        fail(f"无法创建 skills 目录：{e}")
        return False
    if link.exists():
        try:
            same = link.resolve() == ROOT.resolve()
        except OSError:
            same = False
        if same:
            ok("链接已存在且指向本目录")
            return True
        fail(f"{link} 已存在且指向别处，请手工处理")
        return False
    try:
        if os.name == "nt":
            # Windows 必须用 Junction：Git Bash 的 ln -s 会退化成空目录。
            import subprocess as sp
            sp.run(["cmd", "/c", "mklink", "/J", str(link), str(ROOT)], check=True,
                   capture_output=True, encoding="utf-8", errors="replace")
        else:
            link.symlink_to(ROOT, target_is_directory=True)
    except (OSError, subprocess.SubprocessError) as e:
        fail(f"建立链接失败：{e}")
        return False
    ok(f"链接已建立：{link} -> {ROOT}")
    return True


# ------------------------------------------------------------------ 检查模式

def check_mode(clients: list[str]) -> int:
    out("=" * 66)
    out("当前状态")
    out("=" * 66)
    py = venv_python()
    out(f"项目目录 : {ROOT}")
    out(f"venv 解释器 : {py if py.exists() else '（未创建）'}")
    out("")
    for key in clients:
        spec = CLIENTS[key]
        cfg_path: pathlib.Path = spec["config"]()
        data, skey = read_config(cfg_path)
        servers = data.get(skey) or {}
        entry = servers.get(SERVER_NAME)
        if not entry:
            out(f"[--] {spec['label']:14s} 未注册（配置：{cfg_path}）")
            continue
        cmd = entry.get("command", "?")
        alive = pathlib.Path(str(cmd)).exists()
        out(f"[{'OK' if alive else 'WARN'}] {spec['label']:14s} 已注册"
            f"，解释器{'存在' if alive else '不存在'}：{cmd}")
    out("")
    out("完整自检请跑：python scripts/check_setup.py")
    return 0


# ------------------------------------------------------------------ 入口

def main() -> int:
    _fix_stdout_encoding()
    ap = argparse.ArgumentParser(description="bilibili-info-gathering 一键安装")
    ap.add_argument("--client", default="auto",
                    choices=["auto", "all", "minimax", "workbuddy", "claude"])
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="只打印 MCP 配置 JSON")
    ap.add_argument("--check", action="store_true", help="只看现状，不改")
    ap.add_argument("--link-skill", action="store_true",
                    help="顺便把本目录做成 skills 目录的链接")
    ap.add_argument("--skip-venv", action="store_true", help="跳过建 venv 和装依赖")
    args = ap.parse_args()

    if args.print_only:
        print(json.dumps({"mcpServers": {SERVER_NAME: mcp_entry(venv_python())}},
                         ensure_ascii=False, indent=2))
        return 0

    detected = [k for k in CLIENTS
                if CLIENTS[k]["config"]().exists() or CLIENTS[k]["skills"]().exists()]
    if args.client == "auto":
        clients = detected or ["minimax"]
    elif args.client == "all":
        clients = list(CLIENTS)
    else:
        clients = [args.client]

    if args.check:
        return check_mode(clients)

    out("=" * 66)
    out("bilibili-info-gathering 安装")
    out(f"项目目录 : {ROOT}")
    out(f"目标客户端 : {', '.join(CLIENTS[c]['label'] for c in clients)}")
    out("=" * 66)
    out("")

    py = venv_python()
    if not args.skip_venv:
        py = ensure_venv()
        if py is None:
            out("")
            out("环境准备失败，已中止。")
            return 1
        if not install_deps(py):
            out("")
            out("依赖安装失败，已中止（未改动任何客户端配置）。")
            return 1
    elif not py.exists():
        fail("跳过 venv 但它不存在，无法继续")
        return 1

    for c in clients:
        if args.link_skill:
            link_skill(c)
        register_mcp(c, py)

    # 跑一遍自检
    out("")
    step("自检")
    try:
        r = subprocess.run([str(py), str(ROOT / "scripts" / "check_setup.py")],
                           cwd=str(ROOT), capture_output=True, encoding="utf-8",
                           errors="replace", timeout=600,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        stdout = r.stdout or ""
        for line in stdout.splitlines():
            out(line)
        if not stdout.strip():
            # 自检一声不吭 = 根本没跑起来。绝不能在这里报"安装完成"——
            # 那是最典型的假成功：所有验证都跳过了，退出码却是 0。
            fail("自检没有产生任何输出（可能进程在读取阶段就崩了），"
                 "请手动执行 python scripts/check_setup.py")
        elif r.returncode != 0:
            fail("自检未全通过（详见上面）")
        if r.stderr and r.stderr.strip():
            out("")
            out("自检 stderr：")
            for line in r.stderr.strip().splitlines()[-10:]:
                out(f"  {line}")
    except (OSError, subprocess.SubprocessError) as e:
        fail(f"自检没跑起来：{e}（可手动执行 python scripts/check_setup.py）")

    out("")
    out("=" * 66)
    if FAILS:
        out(f"安装完成，但有 {len(FAILS)} 项未通过：")
        for f in FAILS:
            out(f"  - {f}")
        out("")
        out("下一步：看上面标 [FAIL] 的条目，或跑 python scripts/check_setup.py")
        return 1
    out("安装完成。")
    out("")
    out("接下来：")
    out("  1. 重启 / 重连客户端（MCP 是 stdio 进程，不热加载）")
    out("  2. 在对话里说：'B站上关于 <某个议题> 有什么'")
    out("  3. 取字幕需要登录态：把 SESSDATA 写进 ~/.bilibili-mcp/config.json")
    out("     （格式 {\"sessdata\": \"...\"}）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
