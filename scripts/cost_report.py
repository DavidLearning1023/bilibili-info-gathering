#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调用账本 → 报告 §五 的「单轮开销」那一行。

     python scripts/cost_report.py                 # 这一轮（需先打 run-start 标记）
     python scripts/cost_report.py --runs          # 每一轮各一行（对比深入 vs 简要）
     python scripts/cost_report.py --all           # 全部历史
     python scripts/cost_report.py --line          # 只输出可贴进报告的那一行
     python scripts/cost_report.py --mark "议题名"  # 打「本轮开始」标记
     python scripts/cost_report.py --end           # 闭合本轮（数字此后不变）
     python scripts/cost_report.py --rotate        # 强制归档（排障用）

为什么要有这个脚本
------------------
「这轮花了多少」是一个会被写进正式文档、然后被人拿去做判断的数字。
让人（或模型）自己把 JSON 里的四个数加起来再格式化，错误率不低——
而这四个数恰好又都在同一个账本里，没有理由手算。

口径（引用时必须带上，别省）
--------------------------
* `calls` 是**真的打了几次上游**（`http.call_count` 的差值），不是估算。
* `comment_rows` / `transcript_chars` 是实际读进来的行数与字符数。
* `est_output_tokens` 是**输出侧粗估**：CJK×0.7 + 其它×0.25。
  用途是横向比较（哪个工具最费、这一轮跑了几次），**不是账单金额**——
  真实费用要乘供应商单价，且输入侧 token（模型读到的返回）没算进来。

输出字符只用 GBK 可编码的字符（中文 + ASCII 标点），原因同 `check_setup.py`：
Windows 下 stdout 被管道接走时按系统代码页解码，emoji 会抛 UnicodeEncodeError。
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from big import registry  # noqa: E402


def _print_table(by_tool: dict) -> None:
    if not by_tool:
        print("  （没有工具调用记录）")
        return
    print(f"  {'工具':<20}{'次数':>6}{'上游调用':>10}{'评论行':>10}{'转写字符':>12}{'输出估算':>12}")
    for name, b in by_tool.items():
        print(f"  {name:<20}{b['n']:>6}{b['calls']:>10}{b['comment_rows']:>10}"
              f"{b['transcript_chars']:>12}{b['est_output_tokens']:>12}")


def main() -> int:
    ap = argparse.ArgumentParser(description="调用账本汇总 / 报告单轮开销行")
    ap.add_argument("--all", action="store_true", help="统计全部历史（默认只统计本轮）")
    ap.add_argument("--limit", type=int, default=2000, help="最多读多少行")
    ap.add_argument("--runs", action="store_true", help="每一轮各一行（对比多轮）")
    ap.add_argument("--line", action="store_true", help="只输出可贴进报告的那一行")
    ap.add_argument("--mark", metavar="议题名", default=None,
                    help="在账本里打「本轮开始」标记")
    ap.add_argument("--end", action="store_true", help="闭合本轮（打 run-end，数字此后不变）")
    ap.add_argument("--rotate", action="store_true", help="强制归档当前账本")
    a = ap.parse_args()

    if a.mark is not None:
        res = registry.mark_run_start(a.mark)
        print(f"[OK] 已标记本轮开始 run={res['run']}（{res['log']}）")
        return 0
    if a.end:
        res = registry.mark_run_end("cost_report --end")
        print(f"[OK] 已闭合 run={res.get('run')}" if res.get("closed")
              else f"[SKIP] {res.get('note')}")
        return 0
    if a.rotate:
        arc = registry.rotate_cost_log(force=True)
        print(f"[OK] 已归档到 {arc}" if arc else "[SKIP] 没有账本可归档")
        return 0
    if a.runs:
        segs = registry.cost_summary(a.limit, "runs")["runs"]
        if not segs:
            print("[SKIP] 账本里还没有 run-start 标记")
            return 0
        for seg in segs:
            print(f"- {seg['run']}  {seg.get('topic') or '（未写议题）'}"
                  f"  [{seg['closed_by']}]")
            print(f"  {seg['md_line']}")
        return 0

    window = "all" if a.all else "run"
    s = registry.cost_summary(a.limit, window)
    if not s.get("exists"):
        print(f"[SKIP] {s.get('note')}")
        return 0
    if window == "run" and not s.get("has_marker"):
        print("[WARN] 账本里没有「本轮开始」标记——下面退化为全部历史。"
              "先跑 `--mark \"议题名\"` 或 registry_upsert(kind='run-start')。")
        s = s.get("fallback") or s

    if a.line:
        print(s.get("md_line", ""))
        return 0

    scope = "本轮" if window == "run" else "全部历史"
    print(f"== 调用账本（{scope}）==")
    if s.get("run"):
        print(f"   run        {s['run']}")
    print(f"   时间        {s.get('since')} → {s.get('until')}")
    print(f"   工具调用     {s.get('n_calls')} 次（记录行）")
    print(f"   上游调用     {s.get('upstream_calls')} 次")
    print(f"   评论行       {s.get('comment_rows')}")
    print(f"   转写字符     {s.get('transcript_chars')}")
    print(f"   输出估算     {s.get('est_output_tokens')} token（粗估，非账单）")
    if s.get("truncated"):
        print(f"   [WARN] 账本共 {s.get('lines_total')} 行，只读了最后 {a.limit} 行")
    print()
    _print_table(s.get("by_tool") or {})
    print()
    print("报告 §五 可直接粘贴（commit 后用 --line 取最终值）：")
    print(s.get("md_line", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
