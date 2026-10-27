#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单独验证 MCP server 本身能不能起、工具全不全。

    python scripts/probe_mcp.py                 # 自动找本机客户端配置里的条目
    python scripts/probe_mcp.py --config <path> # 指定 mcp.json
    python scripts/probe_mcp.py --command "D:/x/.venv/Scripts/python.exe" --arg -m --arg big.mcp_server

**为什么需要它**：客户端里"看不到工具"时，问题可能有两层——
① 服务器起不来；② 服务器好的，但客户端没把它装进会话。
`check_setup.py` 只覆盖 ①。这个脚本把 ① 单独钉死：
如果它能拿到 14 个工具，就说明**问题一定在客户端侧**，不用再怀疑代码和 venv。

只用标准库，不依赖 mcp SDK（协议就是换行的 JSON-RPC）。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

SERVER_NAME = "bilibili-info-gathering"
EXPECTED = 14

CANDIDATES = [
    pathlib.Path.home() / ".workbuddy" / "mcp.json",
    pathlib.Path.home() / ".minimax" / "mcp.json",
    pathlib.Path.home() / ".claude.json",
]


def find_entry(config: pathlib.Path) -> dict | None:
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for key in ("mcpServers", "mcp", "servers"):
        servers = data.get(key)
        if isinstance(servers, dict) and SERVER_NAME in servers:
            entry = servers[SERVER_NAME]
            if isinstance(entry, dict):
                return entry
    return None


def probe(entry: dict, timeout: float = 30.0) -> int:
    cmd = entry.get("command") or entry.get("cmd")
    args = list(entry.get("args") or [])
    if not cmd:
        print("[FAIL] 配置条目里没有 command，无法探测")
        return 1
    env = dict(os.environ)
    env.update({k: str(v) for k, v in (entry.get("env") or {}).items()})

    print(f"  命令：{cmd} {' '.join(args)}")
    for k in ("BIG_REGISTRY_DIR", "BIG_REPORT_DIR", "BIG_OUT_DIR"):
        if env.get(k):
            print(f"  env {k} = {env[k]}")

    t0 = time.time()
    try:
        p = subprocess.Popen([cmd, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env, text=True,
                             encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"[FAIL] 起不来：{e}")
        return 1

    def rpc(obj: dict) -> dict:
        assert p.stdin and p.stdout
        p.stdin.write(json.dumps(obj) + "\n")
        p.stdin.flush()
        line = p.stdout.readline()
        if not line:
            raise RuntimeError("服务器没有返回（可能是 import 失败或 stdout 被污染）")
        return json.loads(line)

    try:
        init = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "probe-mcp", "version": "0"}}})
        dt = time.time() - t0
        name = (init.get("result") or {}).get("serverInfo", {}).get("name", "?")
        print(f"[OK]   握手成功（{dt:.1f}s），serverInfo.name = {name}")

        tools = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = [t["name"] for t in (tools.get("result") or {}).get("tools", [])]
        print(f"[{'OK' if len(names) == EXPECTED else 'WARN'}]   工具 {len(names)} 个"
              f"（期望 {EXPECTED}）：{', '.join(names[:6])}{' …' if len(names) > 6 else ''}")
        if len(names) != EXPECTED:
            print("       工具数与文档不一致：分别可能是版本旧、或有工具改过名")
    except Exception as e:                                    # noqa: BLE001
        print(f"[FAIL] 探测失败：{type(e).__name__}: {e}")
        try:
            err = (p.stderr.read() or "").strip() if p.stderr else ""
        except Exception:                                     # noqa: BLE001
            err = ""
        if err:
            print("       stderr 末尾：")
            for line in err.splitlines()[-8:]:
                print("       " + line)
        return 1
    finally:
        try:
            if p.stdin:
                p.stdin.close()
            p.wait(timeout=5)
        except Exception:                                     # noqa: BLE001
            p.kill()

    print("\n结论：服务器本身可用。客户端里看不到工具 ⇒ 查连接器状态 / 重启客户端，别再怀疑代码。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="单独验证 bilibili-info-gathering MCP 是否能起")
    ap.add_argument("--config", help="mcp.json 路径（默认自动在已知客户端位置里找）")
    ap.add_argument("--command", help="不看配置，直接指定启动命令")
    ap.add_argument("--arg", action="append", default=[], help="配合 --command 的参数，可重复")
    a = ap.parse_args()

    if a.command:
        return probe({"command": a.command, "args": a.arg})

    paths = [pathlib.Path(a.config)] if a.config else CANDIDATES
    for cfg in paths:
        if not cfg.exists():
            continue
        entry = find_entry(cfg)
        if not entry:
            continue
        print(f"== 用 {cfg} 里的 {SERVER_NAME} 条目探测")
        return probe(entry)

    print("[FAIL] 没在任何已知客户端配置里找到 "
          f"{SERVER_NAME}。找过：{', '.join(str(p) for p in paths)}")
    print("       装了但没注册？跑 python scripts/install_mcp.py")
    return 1


if __name__ == "__main__":
    sys.exit(main())
