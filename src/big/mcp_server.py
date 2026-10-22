"""MCP server：把取数能力暴露成工具。

设计原则
--------
1. **一个阶段一次调用。** 阶段 1 广扫是一个 `scan_topic`，不是 30 次 `get_metadata`。
   MCP 的价值在"少往返"，不在于"把每个 REST 接口包一层"。
2. **返回紧凑 JSON。** 不放原始 API 响应、不返回全部评论正文——那是把上下文当垃圾桶。
3. **判断不进代码。** 这里不做"这条值不值得看"的结论，只给判断所需的最小事实。
   双轴评级、离场规则、冗余度都是方法论，住在 SKILL.md 里。

启动：
    python -m big.mcp_server            # stdio
"""

from __future__ import annotations

import json
import sys

# mcp 2.x 把 FastMCP 改名为 MCPServer 并挪了模块路径。两个版本都支持，
# 免得"用户装了哪个版本"决定项目能不能跑（这种坑最难跟人解释）。
try:                                                          # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:                                           # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from . import asr, creator, frames, registry, stream
from .comments import brief_comments, comment_stats, opinion_comments
# ⚠️ 必须起别名：下面 `def get_comments` 是**工具名**，同名导入会被模块级 def 重新绑定，
# 工具体内再调 get_comments(...) 就变成**调用自己** → TypeError 缺参数。
# 这个 bug 真实发生过：工具能注册、能列参数，但每次调用都失败。
from .comments import get_comments as _fetch_comments
from .danmaku import analyze as analyze_danmaku
from .danmaku import fetch_xml, parse as parse_danmaku
from .http import call_count as _http_calls
from .http import load_cookie, parse_bvid
from .media import OUT_ROOT as OUT_DIR
from .media import contact_sheet
from .search import scan_topic as _scan_topic
from .search import search_keyword
from .trust import flag_injection, untrusted_block
from .video import fmt_ts, get_info, get_transcript as _get_transcript

mcp = _Server("bilibili-info-gathering")


def _j(obj) -> str:
    """序列化 + **统一外壳兜底**（批次 D1）。

    为什么兜底放在这里而不是每个 return 处：工具里有 13 个
    `return _j({"error": ...})` 的早退分支（分P越界、缺参数、没有 ffmpeg、kind 写错）。
    它们不走 `_finish`，于是返回里既没有 `status` 也没有 `degraded`/`cost` ——
    调用方要么写两套解析，要么把失败当成功。
    在**唯一的序列化出口**兜一次，比在 13 处各改一遍可靠（漏一处就回到老问题）。

    status 三值：
      ok        完整拿到，没有降级
      degraded  拿到了，但有降级（封顶 / 只读了一部分 / 判读深度不足…）
      failed    没拿到（异常或前置条件不满足）
    `ok` 保留为 `status == "ok"` 的布尔快捷方式，向后兼容。
    """
    if isinstance(obj, dict):
        if "status" in obj:
            obj.setdefault("ok", obj.get("status") == "ok")
        elif obj.get("error"):
            obj["status"] = "failed"
            obj.setdefault("ok", False)
        else:
            obj["status"] = "degraded" if obj.get("degraded") else "ok"
            obj.setdefault("ok", obj["status"] == "ok")
        obj.setdefault("degraded", [])
        # cost 缺失时补零：失败分支没有真实开销，但**字段不该缺**——
        # 缺字段会让调用方的统一解析逻辑炸掉。
        obj.setdefault("cost", {"calls": 0, "comment_rows": 0,
                                "transcript_chars": 0, "est_output_tokens": 0})
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _err(e: Exception) -> str:
    # 记账要记到**真实调用者**的名字上：_finish 里的 frame(1) 在这里是 `_err`，
    # 所以显式把上一层的函数名传下去。
    try:
        name = sys._getframe(1).f_code.co_name
    except Exception:                                         # noqa: BLE001
        name = "unknown"
    return _finish({"error": f"{type(e).__name__}: {e}"}, status="failed", tool=name)


def _degraded(*items) -> list[str]:
    """降级信号统一成一个列表（批次 B1 会推广到所有工具）。

    为什么要有它：`references/exit-rules.md` 已经把「静默失效」列为必须防的故障，
    但代码层一直没有承载它的字段。降级说明散在各个工具自己的 note 里，
    Agent 很容易只看主字段而漏掉 note——**漏掉 note = 当成没降级**。
    """
    return [i for i in items if i]


def _est_tokens(text: str) -> int:
    """粗估 token 数。口径：**CJK 字符 × 0.7 + 其它字符 × 0.25**。

    为什么不用真 tokenizer：会给「任何 Python 3.10+ 直接跑起来」的前提加一个依赖。
    这个数的用途是**比较不同调用的相对成本**（哪个调用最烧上下文），
    **不是**精确 token 数，引用时必须带这个口径。
    """
    cjk = 0
    for ch in text:
        o = ord(ch)
        if (0x4E00 <= o <= 0x9FFF) or (0x3000 <= o <= 0x303F) or (0xFF00 <= o <= 0xFFEF):
            cjk += 1
    return int(cjk * 0.7 + (len(text) - cjk) * 0.25)


def _finish(payload: dict, degraded: list[str] | None = None, calls: int = 0,
            comment_rows: int = 0, transcript_chars: int = 0,
            status: str | None = None, tool: str | None = None) -> str:
    """统一返回外壳（批次 B1，批次 D1 扩为三值状态）：**每个**工具都带
    `status` / `degraded` / `ok` / `cost`。

    * `status`   —— `ok` / `degraded` / `failed`，**三值不是二值**。
      历史问题：`ok = not degraded` 把「失败」和「降级」压成一个布尔。实测
      `scan_topic` 因关键词封顶返回 ok=false，而数据完整可用 —— Agent 极易
      把它当失败而重跑，白烧一轮调用。
    * `degraded` —— 本次有哪些降级（无字幕 / 多P只读了一部分 / 判读深度不足 / 搜索封顶…）
    * `ok`       —— `status == "ok"` 的布尔快捷方式，保留兼容
    * `cost`     —— 本次的实际开销：`calls` 是**真的打了几次上游**
                   （口径见 http.call_count），不是估算；
                   `est_output_tokens` 是输出侧估算——DSH 插件 README 的一手口径是
                   **「输出比输入贵约 200 倍」**，所以输出侧才是真正会爆的预算。
    """
    d = [x for x in (degraded or []) if x]
    if not isinstance(payload, dict):
        # 统一外壳必须是顶层 dict——否则 degraded/ok/cost 没地方放。
        # 列表型返回（批量结果）一律包成 {"items": [...]}。
        payload = {"items": payload}
    st = status or ("degraded" if d else "ok")
    payload["degraded"] = d
    payload["status"] = st
    payload["ok"] = st == "ok"
    payload["cost"] = {
        "calls": calls,
        "comment_rows": comment_rows,
        "transcript_chars": transcript_chars,
        "est_output_tokens": 0,
    }
    # 先量一次体积再回填（回填只改一个数字，误差可忽略）
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    payload["cost"]["est_output_tokens"] = _est_tokens(body)
    # 记账（批次 D5）：追加进 .registry/cost-log.jsonl，用于回答"一轮花了多少"。
    # 取真实调用者名：正常路径下 frame(1) 就是工具函数。
    name = tool
    if not name:
        try:
            name = sys._getframe(1).f_code.co_name
        except Exception:                                     # noqa: BLE001
            name = "unknown"
    registry.append_cost(name, payload["cost"])
    return _j(payload)


def _pick_page(info: dict, page: int) -> tuple[int | None, str | None]:
    """返回 (cid, 降级说明)。越界与缺失都要**显式报错**，不能静默回落到 P1。

    为什么必须显式报错：多 P 视频（课程合集、系列讲座）只读 P1 而不吭声，
    Agent 会以为已经读完了整个视频——**结论直接缺一半**。
    """
    pages = info.get("pages") or []
    if not pages:
        return None, "无分P信息，取不到 cid"
    n = len(pages)
    try:
        page = int(page)
    except (TypeError, ValueError):
        return None, f"page={page!r} 不是整数"
    if page < 1 or page > n:
        return None, f"page={page} 越界（该视频共 {n} 个分P）"
    cid = pages[page - 1]["cid"]
    deg = None
    if n > 1:
        deg = f"该视频有 {n} 个分P，本次只读了第 {page} 部分（{page}/{n}）"
    return cid, deg


def _part_workdir(bv: str, page: int):
    """分 P 的音轨/视频缓存目录。

    为什么不能共用一个目录：音轨与视频流是**按 cid** 拉的，
    而缓存文件名原来只带 bvid。开了 `page` 参数之后，
    转写 P2 会复用 P1 已下载的 `audio.m4s` ⇒ **静默返回 P1 的转写**。
    所以这里按分P分目录，文件名再带上 cid 防"UP 改了分P顺序"导致的张冠李戴。
    """
    return OUT_DIR / bv / f"p{page}"


# ---------------------------------------------------------------- 阶段 1：广扫

@mcp.tool()
def search_videos(keyword: str, limit: int = 20, page: int = 1) -> str:
    """用**一个**关键词搜索视频，返回完整元数据（自带播放/弹幕/评论/收藏/标签，无需二次调用）。

    用途：试探某个词族能覆盖多少样本；或在 scan_topic 之后补充搜索。
    注意：limit 上限 50（上游实测值）。numResults 恒为封顶 1000，不能当总数用。
    常规广扫请直接用 scan_topic，不要连这个循环调用。
    """
    try:
        c0 = _http_calls()
        payload = search_keyword(keyword, limit, page, load_cookie())
        payload["untrusted"] = untrusted_block(
            "bilibili_search",
            ["items[].title", "items[].desc", "items[].tags[]", "items[].author"])
        return _finish(payload,
                       degraded=_degraded(
                           "该关键词命中数已封顶 1000，未穷尽（不代表只有 1000 条）"
                           if payload.get("capped") else None),
                       calls=_http_calls() - c0)
    except Exception as e:                                    # noqa: BLE001
        return _err(e)


@mcp.tool()
def scan_topic(keywords: list[str], per_query: int = 30, max_candidates: int = 40,
               comment_budget: int = 12, comment_n: int = 15,
               image_policy: str = "hot", image_top: int = 6,
               exclude_bvids: list[str] | None = None,
               families: dict | None = None,
               pages_per_keyword: int = 1,
               min_play: int | None = None,
               published_after: str | None = None,
               offset: int = 0,
               exclude_topic: str | None = None) -> str:
    """**阶段 1 广扫的唯一入口**：多关键词搜索 → 去重 → 排序 → 对头部候选拉评论（含低成本判图）。

    一次调用替代原来的「N 次搜索 + M 次元数据 + M 次评论」。返回：
      · candidates：全部候选的完整元数据 + 命中词族数 + 每千播放互动率
      · digest：只有排序靠前的 comment_budget 条带评论摘要与评论区结构指标
      · digest[].images：热评带图时的 160px 微表（判"这图是什么类型"，读不出数字）
      · coverage：每个关键词命中数、去重后总数、跨词族命中数、样本偏差声明
      · degraded / ok / cost：本次的降级信号、是否完整、以及实际开销

    参数取舍：
      keywords      建议 4–6 个，覆盖 同义词/上位词/下位词/黑话/英文 五类
      per_query     每个词取多少条（≤50；取 30 通常已进入长尾）
      comment_budget 花评论预算的条数。默认 12：再多也读不动，再少会漏掉侧面
      comment_n     每条候选读多少条热门评论。默认 15——8 条看不到"高赞反方"这一层
      image_policy  "hot"（默认）热评里有图就判；"off" 最快；"all" 不论有没有都尝试
      image_top     每条候选最多拼几张热评图，默认 6
      families      **强烈建议传**：{"上位词": [...], "产品名": [...], "黑话": [...]}。
                   排序键会改成「命中的不同语义族数 → 族内命中数 → 播放量」。
                   不传时 `kw_families` 返回 null——代码无法凭空知道哪些词属于同一族。
      pages_per_keyword 每个词翻几页。**长尾在第 2 页**——实测每词 page_size=30
                   时各命中 26–30 条，恰好卡在上限，长尾根本没取到。
      min_play      排除播放量低于此值的候选（收窄候选池，不是扩大）
      published_after  "YYYY-MM-DD"，排除更早的内容（算法偏好新内容，"老"≠"好"）

    offset（批次 D2）
      跳过排序后的前 N 条，取下一批。**用来解决「召回 835 条、只返回 50 条」的断层**：
      候选池没变，只是分页看。`coverage.next_offset` 给了下一页的起点；
      `coverage.rest_preview` 是未展示候选的一行摘要（不含评论，成本≈0），
      可以先扫一眼再决定要不要翻页。

    exclude_topic（批次 D3 · 增量模式）
      传议题名，自动把 `.registry/videos.json` 里**该议题已登记过的 BV** 排除。
      同议题第二轮用它，避免把上次已分析的条目再扫一遍。
      与 `exclude_bvids` 可叠加使用。

    排序规则（传了 families 时，批次 D2）：标题/简介**强命中**的不同族数 →
    命中的不同族数 → 命中关键词数 → 字段权重 → 播放量(log)。
    强命中族数是为了压掉「靠标签蹭进来」的候选——B 站搜索是模糊匹配，
    标签沾边就能命中很多关键词。
    """
    try:
        c0 = _http_calls()
        ex = list(exclude_bvids or [])
        known_n, known_err = 0, None
        if exclude_topic:
            try:
                known = [v.get("bvid") for v in
                         (registry.list_videos(topic=exclude_topic) or [])]
                known = [b for b in known if b]
                known_n = len(known)
                ex += known
            except Exception as e:                            # noqa: BLE001
                # 资产库读不到不该让整次扫描失败——但要如实降级，不能装作排除了。
                known_err = f"{type(e).__name__}: {e}"
        # ⚠️ 全部用**关键字参数**传：位置参数一旦上游改签名就会**静默错位**
        # （实测踩过：cookie 被当成 pages_per_keyword 传进去，直接把整个调用打废）。
        payload = _scan_topic(
            keywords=keywords, per_query=per_query, max_candidates=max_candidates,
            comment_budget=comment_budget, comment_n=comment_n,
            image_policy=image_policy, image_top=image_top,
            exclude_bvids=ex or None, families=families,
            pages_per_keyword=pages_per_keyword, min_play=min_play,
            published_after=published_after, offset=offset, cookie=load_cookie())
        if exclude_topic:
            payload["incremental"] = {
                "exclude_topic": exclude_topic,
                "excluded_known": known_n if known_err is None else None,
                "note": (f"已排除该议题在 videos.json 里登记过的 {known_n} 条视频（增量模式）"
                         if known_err is None else
                         "资产库读取失败，本轮**没有**排除已登记视频——当成全量扫描看"),
            }
        payload["untrusted"] = untrusted_block(
            "bilibili_search",
            ["candidates[].title", "candidates[].desc", "candidates[].tags[]",
             "candidates[].author", "candidates[].hit_by[]",
             "candidates[].digest.top[].text"])
        cov = payload.get("coverage") or {}
        capped = [f"关键词「{x.get('keyword')}」命中数已封顶 1000，未穷尽"
                  for x in (cov.get("per_keyword") or []) if x.get("capped")]
        rows = 0
        for d in (payload.get("candidates") or []):
            dig = d.get("digest") or {}
            rows += len(dig.get("top") or [])
        return _finish(payload, degraded=_degraded(
            *capped,
            f"exclude_topic 读取失败，本轮未做增量排除：{known_err}" if known_err else None),
            calls=_http_calls() - c0, comment_rows=rows)
    except Exception as e:                                    # noqa: BLE001
        return _err(e)


@mcp.tool()
def get_metadata(bvids: list[str]) -> str:
    """批量取**完整**元数据（含 cid/pages、标签、简介全文）。

    ⚠️ 广扫阶段不需要它——搜索结果已经带了元数据。只有这两种情况用：
      ① 你只有一个 BV 号，没有搜索结果；
      ② 需要 cid 去拉字幕/弹幕（get_transcript / get_danmaku 内部已自动处理，通常也不用）。

    🔴 **分 P 视频必看**：返回里的 `n_pages` 是分P总数，`pages` 是完整分P清单
    （每项含 page / cid / title / duration）。`n_pages > 1` 时，
    取字幕/弹幕/ASR/抽帧都要显式传 `page=`，否则**默认只读第 1 部分**。
    `pic` 是封面图 URL（批次 C3 放回来的）——最便宜的一手形态信息。
    """
    cookie = load_cookie()
    c0 = _http_calls()
    out = []
    for raw in bvids:
        try:
            bv = parse_bvid(raw)
            info = get_info(bv, cookie)
            pages = info.get("pages") or []
            n = len(pages)
            out.append({k: info[k] for k in
                        ("bvid", "aid", "title", "author", "author_mid", "duration",
                         "pubdate", "tname", "stats", "tags", "pic")}
                       | {"cid": (pages[0]["cid"] if pages else None),
                          "pages": pages,
                          "n_pages": n,
                          "degraded": _degraded(
                              f"该视频有 {n} 个分P，本次只返回了第 1 部分的 cid"
                              if n > 1 else None),
                          "untrusted": untrusted_block(
                              "bilibili_metadata",
                              ["title", "pages[].title", "tags[]"])})
        except Exception as e:                                # noqa: BLE001
            out.append({"input": raw, "error": f"{type(e).__name__}: {e}"})
    return _finish({"items": out, "n": len(out)},
                   degraded=_degraded(*[d for it in out for d in it.get("degraded", [])]),
                   calls=_http_calls() - c0)


# ---------------------------------------------------------------- 阶段 2：深挖

def _transcript_block(bv: str, info: dict, cid: int, deg: str | None,
                      query: str | None, head_ratio: float | None,
                      max_chars: int) -> tuple[dict, list[str], int]:
    """字幕通道的**共用实现**——`get_transcript` 与 `deep_read` 都走这里。

    为什么要抽出来：`deep_read` 是"一次调用聚合多通道"的组合通道，
    如果它去调 `get_transcript` 那个**工具函数**，就变成了"工具调工具"——
    而那正是本项目踩过两次的坑（同名遮蔽 / 签名对不上）。
    所以共用逻辑放在**私有函数**里，两个工具各自调用。
    """
    tr = _get_transcript(bv, cid, load_cookie())
    n_pages = len(info.get("pages") or [])
    if not tr:
        payload = {
            "bvid": bv, "title": info["title"], "duration": fmt_ts(info["duration"]),
            "data_source": None, "language": None, "mode": "none",
            "n_total_lines": 0, "n_returned": 0, "truncated": False,
            "transcript": None,
            "n_pages": n_pages,
            "note": "无字幕（或未登录导致不可见）。不要把「没有字幕」说成「视频没讲」。",
            "untrusted": untrusted_block("bilibili_metadata", ["title", "pages[].title"]),
            "lines": [],
        }
        return payload, _degraded(deg, "本次无字幕"), 0

    lines = tr["lines"]
    total = len(lines)
    picked = lines
    mode = "full"
    q_hit: list[str] = []
    q_miss: list[str] = []
    if query:
        keys = [k for k in str(query).replace("，", " ").replace(",", " ").split() if k]
        idx = [i for i, l in enumerate(lines) if any(k in l["content"] for k in keys)]
        # 哪些词命中、哪些没命中——**必须报出来**：
        # "一个都没命中"有两种完全不同的原因（视频没讲 / 我的用词不对），
        # 不报出来的话 Agent 只能靠猜，而猜错的代价是把"没查到"当成"没有"。
        for k in keys:
            (q_hit if any(k in l["content"] for l in lines) else q_miss).append(k)
        keep: set[int] = set()
        for i in idx:
            keep.update(range(max(0, i - 2), min(total, i + 3)))
        picked = [lines[i] for i in sorted(keep)]
        mode = f"query:{query}"
    elif head_ratio:
        r = max(0.05, min(1.0, float(head_ratio)))
        picked = lines[:max(1, int(total * r))]
        mode = f"head:{r}"

    out_lines, used = [], 0
    for l in picked:
        s = f"[{fmt_ts(l['from'])}] {l['content']}"
        if used + len(s) > max_chars:
            break
        out_lines.append(s)
        used += len(s)
    payload = {
        "bvid": bv, "title": info["title"], "duration": fmt_ts(info["duration"]),
        "data_source": tr["data_source"], "language": tr["language"],
        "mode": mode, "n_total_lines": total, "n_returned": len(out_lines),
        "truncated": used >= max_chars,
        "n_pages": n_pages,
        "untrusted": untrusted_block("bilibili_subtitle",
                                     ["lines[]", "title", "pages[].title"]),
        "lines": out_lines,
    }
    degs = _degraded(deg, "字幕按 max_chars 截断，本次没读完" if used >= max_chars else None)
    if query:
        payload["query_matched"] = q_hit
        payload["query_missed"] = q_miss
        if not q_hit:
            # 🔴 静默失败：query 一个词都没命中时，返回是**空的**，
            # 但没有任何信号说"是没找到"还是"根本没讲"。
            # 实测（2026-10-21 二游第二轮）：一条 387 行字幕用 6 个词组查询，
            # 返回 n_returned=0 且 degraded 为空 —— Agent 完全看不出发生了什么。
            degs.append(
                f"query 的关键词在这条字幕里一个都没命中（{'、'.join(q_miss) or query}）。"
                f"可能是**用词不同**（AI 字幕的措辞与人不同）或**视频确实没讲**——"
                f"换单个词/同义词重试，或用 head_ratio 通读前 20% 确认。")
    return payload, degs, used


def _comments_block(bv: str, info: dict, n: int, detail: str,
                    sanitize: str) -> tuple[dict, list[str], int]:
    """评论通道的**共用实现**——`get_comments` 与 `deep_read` 都走这里。"""
    cmts, total = _fetch_comments(int(info["aid"]), n, "hot", load_cookie())
    st = comment_stats(cmts)
    verdict = st.get("picture_verdict") if st.get("judge_depth", 0) >= 100 else None
    payload = {
        "bvid": bv, "title": info["title"], "reply_total": total,
        "stats": st,
        "picture_verdict": verdict,
    }
    if detail == "full":
        payload["comments"] = cmts
    elif detail == "opinion":
        payload["comments"] = opinion_comments(cmts, n)
        payload["note"] = ("opinion 档位只含顶层评论——三轴（普遍度/认同度/争议度）"
                           "全部可由顶层计算，争议度用的 rcount 只存在于顶层。"
                           "需要读楼中楼争论内容时改用 detail='full'。")
    else:
        payload["comments"] = brief_comments(cmts, n)
    flagged = 0
    if (sanitize or "flag").lower() != "off":
        flagged = _flag_injections(payload.get("comments") or [])
    payload["untrusted"] = untrusted_block(
        "bilibili_comment", ["comments[].text", "comments[].content", "title"])
    degs = _degraded(
        f"评论判读深度只有 {st.get('judge_depth')} 条（<100），"
        f"图片占比阈值不可比，picture_verdict 不可用"
        if st.get("judge_depth", 0) < 100 else None,
        f"检出 {flagged} 条疑似指令注入的评论（已标记 injection_risk，未删改原文）"
        if flagged else None)
    return payload, degs, len(payload.get("comments") or [])


@mcp.tool()
def get_comments(bvid: str, n: int = 10, detail: str = "brief",
                 sanitize: str = "flag") -> str:
    """取顶层评论 + 评论区结构指标。

    detail 三档，按成本递增：

    * **"brief"**（默认，广扫用）：前 n 条顶层，正文截断 90 字，~4.3k token/100 条
    * **"opinion"**（**观点分布用**）：前 n 条顶层 + 完整正文 + 最小字段集
      （text/likes/rcount/author/lv/pics），~7.2k token/100 条。
      **只取顶层就够算三轴**——争议度用的 rcount 只存在于顶层。
      比 full 省 71%，且正文不截断（不毁观点簇判断）
    * **"full"**：全部原始字段（含楼中楼、pictures URL 明细），~25.3k token/100 条。
      只在要引用原话、或要读楼中楼争论内容时用

    stats 里的关键量（判读口径固定在前 100 条顶层，不随 n 漂移）：
      picture_like_share  「图片评论占总赞」——决定要不要读图（≥15% must，≥5% sample）
      reply_intensity     平均每层楼中楼数 —— 争论强度（点赞是"认同"，回复是"争议"）
      reply_to_like       楼中楼总数/赞总数 —— 评论活跃度
      level_count         等级分布 —— 社区结构（高等级占比高 = 老用户为主）
      locations           IP 属地分布 —— 地域结构，**属于个人信息，不要写进可分享的产物**

    n 是顶层评论条数上限，楼中楼不占名额。n<100 时 picture_verdict 不返回（口径不可比）。

    sanitize（默认 "flag"）：给命中指令注入特征的评论追加 `injection_risk` 字段。
    **只标记、不删改原文**——删改会破坏可溯源性（报告要能引用原话）。
    设 "off" 可关闭。实测 B 站评论区存在成规模的、专门针对 AI 账号的注入。

    ⚠️ **命中不等于"这条在攻击 Agent"**：本检测是纯关键词匹配，
    只能当"值得再看一眼"的提示，不能当证据。
    """
    try:
        c0 = _http_calls()
        cookie = load_cookie()
        bv = parse_bvid(bvid)
        info = get_info(bv, cookie)
        payload, degs, rows = _comments_block(bv, info, n, detail, sanitize)
        return _finish(payload, degraded=degs, calls=_http_calls() - c0,
                       comment_rows=rows)
    except Exception as e:                                    # noqa: BLE001
        return _err(e)


def _flag_injections(rows: list[dict]) -> int:
    """给命中的评论追加 `injection_risk` 字段，**不改原文**。返回命中条数。"""
    n = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        text = r.get("text") or r.get("content") or ""
        risk = flag_injection(text)
        if risk:
            r["injection_risk"] = risk["risk"]
            r["risk_reasons"] = risk["reasons"]
            n += 1
    return n


@mcp.tool()
def get_transcript(bvid: str, query: str | None = None, head_ratio: float | None = None,
                   max_chars: int = 8000, page: int = 1) -> str:
    """取字幕，**默认带压缩**——这是阶段 2 里最容易把上下文打爆的一步。

    三种用法，按成本从低到高：
      head_ratio=0.2   只读前 20%（离场规则的判断依据：核心关键词还没出现就退出）
      query="关键词"    只返回命中句 ±2 句上下文（省 80–95% token，**优先用这个**）
      都不给           返回全量（长视频会非常贵，慎用）

    返回 data_source 字段：
      "subtitle"     人工 CC —— 可以当引用
      "ai_subtitle"  AI 字幕 —— **不可当引用**。技术类视频术语错一个，后面所有推论
                     都建立在错的前提上。用它定位、不用它举证。

    page：多 P 视频取第几部分（默认 1）。**越界会明确报错，不会静默回落到 P1**。
    分P数 > 1 时返回里带 `degraded` 说明"本次只读了第几部分"。
    """
    try:
        c0 = _http_calls()
        cookie = load_cookie()
        bv = parse_bvid(bvid)
        info = get_info(bv, cookie)
        cid, deg = _pick_page(info, page)
        n_pages = len(info.get("pages") or [])
        if cid is None:
            return _j({"error": deg, "bvid": bv, "n_pages": n_pages,
                       "page_requested": page,
                       "untrusted": untrusted_block("bilibili_metadata",
                                                   ["title", "pages[].title"])})
        payload, degs, chars = _transcript_block(bv, info, cid, deg, query,
                                                 head_ratio, max_chars)
        payload["page_used"] = int(page)
        return _finish(payload, degraded=degs, calls=_http_calls() - c0,
                       transcript_chars=chars)
    except Exception as e:                                    # noqa: BLE001
        return _err(e)


@mcp.tool()
def get_danmaku(bvid: str, top: int = 5, page: int = 1) -> str:
    """取弹幕并压成"位置信号"：密度曲线、最密时段+样本、高重复句、披露关键词。

    为什么值得单独一条通道：弹幕是**秒级**的，能直接对上口播段落；评论只有发表时间。
    而且**广告披露常常只在弹幕里**（实测某期简介无标注，弹幕 t=635s 直接写了"本期推广…"）。

    用途限制（重要）：只用在两处——
      ① 「延伸发散」：某段弹幕密度暴涨，往往就是观众认为信息量最大的地方；
      ② 「评论区结构性偏差」：弹幕 vs 评论说的事不一样，说明受众分层。
    不要为它单开一章，也不要拿弹幕当"多数人意见"的证据（弹幕是即时反应，不是判断）。

    page：多 P 视频取第几部分（默认 1）。越界明确报错，不静默回落到 P1。
    """
    try:
        c0 = _http_calls()
        cookie = load_cookie()
        bv = parse_bvid(bvid)
        info = get_info(bv, cookie)
        cid, deg = _pick_page(info, page)
        n_pages = len(info.get("pages") or [])
        if cid is None:
            return _j({"error": deg, "bvid": bv, "n_pages": n_pages,
                       "page_requested": page})
        raw = fetch_xml(cid, bv)
        items = parse_danmaku(raw)
        res = analyze_danmaku(items, info.get("duration") or 0, top=top)
        res.update({"bvid": bv, "title": info["title"],
                    "stat_danmaku": (info.get("stats") or {}).get("danmaku"),
                    "stat_reply": (info.get("stats") or {}).get("reply"),
                    "n_pages": n_pages, "page_used": page,
                    "untrusted": untrusted_block(
                        "bilibili_danmaku",
                        ["peaks[].samples[]", "repeated[].text",
                         "disclosure.items[]", "title"])})
        return _finish(res,
                       degraded=_degraded(deg,
                                         "弹幕为空（可能被关闭或需要登录）"
                                         if not items else None),
                       calls=_http_calls() - c0)
    except Exception as e:                                    # noqa: BLE001
        return _err(e)


@mcp.tool()
def get_comment_images(bvid: str, top_n: int = 0, comments: int = 100) -> str:
    """评论区图片：阈值判读 → 并行下载 → 拼成带编号的联系表，返回格号清单。

    top_n=0 时按阈值自动：图片占总赞 ≥15% 下 20 张，≥5% 下 10 张，低于 5% 直接跳过。

    用法：先看联系表整版（一次视觉扫描，判断哪几格有信息量），再按 #编号回读原图。
    **数字与结论只认原图**——缩略图只用于定位，不能作为引用依据（长图尤其）。
    """
    try:
        c0 = _http_calls()
        cookie = load_cookie()
        bv = parse_bvid(bvid)
        info = get_info(bv, cookie)
        # ⚠️ 必须调 `_fetch_comments`（库函数），不能调 `get_comments`（**同名工具**）——
        # 工具签名是 (bvid, n, detail)，库函数是 (aid, n, sort, cookie)。
        # 调错会拿到 TypeError，且**每次调用都失败**（工具名把导入名重新绑定）。
        cmts, total = _fetch_comments(int(info["aid"]), max(comments, 100), "hot", cookie)
        st = comment_stats(cmts)
        outdir = OUT_DIR / bv
        res = contact_sheet(bv, cmts, st, outdir, top_n=top_n)
        res.update({"bvid": bv, "title": info["title"], "reply_total": total,
                    "picture_like_share": st.get("picture_like_share"),
                    "picture_share": st.get("picture_share"),
                    "judge_depth": st.get("judge_depth"),
                    "untrusted": untrusted_block(
                        "bilibili_comment", ["grid[].text", "title"])})
        return _finish(res,
                       degraded=_degraded(
                           f"评论判读深度只有 {st.get('judge_depth')} 条（<100），"
                           f"图片阈值不可比" if st.get("judge_depth", 0) < 100 else None,
                           "图片占比低于阈值，本次没有拉图" if not res.get("grid") else None),
                       calls=_http_calls() - c0,
                       comment_rows=len(cmts))
    except Exception as e:                                    # noqa: BLE001
        return _err(e)


# ---------------------------------------------------------------- 补齐文本与画面

@mcp.tool()
def transcribe_audio(bvid: str, page: int = 1) -> str:
    """**没有字幕时的第三条路**：下载音轨 → 本地 ASR 自建转写。

    什么时候用
    ----------
    * `get_transcript` 返回 `transcript: null`（无字幕）
    * 或字幕是 `ai_subtitle` 且**错误多到不能作为依据**

    ⚠️ 判「有没有字幕」必须带登录态：未登录时 B站返回空字幕列表，
    **看起来像"没字幕"**（实测 6 个视频 + 195 个分P 全部返回空列表）。

    转写引擎是**使用者自备**的（模型体积大，不进仓库）。
    两种接法：
      ① pip install faster-whisper（首次会下模型，large-v3 约 3 GB）
      ② 设 BIG_ASR_CMD 指向你已有的转写命令（含 {audio} {out} 占位符）
    **两个都没有时**，本工具返回 `status="no_backend"` + 完整安装指引，
    并明确告诉你要把「该视频无可用字幕」记成结论里的缺口——
    **不静默失败**（静默失败会让 Agent 误以为"这视频没讲"）。

    返回结构与 `get_transcript` 一致（data_source="asr"），可直接接进后续流程。
    ⚠️ 本地 ASR 同样会误转，**处理标准与 AI 字幕完全一致**（数字/术语要标注
    "建议回原片核对"），并在引用时注明 `engine`（转写模型）。

    page：多 P 视频转写第几部分（默认 1）。**这个参数以前是假的**——
    签名里收了 page 但实现里写死读 P1。现在按 page 取对应的 cid，
    音轨缓存也按分P分目录（否则 P2 会复用 P1 的音轨，静默返回 P1 的转写）。
    """
    try:
        c0 = _http_calls()
        cookie = load_cookie()
        bv = parse_bvid(bvid)
        info = get_info(bv, cookie)
        cid, deg = _pick_page(info, page)
        n_pages = len(info.get("pages") or [])
        if cid is None:
            return _j({"error": deg, "bvid": bv, "n_pages": n_pages,
                       "page_requested": page})
        page = int(page)

        backend, detail = asr.detect_backend()
        if backend is None:
            return _finish({
                "status": "no_backend",
                "bvid": bv,
                "title": info["title"],
                "error": detail,
                "install_hint": asr.install_hint(),
                "n_pages": n_pages, "page_used": page,
                "what_to_do": ("把「该视频无可用字幕」记进结论的缺口声明，"
                               "不要当成「视频没讲」；"
                               "装好后重跑本工具可补上。"),
            }, degraded=_degraded(deg, "本机没有可用的 ASR 后端，未转写"),
                calls=_http_calls() - c0)

        workdir = _part_workdir(bv, page)
        workdir.mkdir(parents=True, exist_ok=True)
        # 文件名带 cid：分P顺序可能被 UP 改，只有 cid 能唯一确定这是哪一路流
        audio = workdir / f"audio_{cid}.m4s"
        dl_note = None
        if not audio.exists() or audio.stat().st_size == 0:
            size, secs = stream.fetch_audio(bv, cid, audio, cookie)
            dl_note = f"音轨已下载 {size / 1024 / 1024:.1f} MB（{secs}s）"
        else:
            dl_note = f"复用已下载音轨 {audio.stat().st_size / 1024 / 1024:.1f} MB"

        tr = asr.transcribe(audio, workdir)
        lines = tr["lines"]
        chars = sum(len(l["content"]) for l in lines)
        return _finish({
            "bvid": bv, "title": info["title"],
            "data_source": "asr", "language": tr.get("language", "zh"),
            "engine": tr.get("engine"), "note": dl_note,
            "n_total_lines": len(lines),
            "n_pages": n_pages, "page_used": page,
            "untrusted": untrusted_block("bilibili_asr", ["lines[]", "title"]),
            "lines": [{"from": l["from"], "to": l["to"], "content": l["content"]}
                      for l in lines],
            "caveat": ("本地 ASR 与 AI 字幕同样会误转：数字/术语/人名一律标注"
                       "「建议回原片核对」；引用时注明 engine（转写模型）。"),
        }, degraded=_degraded(deg), calls=_http_calls() - c0, transcript_chars=chars)
    except Exception as e:                                    # noqa: BLE001
        return _err(e)


@mcp.tool()
def grab_frames(bvid: str, at_seconds: list[float], quality: int = 720,
                page: int = 1) -> str:
    """**按需抽帧**：只在字幕出现「明显影响理解且无法纠正」的错误时用。

    什么时候用
    ----------
    字幕/口播里出现数字、专有名词、参数名，且**这个错会改变结论**——
    不是"可能读错"，是"读错了我就没法下判断"。这时抽该时间点附近的一张
    画面确认。**数字以画面为准，不以逐字稿为准。**

    ⛔ **不要全片抽帧。** 全片抽帧产出的是"我看过这个视频"，
    不是"我核对了这个数字"。先用判据筛，命中才抽。

    quality：720 看画面结构够用；**要读表格/榜单里的数字必须 1080**
    （实测 640×360 只能认出几个大字，格子里全是糊的）。

    page：多 P 视频抽第几部分（默认 1）。视频流缓存按分P分目录，
    否则抽 P2 会复用 P1 的视频流，抽出来的画面根本不是那一段。

    需要 ffmpeg。没装时返回明确指引，不静默失败。

    返回 {sheet, grid[]}：grid 里每项含 #编号 / sec / ts / file，
    **按编号回读原图取数字**（联系表只用于定位）。
    """
    try:
        c0 = _http_calls()
        cookie = load_cookie()
        bv = parse_bvid(bvid)
        info = get_info(bv, cookie)
        cid, deg = _pick_page(info, page)
        n_pages = len(info.get("pages") or [])
        if cid is None:
            return _j({"error": deg, "bvid": bv, "n_pages": n_pages,
                       "page_requested": page})
        page = int(page)
        if not at_seconds:
            return _j({"error": "at_seconds 不能为空——本工具是段级抽帧，"
                                "只在你已定位到具体时间点时调用"})

        if not frames.ffmpeg_bin():
            return _j({"error": frames.ffmpeg_hint(),
                       "bvid": bv,
                       "what_to_do": ("在结论里注明「该处字幕疑似有误，但未做画面核对」；"
                                      "或安装 ffmpeg 后重试。")})

        workdir = _part_workdir(bv, page)
        workdir.mkdir(parents=True, exist_ok=True)
        video = workdir / f"video{quality}_{cid}.mp4"
        dl_note = None
        if not video.exists() or video.stat().st_size == 0:
            size, secs = stream.fetch_video(bv, cid, video, height=quality, cookie=cookie)
            dl_note = f"视频流已下载 {size / 1024 / 1024:.1f} MB（{secs}s）"
        else:
            dl_note = f"复用已下载视频 {video.stat().st_size / 1024 / 1024:.1f} MB"

        got = frames.extract_at(video, at_seconds, workdir / "frames", prefix="f")
        sheet = frames.contact_sheet(got, workdir)
        seek = frames.verify_seek(got)
        failed = [f for f in got if not f.get("ok")]
        return _finish({
            "bvid": bv, "title": info["title"], "note": dl_note,
            "quality": quality,
            "n_pages": n_pages, "page_used": page,
            "untrusted": untrusted_block("bilibili_frame", ["title"]),
            "video_file": str(video),
            "sheet": sheet.get("sheet"),
            "grid": sheet.get("grid") or [],
            "failed": [{"sec": f["sec"], "err": f["err"]} for f in failed],
            "seek_check": seek,
            "caveat": "联系表只用于定位；**数字与结论只认原图**（grid[].file）。",
        }, degraded=_degraded(
            deg,
            f"{len(failed)} 帧抽取失败" if failed else None,
            "定位自检未通过（多帧完全相同）——这批图不能用" if not seek.get("ok", True) else None),
            calls=_http_calls() - c0)
    except Exception as e:                                    # noqa: BLE001
        return _err(e)


# ---------------------------------------------------------------- 组合通道（批次 C2）

@mcp.tool()
def deep_read(bvid: str, channels: list[str] | None = None,
              transcript_query: str | None = None,
              transcript_head_ratio: float | None = 0.2,
              transcript_max_chars: int = 6000,
              comment_n: int = 60, comment_detail: str = "opinion",
              frames_at: list[float] | None = None, page: int = 1,
              quality: int = 720) -> str:
    """**一次调用聚合多条通道**：字幕 + 评论 + 弹幕 + 封面（可选抽帧）。

    为什么要有它
    ------------
    本项目有 5 条取数通道，但每条要**单独一次调用**：Agent 要自己编排、
    每次都要重新拼参数、而且每条通道都要先拉一次视频元数据。
    DSH 那边的实现把多路整合成"一次看懂"（字幕/弹幕/热评/封面/抽帧）。
    本工具是同构的等价物：**少几轮往返**，且一次拿到各通道的**统一降级信号**。

    channels（默认 ["transcript","comments","danmaku","cover"]）：
      transcript  字幕（受 transcript_query / transcript_head_ratio / transcript_max_chars 控制）
      comments    评论（受 comment_n / comment_detail 控制）
      danmaku     弹幕（密度与披露线索）
      cover       封面 URL（批次 C3 放回来的 `pic`，成本最低的一路）
      frames      抽帧（**只在给了 frames_at 时**才做；需要 ffmpeg）

    返回：`{bvid, title, channels: {...}, degraded: [...], ok, cost}`
    每条通道的完整载荷在 `channels.<name>` 里，与单通道工具**同构**——
    所以 deep_read 的结果可以直接当单通道结果用，不会出现"两套结构"。

    ⚠️ 成本：一次调用可能打多次上游（`cost.calls` 会告诉你实际打了几次）。
    字幕仍是最大头——**强烈建议给 transcript_query 或 transcript_head_ratio**。
    """
    try:
        c0 = _http_calls()
        chans = [str(x) for x in (channels or
                                  ["transcript", "comments", "danmaku", "cover"])]
        cookie = load_cookie()
        bv = parse_bvid(bvid)
        info = get_info(bv, cookie)
        cid, deg = _pick_page(info, page)
        n_pages = len(info.get("pages") or [])
        out: dict = {}
        degs: list[str] = []
        rows = 0
        chars = 0

        if cid is None:
            return _j({"error": deg, "bvid": bv, "n_pages": n_pages,
                       "page_requested": page})

        if "transcript" in chans:
            p, d, chars = _transcript_block(bv, info, cid, deg, transcript_query,
                                            transcript_head_ratio, transcript_max_chars)
            p["page_used"] = int(page)
            out["transcript"] = p
            degs += d
        if "comments" in chans:
            p, d, rows = _comments_block(bv, info, comment_n, comment_detail, "flag")
            out["comments"] = p
            degs += d
        if "danmaku" in chans:
            raw = fetch_xml(cid, bv)
            items = parse_danmaku(raw)
            res = analyze_danmaku(items, info.get("duration") or 0, top=5)
            res.update({"bvid": bv, "title": info["title"],
                        "untrusted": untrusted_block(
                            "bilibili_danmaku",
                            ["peaks[].samples[]", "repeated[].text",
                             "disclosure.items[]", "title"])})
            out["danmaku"] = res
            if not items:
                degs.append("弹幕为空（可能被关闭或需要登录）")
        if "cover" in chans:
            # 批次 C3：封面是"六路看懂"里最便宜的一路——一个 URL + 一次图读。
            out["cover"] = {
                "url": info.get("pic"),
                "note": "封面只用于判断内容形态（截图/标题卡/立绘），不作为事实依据。",
            }
            if not info.get("pic"):
                degs.append("该视频没有封面图")
        if "frames" in chans:
            if not frames_at:
                degs.append("请求了 frames 通道但没给 frames_at——没有抽帧")
            elif not frames.ffmpeg_bin():
                out["frames"] = {"error": frames.ffmpeg_hint(),
                                 "what_to_do": "在结论里注明该处未做画面核对；或安装 ffmpeg 后重试。"}
                degs.append("缺 ffmpeg，未抽帧")
            else:
                workdir = _part_workdir(bv, int(page))
                workdir.mkdir(parents=True, exist_ok=True)
                video = workdir / f"video{quality}_{cid}.mp4"
                if not video.exists() or video.stat().st_size == 0:
                    stream.fetch_video(bv, cid, video, height=quality, cookie=cookie)
                got = frames.extract_at(video, frames_at, workdir / "frames", prefix="f")
                sheet = frames.contact_sheet(got, workdir)
                out["frames"] = {"sheet": sheet.get("sheet"),
                                  "grid": sheet.get("grid") or [],
                                  "seek_check": frames.verify_seek(got),
                                  "caveat": "联系表只用于定位；数字与结论只认原图。"}

        payload = {
            "bvid": bv, "title": info["title"],
            "n_pages": n_pages, "page_used": int(page),
            "channels": out,
            "channels_requested": chans,
            "channels_returned": sorted(out.keys()),
            "untrusted": untrusted_block(
                "bilibili_multi",
                ["channels.transcript.lines[]", "channels.comments.comments[].text",
                 "channels.danmaku.peaks[].samples[]", "title"]),
        }
        if deg:
            degs.insert(0, deg)
        return _finish(payload, degraded=_degraded(*degs), calls=_http_calls() - c0,
                       comment_rows=rows, transcript_chars=chars)
    except Exception as e:                                    # noqa: BLE001
        return _err(e)


@mcp.tool()
def get_creator_content(mid: int, section: str = "overview", page: int = 1,
                        page_size: int = 30, order: str = "pubdate") -> str:
    """**UP 主通道**：档案 / 投稿列表 / 合集 / 系列 / 动态。

    为什么需要这条通道
    ------------------
    议题收敛到 2–3 个核心 UP 之后，"**这个 UP 后续又发了什么**"是自然的下一步追问。
    本项目原来没有这个能力——只能反复 `search_videos` 搜关键词，
    但搜索是**按内容召回**的，召回不到"某人后来发的某条"。

    section：
      "overview"     UP 档案（昵称/签名/等级/粉丝数/认证）
      "videos"       投稿列表（按 order 排序，order 可选 pubdate / click / dm）
      "collections"  合集（season）
      "series"       系列（series）
      "dynamics"     动态流（最近发的动态，含视频引用）

    ⚠️ **端点可用性**（2026-10-21 本机实测）：space 系列端点**必须带 WBI 签名**，
    不带就是 -799「请求过于频繁」。合集/系列的正确路径是
    `/x/polymer/**web-space**/seasons_series_list`（`polymer/space/...` 是 404）。
    """
    try:
        c0 = _http_calls()
        body = creator.creator_content(mid, section, page, page_size, order,
                                       load_cookie())
        body["untrusted"] = untrusted_block(
            "bilibili_creator",
            ["items[].title", "items[].text", "items[].author", "name", "sign"])
        return _finish(body, degraded=body.get("degraded") or [],
                       calls=_http_calls() - c0)
    except Exception as e:                                    # noqa: BLE001
        return _err(e)


@mcp.tool()
def scan_untrusted_text(texts: list[str]) -> str:
    """**注入检测**（批次 C6 · 信任边界第二层）：扫描任意外部文本。

    为什么要有这个工具
    ------------------
    前面所有通道的注入检测都发生在**取数时**。但有时文本不是从本项目取来的：
    * 别的 MCP 工具（上游 bilibili-mcp）返回的文本
    * 用户粘进来的网页/摘录
    * 你自己从别处复制的段落
    这些都**不经过** `get_comments` 的 sanitize ⇒ 不会被标记。
    本工具让 Agent 能主动扫一遍。

    ⚠️ **命中不等于"这条在攻击 Agent"**：本检测是纯关键词匹配，
    只能当"值得再看一眼"的提示，不能当证据。真正的判断要人/Agent 看原文。
    误报是真实存在的（2026-10-21 实测：一条洛谷体长评因为「忽视」与「之前」
    相隔很远而被独立匹配到一起，现已改成邻近匹配）。

    返回：{n, flagged: [{index, risk, reasons, preview}], n_flagged, degraded}
    """
    try:
        rows = []
        for i, t in enumerate(texts or []):
            risk = flag_injection(str(t or ""))
            if risk:
                rows.append({"index": i, "risk": risk["risk"],
                             "reasons": risk["reasons"],
                             "preview": str(t)[:120]})
        return _finish({
            "n": len(texts or []),
            "n_flagged": len(rows),
            "flagged": rows,
            "note": "命中只说明『值得再看一眼』，不等于攻击；判断要看原文。",
        }, degraded=_degraded(
            f"检出 {len(rows)} 条疑似指令注入的文本（已标记，未删改原文）" if rows else None))
    except Exception as e:                                    # noqa: BLE001
        return _err(e)


# ---------------------------------------------------------------- 资产库

@mcp.tool()
def registry_get(kind: str = "stats", mid: int | None = None, bvid: str | None = None,
                 domain: str | None = None, topic: str | None = None,
                 window: str = "all") -> str:
    """读资产库。**每次任务开始前先查一遍**——已有评级的 UP 主直接沿用。

    kind:
      "stats"     总览（条数、评级分布、评级变动、议题分布、经验库体积、议题档案数、
                  **上一轮开销**、报告库概览）
      "source"    按 mid 查单个 UP 主（需 mid）
      "video"     按 bvid 查单条视频（需 bvid，返回历史 key_findings，避免重复分析）
      "sources"   列表（可按 domain 过滤）
      "videos"    列表（可按 topic 过滤）
      "insights"  读**经验沉淀**（跨议题的领域/信源/方法认知）。给了 topic 就只看该议题
      "topic"     读**议题档案**（需 topic 名）：核心问题/已搜方向/已覆盖/缺口/未收敛冲突/
                  用户采纳了什么/下次从哪切入——跨任务记忆与反馈闭环的落点
      "topics"    列出全部议题档案
      "protocol"  读**检索协议**（需 topic 名）：阶段 0 的产物落盘，
                  报告 §五「检索范围与偏差声明」从这里读出来，不手写
      "protocols" 列出全部已落盘的协议
      "clusters"  读某议题的**观点簇**（需 topic）：归簇结果落盘，供复核
      "staging"   看**暂存区**里有什么（写侧闸门：commit 之前正式库不会被改动）
      "cost"      调用账本汇总。`window="run"` 只看**本轮**（需先用
                  `registry_upsert(kind="run-start")` 打标记）；
                  返回里的 `md_line` 可直接贴进报告 §五「单轮开销」；`closed=true` 表示这一轮已收尾、数字不会再变

    ⚠️ `est_output_tokens` 是**输出侧粗估**（CJK×0.7 + 其它×0.25），
    用于横向比较"哪个工具最费、这一轮跑了几次"，**不是账单金额**。
    """
    try:
        k = (kind or "stats").lower()
        if k == "stats":
            payload = registry.stats()
            cs = registry.cost_summary()
            # 只放摘要，明细走 kind="cost"——stats 是"开工前一眼看现状"的入口，
            # 塞一张按工具展开的表会让它变长而失去一眼性。
            payload["spend"] = {
                "n_calls_logged": cs.get("n_calls"),
                "upstream_calls": cs.get("upstream_calls"),
                "est_output_tokens": cs.get("est_output_tokens"),
                "since": cs.get("since"), "until": cs.get("until"),
                "detail": "registry_get(kind='cost') 看按工具展开的明细",
            } if cs.get("exists") else {"exists": False}
            return _finish(payload)
        if k == "cost":
            return _finish(registry.cost_summary(window=window))
        if k == "insights":
            return _finish(registry.read_insights(topic))
        if k == "source":
            if mid is None:
                return _j({"error": "kind=source 需要 mid"})
            r = registry.get_source(mid)
            return _finish(r or {"note": f"未入库 mid={mid}（首次遇到，按 source-grading.md 正常定级即可）"})
        if k == "video":
            if not bvid:
                return _j({"error": "kind=video 需要 bvid"})
            r = registry.get_video(bvid)
            return _finish(r or {"note": f"未分析过 {bvid}（可正常走全流程）"})
        if k == "sources":
            return _finish(registry.list_sources(domain=domain))
        if k == "videos":
            return _finish(registry.list_videos(topic=topic))
        if k == "topic":
            if not topic:
                return _j({"error": "kind=topic 需要 topic（议题名）"})
            r = registry.get_topic(topic)
            return _finish(r or {"note": f"还没有 {topic} 的议题档案",
                                 "how": "registry_upsert(kind='topic') 写一条即可"})
        if k == "topics":
            return _finish(registry.list_topics())
        if k == "protocol":
            if not topic:
                return _j({"error": "kind=protocol 需要 topic（议题名）"})
            return _finish(registry.read_protocol(topic))
        if k == "protocols":
            return _finish(registry.list_protocols())
        if k == "clusters":
            if not topic:
                return _j({"error": "kind=clusters 需要 topic（议题名）"})
            return _finish(registry.read_clusters(topic))
        if k == "staging":
            return _finish(registry.list_staging())
        return _j({"error": f"未知 kind={kind}"})
    except Exception as e:                                    # noqa: BLE001
        return _err(e)


@mcp.tool()
def registry_upsert(kind: str, records: list[dict] | None = None,
                    topic: str | None = None) -> str:
    """写资产库。**每次任务结束必须调用**，否则"资产积累"是空话。

    kind="source"：{mid, name, domains[], grade(A–F), grade_basis, notes}
        grade_basis 必须写**依据**，不写就等于没评级——半年后看到"A"也想不起为什么。
        重复写入且评级变了，工具会自动追加一条 conflicts 记录。
    kind="video"：{bvid, title, mid, topics[], grade(如 A1/D4), redundancy(原创/冗余/冲突),
        key_findings[], transcript_pulled(bool), verdict}
        verdict 是一句话定性：这条在议题里是什么角色。
    kind="insight"：{section: "领域"|"信源"|"方法", text}，需要同时给 topic。
        **这是"越用越好用"的真正来源**：不是又一份报告，而是下次能直接用的判断。
        写入标准只有一个——它对下一个议题有用吗？不会帮忙的就别写。
        范例：
          领域：「B站财经区的收益预期普遍锚在 8–10%，任何'提前退休'测算先查这个锚」
          信源：「某 UP 的数据都是二手转述，引用前必须找他引的原报告」
          方法：「热点词族会带入大量番剧分集，40 条候选里 12 条无效，需在阶段 0 预判」
    kind="topic"：{topic, core_questions[], searched_axes[], covered[], gaps[],
        open_conflicts[], user_adopted[], next_entry}
        **议题档案**——跨任务记忆 + 反馈闭环。`user_adopted` 记录用户在本议题上
        采纳了哪些发现（等价于"你点了赞"），这样"下轮往哪深挖"就有外部信号。
    kind="protocol"：{topic, core_questions[], keyword_families[], include_criteria[],
        exclude_criteria[], budget{}, known_assets[], run_funnel{}}
        **阶段 0 的检索协议落盘**。写进 `.registry/topics/<议题>/protocol.md`
        （+ protocol.sha256 版本账本 + run-<日期>.json 漏斗数字）。
        报告 §五「检索范围与偏差声明」**从这里读出来**，不再手写。
        升级规矩同 DSH 插件：你没改过就自动升级；改过就保留你那份、新版另存 protocol.md.new。
    kind="cluster"：{bvid, cluster（簇名）, stance（一句话主张）, member_rpids[],
        evidence[]（"<赞数>赞/<楼层>楼｜<原文摘录>"）, axis{prevalence,agreement,controversy}（可选）}
        需要同时给 topic。**这是「去黑箱化」的落点**：
        归簇由你做判断（代码不聚类），但结果必须落盘——否则半年后
        看到报告里的「A 派占据评论区最高赞」，既不知道由哪几条评论支撑，
        也无法判断换个模型会不会归出别的簇。
        落盘位置 `.registry/topics/<议题>/clusters.json`，主键 `(bvid, cluster)`。
        读回：`registry_get(kind="clusters", topic="…")`。
    kind="commit"：**收尾时调用一次**，把暂存区合并进正式库；
        同时自动打 `run-end` 并回传 `final_cost.md_line`（最终单轮开销，此后不变）。
        ⇒ 收尾顺序固定为：commit → 把 final_cost.md_line 贴进报告 §五 → 渲染。
    kind="discard"：丢弃暂存区（这轮白跑了，不要写进资产库）。
    kind="run-start"：**阶段 0 结束时调用一次**，在调用账本里打「本轮从这里开始」标记
        （`records` 不用给）。零上游调用、零成本，但它是
        `registry_get(kind="cost", window="run")` 能把这一轮与历史分开的唯一依据——
        没有它，「这轮花了多少」就永远算不出来，报告 §五 只能写"跑了很久"。
        返回里的 `run` 是这一轮的编号。

    🔴 **写侧闸门（批次 C1）**：上面所有 kind 默认**只进暂存区**
    （`.registry/staging/`），**正式库在你调用 kind="commit" 之前不会被改动**。
    这是 DSH 插件「能力最小化」范式的等价实现：写侧始终可用（不打断"边挖边记"），
    但**碰不到正式库**。收尾时 `kind="commit"` 一次合并，可预览、可回滚。
    想看暂存区里有什么：`registry_get(kind="staging")`。

    ⚠️ 写入边界会做**入参归一化**：MCP 客户端层实测会把记录里的数组压成
    `{"item": X}`、把布尔转成字符串、把整数转成字符串。工具会在落盘前还原，
    所以 topics/domains/key_findings 一定是字符串列表、transcript_pulled 一定是布尔。
    形状还原不了的（例如 topics 传成数字）会**直接报错**而不是静默写坏数据。

    ⚠️ kind="insight" 会做**指令注入拦截**：命中注入特征的条目被**拒写**，
    并在返回的 `injection_rejected` 里说明原因。经验库是跨任务读取的，
    写进去等于把注入持久化。改写成不含祈使句的判断句即可重试。
    """
    try:
        k = (kind or "").lower()
        if k == "run-start":
            return _finish(registry.mark_run_start(topic or "",
                                                   note="阶段 0 结束标记"))
        if k == "commit":
            out = registry.commit_staging()
            # 收尾即闭合单轮窗口：此后 window="run" 的数字固定不变。
            # 之前只有 run-start，Agent 取 md_line 的时刻在 commit 之前，
            # 于是贴进报告的数字永远比账本后来的读数小（实测差 6.8%）。
            end = registry.mark_run_end("commit 时自动闭合")
            out["run_end"] = end
            if end.get("closed"):
                cs = registry.cost_summary(window="run")
                out["final_cost"] = {
                    "md_line": cs.get("md_line"),
                    "upstream_calls": cs.get("upstream_calls"),
                    "est_output_tokens": cs.get("est_output_tokens"),
                    "note": "这是最终数字（窗口已闭合）。把它贴进报告 §五，"
                            "替换掉之前暂填的那一行。",
                }
            return _finish(out)
        if k == "discard":
            return _finish(registry.discard_staging())
        if k == "insight":
            if not topic:
                return _j({"error": "kind=insight 需要 topic（议题名）"})
            return _finish(registry.stage_insights(topic, records or []))
        if k in ("source", "video", "topic", "protocol", "cluster"):
            if not records:
                return _j({"error": f"kind={k} 需要至少一条 records"})
            if k == "cluster":
                if not topic:
                    return _j({"error": "kind=cluster 需要 topic（议题名）"})
                records = [{**r, "topic": r.get("topic") or topic} for r in records]
            out = []
            for rec in records:
                try:
                    out.append(registry.stage_record(k, rec))
                except Exception as e:                        # noqa: BLE001
                    out.append({"input": rec, "error": f"{type(e).__name__}: {e}"})
            return _finish(out)
        return _j({"error": 'kind 必须是 "source" / "video" / "insight" / "topic" / '
                            '"protocol" / "cluster" / "run-start" / "commit" / "discard"'})
    except Exception as e:                                    # noqa: BLE001
        return _err(e)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
