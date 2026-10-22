"""MCP 协议级自检：起一个真实 stdio server，走 initialize → tools/list → tools/call。

不做假 mock——mock 过的 MCP 服务器上线后照样连不上，这类问题只有真连一次才能发现。

三层防护
--------
1. **静态自检（自引用）**：每个 `@mcp.tool()` 函数的字节码里如果引用了自己的名字，
   说明它调错了同名的导入（历史上真发生过：`get_comments` 工具名把
   `big.comments.get_comments` 导入名重新绑定，工具内部调自己 → 每次调用都失败，
   但 `tools/list` 完全正常）。这个检查快且不依赖网络。
2. **静态自检（跨工具）**：一个工具**不允许调用另一个工具**。
   工具签名与库函数签名不同（`get_comments(bvid, n, detail)` vs
   `get_comments(aid, n, sort, cookie)`），调错就是每次调用都失败。
   实测 `get_comment_images` 正是这样坏的——而第 1 层查不出来，
   因为它不是自引用。**这个检查是那个 bug 的墓碑。**
3. **真调用**：真的调几个便宜的工具，断言返回里没有 error；
   并对多P、注入标记做**定向断言**。

    python -m pytest tests -q      （或直接 python tests/test_mcp_smoke.py）
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# 这一整个文件会**真的联网**（还要登录态）。别人 clone 下来跑 `pytest` 时，
# 没网/没 Cookie 就会整片红——而那不是代码坏了，第一印象却已经崩了。
# 所以给一个显式开关：CI 与环境自检用 BIG_SKIP_NETWORK=1 跳过，
# 维护者本地完整跑一次仍然什么都不用设。
pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(os.environ.get("BIG_SKIP_NETWORK") == "1",
                       reason="BIG_SKIP_NETWORK=1：跳过需要联网与登录态的冒烟测试"),
]

ROOT = Path(__file__).resolve().parent.parent
EXPECT = ("scan_topic", "search_videos", "get_metadata", "get_comments", "get_transcript",
          "get_danmaku", "get_comment_images", "registry_get", "registry_upsert",
          "transcribe_audio", "grab_frames",
          # 批次 C 新增
          "deep_read", "get_creator_content", "scan_untrusted_text")

# 真调用哪些（便宜优先：本地或单次网络请求）
CALL_CASES = (
    ("registry_get", {"kind": "stats"}),
    ("registry_get", {"kind": "insights"}),
    ("get_metadata", {"bvids": ["BV1faGX65EgK"]}),
    ("get_comments", {"bvid": "BV1faGX65EgK", "n": 5, "detail": "brief"}),
    ("get_comments", {"bvid": "BV1faGX65EgK", "n": 5, "detail": "opinion"}),
)

# 多 P 靶子：45 个分P，每个分P 都有**各自独立**的字幕（cid 与首句都不同），
# 所以"读到的到底是不是第 2 部分"可以被**内容**判定，不只是看参数回显。
MULTIPAGE_BVID = "BV1vkJC6wENU"
MULTIPAGE_N = 45

# 注入靶子：实测这条视频评论区有一条 597 赞的指令注入
INJECTION_BVID = "BV19dJQ6sE9F"


def _tool_functions() -> dict:
    """从 mcp_server 模块里取出所有 @mcp.tool() 装饰的函数。"""
    sys.path.insert(0, str(ROOT / "src"))
    import big.mcp_server as srv
    out = {}
    for name in dir(srv):
        obj = getattr(srv, name)
        if callable(obj) and getattr(obj, "__module__", "") == srv.__name__:
            out[name] = obj
    return out


def test_no_tool_self_recursion():
    """每个工具的字节码里不能引用自己的名字。

    工具名与导入名同名时，模块级 def 会把导入名重新绑定，工具内部
    「调用同名函数」就变成调用自己 → 参数不对 → 每次调用都失败，
    而 tools/list 完全正常（最坏的一类静默 bug）。
    """
    funcs = _tool_functions()
    bad = []
    for name, fn in funcs.items():
        code = getattr(fn, "__code__", None)
        if code is None:
            continue
        if name in code.co_names:
            bad.append(name)
    assert not bad, (f"这些工具在自己的实现里引用了同名全局名（极可能是调错导入）："
                     f"{bad}")
    print(f"[OK] 静态自检（自引用）：{len(funcs)} 个模块级函数，无自我递归")


def test_no_tool_calls_another_tool():
    """一个工具**不允许**调用另一个工具。

    为什么单列一条：工具签名与库函数签名不同，工具调工具几乎必然是调错。
    实测：`get_comment_images` 里写的是 `get_comments(aid, n, sort, cookie)`，
    但那是**工具** `get_comments(bvid, n, detail)` —— 4 个位置参数对 3 个形参，
    于是每次调用都 TypeError。该工具 100% 不可用，而 tools/list 完全正常、
    「自引用」检查也查不出来（它不是自引用，是跨工具）。
    """
    funcs = _tool_functions()
    tool_names = set(EXPECT)
    bad = []
    for name, fn in funcs.items():
        code = getattr(fn, "__code__", None)
        if code is None:
            continue
        for other in tool_names - {name}:
            if other in code.co_names:
                bad.append(f"{name} -> {other}")
    assert not bad, (f"这些工具在实现里调用了另一个工具（多半是调错同名库函数）："
                     f"{bad}")
    print(f"[OK] 静态自检（跨工具）：{len(tool_names)} 个工具互不调用")


async def _run() -> tuple[set[str], list[tuple[str, str]]]:
    # 用**临时 registry 目录**：冒烟测试会写入议题档案与协议，
    # 指向真资产库会污染它（真库是"用完即弃"之外的东西，不该被测试写脏）。
    tmp_registry = tempfile.mkdtemp(prefix="big-smoke-reg-")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "big.mcp_server"],
        env={"BIG_REGISTRY_DIR": tmp_registry,
             "BIG_OUT_DIR": str(ROOT / ".bigout"),
             "PYTHONPATH": str(ROOT / "src"),
             "PYTHONIOENCODING": "utf-8",
             "PYTHONUNBUFFERED": "1"},
    )
    problems: list[tuple[str, str]] = []
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            names = {t.name for t in tools.tools}
            for tool, args in CALL_CASES:
                res = await s.call_tool(tool, args)
                text = res.content[0].text
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    problems.append((tool, f"返回不是 JSON：{text[:160]}"))
                    continue
                if isinstance(payload, dict) and payload.get("error"):
                    problems.append((tool, str(payload["error"])[:200]))
                elif isinstance(payload, list):
                    for item in payload:
                        if isinstance(item, dict) and item.get("error"):
                            problems.append((tool, str(item["error"])[:200]))
            problems.extend(await _check_multipage(s))
            problems.extend(await _check_untrusted(s))
            problems.extend(await _check_envelope(s))
            problems.extend(await _check_registry_kinds(s))
            problems.extend(await _check_scan_offset(s))
    shutil.rmtree(tmp_registry, ignore_errors=True)
    return names, problems


async def _check_multipage(s: ClientSession) -> list[tuple[str, str]]:
    """多 P 全链路：metadata 暴露分P清单 / page 参数真的生效 / 越界显式报错。"""
    out: list[tuple[str, str]] = []

    res = await s.call_tool("get_metadata", {"bvids": [MULTIPAGE_BVID]})
    payload = json.loads(res.content[0].text)
    meta = (payload.get("items") or [payload])[0]
    if meta.get("error"):
        out.append(("get_metadata", str(meta["error"])[:200]))
        return out
    if meta.get("n_pages") != MULTIPAGE_N:
        out.append(("get_metadata",
                    f"n_pages 期望 {MULTIPAGE_N}，实际 {meta.get('n_pages')}"))
    if len(meta.get("pages") or []) != MULTIPAGE_N:
        out.append(("get_metadata", "pages 列表长度与 n_pages 不一致"))
    if not (meta.get("pages") or [{}])[1].get("title"):
        out.append(("get_metadata", "pages[].title 缺失——分P标题是读哪一部分的依据"))
    if not (meta.get("degraded") or []):
        out.append(("get_metadata", "多P视频必须带降级说明"))

    p1 = json.loads((await s.call_tool("get_transcript",
                                       {"bvid": MULTIPAGE_BVID, "page": 1,
                                        "max_chars": 600})).content[0].text)
    p2 = json.loads((await s.call_tool("get_transcript",
                                       {"bvid": MULTIPAGE_BVID, "page": 2,
                                        "max_chars": 600})).content[0].text)
    if not p2.get("data_source"):
        out.append(("get_transcript(page=2)", f"data_source 为空：{str(p2)[:160]}"))
    if p2.get("page_used") != 2:
        out.append(("get_transcript(page=2)",
                    f"page_used 期望 2，实际 {p2.get('page_used')}"))
    if not any("只读了第 2 部分" in d for d in (p2.get("degraded") or [])):
        out.append(("get_transcript(page=2)", "缺少「只读了第 2 部分」的降级说明"))
    if p1.get("lines") and p2.get("lines") and p1["lines"][0] == p2["lines"][0]:
        out.append(("get_transcript(page=2)",
                    "第1部分与第2部分首句相同——page 参数可能没生效"))

    p99 = json.loads((await s.call_tool("get_transcript",
                                        {"bvid": MULTIPAGE_BVID, "page": 99})).content[0].text)
    if not p99.get("error"):
        out.append(("get_transcript(page=99)", "越界必须报错，不能静默回落到 P1"))
    if "越界" not in str(p99.get("error", "")):
        out.append(("get_transcript(page=99)",
                    f"报错信息要说清越界：{p99.get('error')}"))

    ta = json.loads((await s.call_tool("transcribe_audio",
                                       {"bvid": MULTIPAGE_BVID, "page": 99})).content[0].text)
    if not ta.get("error"):
        out.append(("transcribe_audio(page=99)", "越界必须报错——这个参数以前是假的"))
    return out


async def _check_envelope(s: ClientSession) -> list[tuple[str, str]]:
    """批次 B1：每个工具都必须带 degraded / ok / cost 三件套。"""
    out: list[tuple[str, str]] = []
    cases = (
        ("registry_get", {"kind": "stats"}),
        ("get_metadata", {"bvids": ["BV1faGX65EgK"]}),
        ("get_comments", {"bvid": "BV1faGX65EgK", "n": 5, "detail": "brief"}),
        ("get_transcript", {"bvid": "BV1faGX65EgK", "query": "投资", "max_chars": 300}),
        ("get_danmaku", {"bvid": "BV1faGX65EgK", "top": 2}),
    )
    for tool, args in cases:
        res = await s.call_tool(tool, args)
        try:
            p = json.loads(res.content[0].text)
        except json.JSONDecodeError:
            out.append((tool, "返回不是 JSON"))
            continue
        if p.get("error"):
            out.append((tool, f"调用失败：{str(p['error'])[:120]}"))
            continue
        for field in ("status", "degraded", "ok", "cost"):
            if field not in p:
                out.append((tool, f"缺少统一字段 {field}"))
        if p.get("status") not in ("ok", "degraded", "failed"):
            out.append((tool, f"status 必须是 ok/degraded/failed，实际 {p.get('status')!r}"))
        elif p.get("ok") != (p.get("status") == "ok"):
            out.append((tool, "ok 必须等于 (status == 'ok')"))
        cost = p.get("cost") or {}
        for field in ("calls", "comment_rows", "transcript_chars", "est_output_tokens"):
            if field not in cost:
                out.append((tool, f"cost 缺少 {field}"))
        if not isinstance(p.get("degraded"), list):
            out.append((tool, "degraded 必须是列表"))
        if p.get("status") in ("ok", "degraded") and p.get("ok") != (not p.get("degraded")):
            out.append((tool, "非 failed 时 ok 必须等于 not degraded"))
    return out


async def _check_registry_kinds(s: ClientSession) -> list[tuple[str, str]]:
    """批次 B2 / B4 / C1：暂存 → commit → 正式库往返。

    C1 之后**写侧默认只进暂存区**，所以「往返一致」的前提是**先 commit**。
    这条测试同时验证了写侧闸门本身：commit 之前正式库必须**读不到**新东西。
    """
    out: list[tuple[str, str]] = []
    topic = "冒烟测试议题"

    # ---- 1) 写入只进暂存区
    res = await s.call_tool("registry_upsert", {
        "kind": "topic", "records": [{
            "topic": topic,
            "core_questions": ["这个议题要回答什么"],
            "searched_axes": ["轴1", "轴2"],
            "covered": ["已覆盖的方向"],
            "gaps": ["还没覆盖的方向"],
            "open_conflicts": ["未收敛的冲突"],
            "user_adopted": ["用户采纳的发现"],
            "next_entry": "下次从哪切入",
        }]})
    p = json.loads(res.content[0].text)
    first = ((p.get("items") or [{}])[0] if isinstance(p.get("items"), list) else p) or {}
    if first.get("error"):
        out.append(("registry_upsert(kind=topic)", str(first["error"])[:160]))
    elif first.get("action") != "暂存":
        out.append(("registry_upsert(kind=topic)",
                    f"写侧闸门失效：action 应为「暂存」，实际 {first.get('action')!r}"))

    # ---- 2) commit 之前，正式库必须读不到
    got = json.loads((await s.call_tool("registry_get",
                                        {"kind": "topic", "topic": topic})).content[0].text)
    if got.get("topic") == topic:
        out.append(("registry_get(kind=topic)",
                    "commit 之前正式库里就有数据了——写侧闸门没起作用"))

    # ---- 3) 暂存区里能看到
    st = json.loads((await s.call_tool("registry_get",
                                       {"kind": "staging"})).content[0].text)
    if not (st.get("topic") or {}).get("n"):
        out.append(("registry_get(kind=staging)", "暂存区里看不到刚写的议题档案"))

    # ---- 4) commit 之后才进正式库
    res = await s.call_tool("registry_upsert", {"kind": "commit", "records": []})
    p = json.loads(res.content[0].text)
    if not p.get("ok"):
        out.append(("registry_upsert(kind=commit)", f"commit 失败：{str(p)[:160]}"))

    got = json.loads((await s.call_tool("registry_get",
                                        {"kind": "topic", "topic": topic})).content[0].text)
    if got.get("topic") != topic:
        out.append(("registry_get(kind=topic)", f"往返不一致：{got.get('topic')!r}"))
    if got.get("core_questions") != ["这个议题要回答什么"]:
        out.append(("registry_get(kind=topic)", "core_questions 往返不一致"))
    if got.get("user_adopted") != ["用户采纳的发现"]:
        out.append(("registry_get(kind=topic)", "user_adopted 往返不一致"))

    # ---- 5) 协议：同样先暂存，commit 后落盘
    res = await s.call_tool("registry_upsert", {
        "kind": "protocol", "records": [{
            "topic": topic,
            "core_questions": ["Q1"],
            "keyword_families": [{"family": "上位词", "keywords": ["词1", "词2"],
                                   "hits": [3, 5]}],
            "include_criteria": ["纳入标准1"],
            "exclude_criteria": ["排除标准1"],
            "budget": {"候选上限": 40, "调用上限": 20},
            "known_assets": ["已有信源 3 条"],
            "run_funnel": {"unique_candidates": 12, "final": 3},
        }]})
    p = json.loads(res.content[0].text)
    first = ((p.get("items") or [{}])[0] if isinstance(p.get("items"), list) else p) or {}
    if first.get("error"):
        out.append(("registry_upsert(kind=protocol)", str(first["error"])[:160]))
    else:
        await s.call_tool("registry_upsert", {"kind": "commit", "records": []})
        got = json.loads((await s.call_tool("registry_get",
                                            {"kind": "protocol",
                                             "topic": topic})).content[0].text)
        if not got.get("exists"):
            out.append(("registry_get(kind=protocol)", "commit 后协议没落盘"))
        else:
            body = got.get("content") or ""
            for must in ("核心问题", "关键词族", "纳入标准", "排除标准", "预算", "已知资产"):
                if must not in body:
                    out.append(("registry_get(kind=protocol)", f"协议模板缺「{must}」"))
            if not (got.get("runs") or []):
                out.append(("registry_get(kind=protocol)", "缺少 run-<日期>.json 漏斗文件"))

    # ---- 6) 观点簇（批次 D4）：暂存 → commit → 按 (bvid, cluster) 读回
    #     这里同时验复合主键：同视频同名簇要覆盖，不同视频同名簇要并存。
    cluster_rec = {"bvid": "BV1smoke00001", "cluster": "免疫派",
                   "stance": "社区有害，最优策略是不混",
                   "member_rpids": ["123", "456"],
                   "evidence": ["468赞/8楼｜打开 APP，打卡，签到，退出 APP"],
                   "axis": {"prevalence": 41.0, "agreement": 33.5, "controversy": 12.0}}
    res = await s.call_tool("registry_upsert",
                            {"kind": "cluster", "topic": topic, "records": [cluster_rec]})
    p = json.loads(res.content[0].text)
    first = ((p.get("items") or [{}])[0] if isinstance(p.get("items"), list) else p) or {}
    if first.get("error"):
        out.append(("registry_upsert(kind=cluster)", str(first["error"])[:160]))
    else:
        await s.call_tool("registry_upsert", {"kind": "commit", "records": []})
        got = json.loads((await s.call_tool(
            "registry_get", {"kind": "clusters", "topic": topic})).content[0].text)
        if not got.get("exists"):
            out.append(("registry_get(kind=clusters)", "commit 后簇没落盘"))
        else:
            if got.get("n") != 1:
                out.append(("registry_get(kind=clusters)", f"n 期望 1，实际 {got.get('n')}"))
            c0 = (got.get("clusters") or [{}])[0]
            if c0.get("cluster") != "免疫派":
                out.append(("registry_get(kind=clusters)", "簇名往返不一致"))
            if c0.get("member_rpids") != ["123", "456"]:
                out.append(("registry_get(kind=clusters)",
                            f"member_rpids 往返不一致：{c0.get('member_rpids')!r}"))
            if not isinstance(c0.get("axis"), dict):
                out.append(("registry_get(kind=clusters)", "axis 必须是 dict"))

            # 不同视频的同名簇 → 应并存为 2 条（复合主键生效）
            await s.call_tool("registry_upsert", {
                "kind": "cluster", "topic": topic,
                "records": [{**cluster_rec, "bvid": "BV1smoke00002"}]})
            await s.call_tool("registry_upsert", {"kind": "commit", "records": []})
            got2 = json.loads((await s.call_tool(
                "registry_get", {"kind": "clusters", "topic": topic})).content[0].text)
            if got2.get("n") != 2:
                out.append(("registry_get(kind=clusters)",
                            f"不同视频同名簇应并存，n 期望 2，实际 {got2.get('n')}"))

            # 同一 (bvid, cluster) 再写 → 覆盖，不新增
            await s.call_tool("registry_upsert", {
                "kind": "cluster", "topic": topic,
                "records": [{**cluster_rec, "stance": "改过的说法"}]})
            await s.call_tool("registry_upsert", {"kind": "commit", "records": []})
            got3 = json.loads((await s.call_tool(
                "registry_get", {"kind": "clusters", "topic": topic})).content[0].text)
            if got3.get("n") != 2:
                out.append(("registry_get(kind=clusters)",
                            f"同键重写应覆盖，n 期望 2，实际 {got3.get('n')}"))
            else:
                stances = [c.get("stance") for c in got3.get("clusters") or []]
                if "改过的说法" not in stances:
                    out.append(("registry_get(kind=clusters)", "覆盖后没读到新值"))
    return out


async def _check_scan_offset(s: ClientSession) -> list[tuple[str, str]]:
    """批次 D2：offset 翻页必须真的换一批候选，且两页不重叠。

    这条断言针对实测问题：二游议题 unique_candidates 835 / 619，却只返回 50 / 45，
    且当时**没有翻页参数**——约 1,359 条候选永远看不到。
    """
    out: list[tuple[str, str]] = []
    base = {"keywords": ["AI Agent 自主学习"], "per_query": 5, "max_candidates": 3,
            "comment_budget": 1, "comment_n": 5, "image_policy": "off"}
    p1 = json.loads((await s.call_tool("scan_topic", base)).content[0].text)
    if p1.get("error"):
        out.append(("scan_topic(offset)", str(p1["error"])[:160]))
        return out
    cov = p1.get("coverage") or {}
    if cov.get("offset") != 0:
        out.append(("scan_topic(offset)", f"首页 offset 应为 0，实际 {cov.get('offset')}"))
    if not cov.get("rest_preview"):
        out.append(("scan_topic(offset)", "缺 rest_preview（未展示候选摘要）"))
    if (cov.get("ranked_total") or 0) <= base["max_candidates"]:
        return out                      # 池子太小，翻页断言不适用（不算失败）
    nxt = cov.get("next_offset")
    if nxt is None:
        out.append(("scan_topic(offset)", "池子大于 max_candidates 却没给 next_offset"))
        return out
    p2 = json.loads((await s.call_tool("scan_topic", {**base, "offset": nxt})).content[0].text)
    if p2.get("error"):
        out.append(("scan_topic(offset=第2页)", str(p2["error"])[:160]))
        return out
    cov2 = p2.get("coverage") or {}
    if cov2.get("offset") != nxt:
        out.append(("scan_topic(offset=第2页)", f"offset 回显不对：{cov2.get('offset')}"))
    b1 = [c.get("bvid") for c in (p1.get("candidates") or [])]
    b2 = [c.get("bvid") for c in (p2.get("candidates") or [])]
    overlap = set(b1) & set(b2)
    if overlap:
        out.append(("scan_topic(offset=第2页)", f"两页候选重叠：{sorted(overlap)}"))
    return out


async def _check_untrusted(s: ClientSession) -> list[tuple[str, str]]:
    """外部文本必须带 untrusted 标记；注入评论必须被标记出来。"""
    out: list[tuple[str, str]] = []
    res = await s.call_tool("get_comments",
                            {"bvid": INJECTION_BVID, "n": 100, "detail": "opinion"})
    payload = json.loads(res.content[0].text)
    if payload.get("error"):
        out.append(("get_comments", str(payload["error"])[:200]))
        return out
    if not (payload.get("untrusted") or {}).get("true"):
        out.append(("get_comments", "返回顶层缺少 untrusted 标记"))
    if not (payload.get("untrusted") or {}).get("fields"):
        out.append(("get_comments", "untrusted 块必须列出哪些路径是外部原文"))
    hits = [c for c in (payload.get("comments") or []) if c.get("injection_risk")]
    if not hits:
        out.append(("get_comments",
                    "实测注入评论没有被标记（BV19dJQ6sE9F 有一条 597 赞的注入）"))
    else:
        print(f"[OK] 注入检测：{INJECTION_BVID} 标出 {len(hits)} 条注入评论")
    return out


def test_mcp_server():
    test_no_tool_self_recursion()
    test_no_tool_calls_another_tool()
    names, problems = asyncio.run(_run())
    missing = [e for e in EXPECT if e not in names]
    assert not missing, f"缺少工具 {missing}；实际有 {sorted(names)}"
    assert not problems, "真调用失败：\n" + "\n".join(f"  {t}: {m}" for t, m in problems)
    print(f"[OK] tools={len(names)}，真调用 {len(CALL_CASES)} 个工具 + 多P/注入定向断言全部成功")


if __name__ == "__main__":
    test_mcp_server()
