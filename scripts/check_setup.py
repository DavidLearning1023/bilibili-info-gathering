#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-info-gathering 自检。

一次跑完五项：依赖 / MCP 握手 / 资产库 / B站接口 / 凭据。

    python scripts/check_setup.py            # 全量自检（会联网打一次 B站接口）
    python scripts/check_setup.py --quick    # 跳过联网项
    python scripts/check_setup.py --json     # 机器可读输出

退出码 0 = 没有 FAIL；1 = 有 FAIL。任何 FAIL 都在末尾汇总。

设计约束
--------
* **只用标准库 + 本项目自身的依赖**，不联网下载任何东西。
* **输出字符必须 GBK 可编码**（中文 + ASCII 标点）。Windows 下 stdout 被管道
  接走时用的是系统 ANSI 代码页(GBK)，打印 emoji / 生僻符号会抛
  UnicodeEncodeError，脚本以 exit 1 结束——看起来像"自检失败"，其实是编码问题。
  所以本文件不使用 emoji、->、⇒ 等非 GBK 字符。
* **退出码只反映检查结果**，绝不会被编码问题或输出异常污染。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL_NAME = "bilibili-info-gathering"

EXPECTED_TOOLS = (
    "scan_topic", "search_videos", "get_metadata", "get_comments", "get_transcript",
    "get_danmaku", "get_comment_images", "registry_get", "registry_upsert",
    "transcribe_audio", "grab_frames",
    # 批次 C 新增
    "deep_read", "get_creator_content", "scan_untrusted_text",
)

# 让 MCP 进程的工作目录与安装位置无关。MCode / WorkBuddy / Claude Code 的
# stdio server 未必带 cwd 参数（客户端配置层通常只有 command/args/env），
# 而 big.media.OUT_ROOT 的默认值依赖 cwd —— 不钉死就会把图片写到随机目录。
PINNING_ENV = {
    "BIG_OUT_DIR": str(ROOT / ".bigout"),
    "BIG_REGISTRY_DIR": str(ROOT / ".registry"),
    "PYTHONPATH": str(ROOT / "src"),
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUNBUFFERED": "1",
}


# ------------------------------------------------------------------ 输出底座

def _fix_stdout_encoding() -> None:
    """把管道/重定向下的 stdout 强制成 UTF-8。

    Windows 的坑：stdout **被管道接走**时用系统 ANSI 代码页（本机 cp936/GBK），
    不是控制台代码页。于是同一段中文，人在终端里看是好的，脚本被管道一接就变乱码。
    消费方（CI、Agent、编辑器）几乎都按 UTF-8 读，所以**非交互场景一律强制 UTF-8**；
    交互终端保留控制台自己的编码（老 GBK 控制台直接可读中文）。

    只影响编码，不影响检查逻辑；reconfigure 失败也不致命。
    """
    if getattr(sys.stdout, "isatty", lambda: False)():
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


def out(line: str = "") -> None:
    """打印一行，保证不会因编码问题把整个脚本带崩。"""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        data = line.encode(enc)
    except UnicodeEncodeError:
        data = line.encode(enc, "replace")
    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        buf.write(data + b"\n")
        buf.flush()
    else:                                            # stdout 被替换（如 pytest 捕获）
        print(line)


RESULTS: list[dict] = []


def record(level: str, msg: str, hint: str = "") -> None:
    RESULTS.append({"level": level, "msg": msg, "hint": hint})
    tag = {"OK": "[OK]  ", "WARN": "[WARN]", "FAIL": "[FAIL]"}[level]
    out(f"  {tag} {msg}")
    if hint:
        out(f"         -> {hint}")


def section(title: str) -> None:
    out("")
    out(f"[{title}]")


# ------------------------------------------------------------------ 解释器定位

def venv_python() -> pathlib.Path | None:
    if os.name == "nt":
        p = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        p = ROOT / ".venv" / "bin" / "python"
    return p if p.exists() else None


def ensure_venv_interpreter() -> None:
    """如果当前解释器不是本项目的 venv，且 venv 存在，就用 venv 重启自己。

    原因：握手需要 mcp 客户端库，只有 venv 里装了。直接用系统 python 跑会在
    import 阶段就 ImportError，用户看到的是看不懂的栈而不是"用 venv 跑"。
    """
    cur = pathlib.Path(sys.executable).resolve()
    vp = venv_python()
    if vp is None:
        return
    try:
        if cur == vp.resolve():
            return
    except OSError:
        pass
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        r = subprocess.run([str(vp), str(pathlib.Path(__file__).resolve()), *sys.argv[1:]],
                           env=env, cwd=str(ROOT))
    except OSError as e:
        out(f"[FAIL] 无法用 venv 解释器重启：{e}")
        sys.exit(1)
    sys.exit(r.returncode)


# ------------------------------------------------------------------ 各项检查

def check_identity() -> None:
    """自动判断「使用者」还是「维护者」——不让用户自己推断。"""
    section("身份")
    clients = {
        "MiniMax Code": pathlib.Path.home() / ".minimax" / "skills" / SKILL_NAME,
        "WorkBuddy": pathlib.Path.home() / ".workbuddy" / "skills" / SKILL_NAME,
        "Claude Code": pathlib.Path.home() / ".claude" / "skills" / SKILL_NAME,
    }
    linked = []
    for client, p in clients.items():
        if not p.exists():
            continue
        is_link = p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
        try:
            same = p.resolve() == ROOT.resolve()
        except OSError:
            same = False
        if same:
            linked.append(f"{client}（链接指向本目录）")
    if linked:
        record("OK", "你是维护者：本目录被 " + "、".join(linked) + " 直接引用")
        record("OK", "改动落在本目录即生效，无需同步脚本")
    else:
        record("OK", "你是使用者：skills 目录里的副本就是你的唯一一份")
        out("         改代码请先 git clone 本仓库到 skills 目录，见 INSTALL.md")


def check_deps() -> None:
    section("1/5 依赖")
    if sys.version_info < (3, 10):
        record("FAIL", f"Python {sys.version.split()[0]} 过低，需要 3.10+",
               "升级 Python 后重跑")
    else:
        record("OK", f"Python {sys.version.split()[0]}")

    try:
        import mcp                                    # noqa: F401
        try:
            from importlib.metadata import version
            ver = version("mcp")
        except Exception:                             # noqa: BLE001
            ver = "已安装"
        record("OK", f"mcp {ver}")
    except ImportError:
        record("FAIL", "缺少 mcp 依赖",
               "在项目目录执行：pip install -e \".[images]\"")

    try:
        import PIL                                     # noqa: F401
        record("OK", f"Pillow {getattr(PIL, '__version__', '已安装')}"
                     f"（评论区联系表可用）")
    except ImportError:
        record("WARN", "缺少 Pillow：get_comment_images 不可用",
               "pip install -e \".[images]\" 补上；其余功能不受影响")

    try:
        import big
        loc = pathlib.Path(big.__file__).resolve()
        expect = (ROOT / "src" / "big" / "__init__.py").resolve()
        if loc == expect:
            record("OK", f"big 包指向本仓库（editable 安装）")
        else:
            record("WARN", f"big 包来自别处：{loc.parent}",
                   "可能是旧安装。重装：pip install -e \".[images]\"")
    except ImportError:
        record("FAIL", "无法导入 big 包",
               "在项目目录执行：pip install -e \".[images]\"")


def check_paths() -> None:
    """验证目录锚定：换一个完全无关的 cwd 启动，路径仍然落在项目里。"""
    section("2/5 目录锚定（工作目录无关性）")
    code = ("import big.media as m, big.registry as r;"
            "print(m.OUT_ROOT);print(r.ROOT)")
    env = dict(os.environ)
    env.update(PINNING_ENV)
    try:
        # 显式按 UTF-8 解码子进程输出：Windows 下 subprocess 默认用 locale(GBK)
        # 解码，而子进程按 PYTHONIOENCODING=utf-8 写出 -> 解码炸在读取线程里，
        # 父进程只看到一个看不懂的 traceback。
        r = subprocess.run([sys.executable, "-c", code], env=env,
                           cwd=tempfile.gettempdir(),
                           capture_output=True, encoding="utf-8", errors="replace",
                           timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        record("FAIL", f"无法验证目录锚定：{e}")
        return
    if r.returncode != 0:
        record("FAIL", "锚定验证脚本执行失败", (r.stderr or "").strip()[-300:])
        return
    lines = [x.strip() for x in r.stdout.strip().splitlines() if x.strip()]
    if len(lines) < 2:
        record("FAIL", "锚定验证输出异常", r.stdout.strip()[-200:])
        return
    out_dir, reg_dir = pathlib.Path(lines[0]), pathlib.Path(lines[1])
    for label, p in (("图片输出目录", out_dir), ("资产库目录", reg_dir)):
        try:
            inside = p.resolve().is_relative_to(ROOT.resolve())
        except (OSError, AttributeError):
            inside = str(p).lower().startswith(str(ROOT).lower())
        if inside:
            record("OK", f"{label}锚定在项目内：{p}")
        else:
            record("FAIL", f"{label}跑到了项目外：{p}",
                   "MCP 配置里必须设 BIG_OUT_DIR / BIG_REGISTRY_DIR，否则换个 cwd 就写到别处")


async def _mcp_probe(net: bool) -> tuple[list[str], dict, dict]:
    """起一个真实的 stdio server，走 initialize -> tools/list -> tools/call。

    不做 mock：mock 过的 MCP 服务器上线后照样连不上，这类问题只有真连一次
    才能发现。cwd 故意设成临时目录，模拟"客户端不带 cwd 启动"的真实情况。
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "big.mcp_server"],
        env=dict(PINNING_ENV),
        cwd=tempfile.gettempdir(),
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            names = [t.name for t in tools.tools]
            reg = await s.call_tool("registry_get", {"kind": "stats"})
            reg_payload = json.loads(reg.content[0].text)
            srch = {}
            if net:
                res = await s.call_tool("search_videos", {"keyword": "投资", "limit": 3})
                srch = json.loads(res.content[0].text)
            return names, reg_payload, srch


def check_mcp(net: bool) -> None:
    section("3/5 MCP 协议握手")
    try:
        names, reg, srch = asyncio.run(_mcp_probe(net))
    except Exception as e:                              # noqa: BLE001
        record("FAIL", f"MCP 握手失败：{type(e).__name__}: {e}",
               "用 python -m big.mcp_server 手动起一次看报错；"
               "若报 ModuleNotFoundError 先 pip install -e \".[images]\"")
        return
    missing = [t for t in EXPECTED_TOOLS if t not in names]
    extra = [t for t in names if t not in EXPECTED_TOOLS]
    if missing:
        record("FAIL", "缺少工具：" + "、".join(missing),
               "实际有：" + "、".join(sorted(names)))
    if extra:
        # 文档里没有的工具：要么补文档，要么删工具。"与文档一致"不能是空话
        record("WARN", "文档里没有的工具：" + "、".join(extra),
               "补进 references/mcp-tools.md，或确认是否该删")
    if not missing and not extra:
        record("OK", f"tools/list 返回 {len(names)} 个工具，与文档完全一致")

    if "sources" in reg and "videos" in reg:
        n_src = reg.get("sources") or 0
        n_vid = reg.get("videos") or 0
        record("OK", f"registry_get 可用（信源 {n_src} 条 / 视频 {n_vid} 条）")
    else:
        record("FAIL", "registry_get 返回结构异常", json.dumps(reg, ensure_ascii=False)[:200])

    if net:
        items = srch.get("items") or []
        if items:
            title = str(items[0].get("title", ""))[:24]
            record("OK", f"search_videos 走通网络（{len(items)} 条，首条：{title}）")
        else:
            record("FAIL", "search_videos 没取到数据",
                   json.dumps(srch, ensure_ascii=False)[:200])


def check_registry() -> None:
    section("4/5 资产库与报告库")
    reg = ROOT / ".registry"
    if not reg.exists():
        record("WARN", f"资产库目录不存在：{reg}",
               "首次写入时自动创建；也可执行 big registry init")
    else:
        record("OK", f"资产库目录：{reg}")
        for label, name in (("信源库", "sources.json"), ("视频索引", "videos.json"),
                            ("经验库", "insights.md")):
            p = reg / name
            if p.exists():
                record("OK", f"{label}存在：{name}")
            else:
                record("WARN", f"{label}尚未建立：{name}", "用到该功能时自动写入")

    # 报告库：产出落盘位置。路径不对不会报错，只会让报告写到意料之外的地方，
    # 所以这里显式打印一次真实路径，并验证"能建目录、能写文件"。
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from big import reports
        lib = reports.library_root()
        if lib.exists():
            n = len([x for x in lib.iterdir() if x.is_dir()])
            record("OK", f"报告库目录：{lib}（{n} 份议题报告）")
        else:
            lib.mkdir(parents=True, exist_ok=True)
            record("OK", f"报告库目录已创建：{lib}")
        probe = lib / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as e:                                    # noqa: BLE001
        record("WARN", f"报告库不可写：{type(e).__name__}: {e}",
               "设 BIG_REPORT_DIR 指向一个可写目录")


def check_credentials() -> None:
    section("5/5 凭据（仅取字幕需要）")
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from big.http import load_cookie
    except ImportError:
        record("WARN", "无法加载 big.http，跳过凭据检查")
        return
    cookie = load_cookie()
    if cookie:
        record("OK", "已配置登录态（取字幕可用）")
    else:
        record("WARN", "未配置登录态：取字幕不可用",
               "元数据 / 评论 / 弹幕 / 搜索不受影响。"
               "配置：把 SESSDATA 写进 ~/.bilibili-mcp/config.json"
               "（{\"sessdata\": \"...\"}）")
    out("         注：MCP 进程不会继承环境变量 BILI_SESSDATA，"
        "所以必须用上面的配置文件方式")


# ------------------------------------------------------------------ 入口

def main() -> int:
    _fix_stdout_encoding()
    ap = argparse.ArgumentParser(description="bilibili-info-gathering 自检")
    ap.add_argument("--quick", action="store_true", help="跳过联网检查")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if args.json:
        globals()["out"] = lambda line="": None

    ensure_venv_interpreter()

    out("=" * 66)
    out("bilibili-info-gathering 自检")
    out(f"项目目录: {ROOT}")
    out(f"解释器  : {sys.executable}")
    out("=" * 66)

    check_identity()
    check_deps()
    check_paths()
    check_mcp(net=not args.quick)
    check_registry()
    check_credentials()

    n_ok = sum(1 for r in RESULTS if r["level"] == "OK")
    n_warn = sum(1 for r in RESULTS if r["level"] == "WARN")
    n_fail = sum(1 for r in RESULTS if r["level"] == "FAIL")
    out("")
    out("=" * 66)
    out(f"结果: [OK] {n_ok} 项  [WARN] {n_warn} 项  [FAIL] {n_fail} 项")

    if n_fail:
        out("")
        out("未通过项：")
        for r in RESULTS:
            if r["level"] == "FAIL":
                out(f"  - {r['msg']}")
                if r["hint"]:
                    out(f"    -> {r['hint']}")
    else:
        out("全部关键项通过，可以开始用。")

    if args.json:
        print(json.dumps({
            "root": str(ROOT),
            "python": sys.executable,
            "ok": n_ok, "warn": n_warn, "fail": n_fail,
            "results": RESULTS,
        }, ensure_ascii=False, indent=1))
    return 1 if n_fail else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
