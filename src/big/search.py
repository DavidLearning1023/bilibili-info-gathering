"""搜索与广扫。

一个改变了整个流程形态的实测事实（2026-10-19）
------------------------------------------------
`/x/web-interface/wbi/search/type` 返回的每条记录**自带完整元数据**：
aid / mid / play / video_review(弹幕数) / review(评论数) / favorites / like /
pubdate / duration / tag / description 全都在。

所以原设计里"每条候选再调一次 get_video_metadata"这一步是多余的——
它让 20 条候选花掉 20 次调用，而实际上 **1 次搜索调用就能拿到 20–50 条完整元数据**。
阶段 1 的预算因此可以全部花在评论上，而不是花在重复取元数据上。

同时实测：`page_size` 上限 50（`page_size=50` 正常返回 50 条），
`numResults` 有值但是**封顶值 1000**，不能当真实总数用——它只说明"多到无法穷尽"。
⚠️ 上一版文档里"一次最多 10 条"的说法来自某个 MCP 工具的 schema 限制，不是上游限制。
"""

from __future__ import annotations

import math
import re
import time
from concurrent.futures import ThreadPoolExecutor

from .comments import brief_comments, comment_stats, get_comments
from .http import load_cookie, wbi_fetch
from .media import OUT_ROOT, micro_sheet
from .trust import flag_injection

SEARCH_PATH = "/x/web-interface/wbi/search/type"
PAGE_SIZE_MAX = 50
_EM = re.compile(r"</?em[^>]*>")


def _clean(s: str | None) -> str:
    return _EM.sub("", s or "").strip()


def _dur_seconds(s: str | None) -> int:
    parts = (s or "").strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(parts) == 2:
        return nums[0] * 60 + nums[1]
    if len(parts) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return 0


def search_keyword(keyword: str, limit: int = 30, page: int = 1,
                   cookie: str | None = None) -> dict:
    """一次搜索，返回完整元数据列表 + 覆盖信息。

    相关性有一个很便宜的代理指标：标题/简介里被 `<em class="keyword">` 包裹的次数，
    就是命中关键词的次数。它比"搜索结果排序"更可解释——排序里混着推荐位和热度权重。
    """
    limit = max(1, min(limit, PAGE_SIZE_MAX))
    st, d, err = wbi_fetch(SEARCH_PATH, {"search_type": "video", "keyword": keyword,
                                        "page": page, "page_size": limit}, cookie)
    if st != 200 or not d or d.get("code") != 0:
        return {"keyword": keyword, "error": err or (d or {}).get("message"), "items": []}
    data = d.get("data") or {}
    rows = data.get("result") or []
    items = []
    for r in rows:
        if not isinstance(r, dict) or r.get("type") != "video":
            continue
        title = _clean(r.get("title"))
        desc = _clean(r.get("description"))
        if not title:
            continue
        bvid = r.get("bvid")
        if not bvid:
            continue
        raw_title = r.get("title") or ""
        items.append({
            "bvid": bvid,
            "aid": r.get("aid"),
            "mid": r.get("mid") or r.get("uid"),
            "title": title[:120],
            "author": r.get("author") or r.get("uname") or "",
            "duration": _dur_seconds(r.get("duration")),
            "pubdate": r.get("pubdate") or r.get("senddate"),
            "play": r.get("play") or 0,
            "danmaku": r.get("video_review") or r.get("danmaku") or 0,
            "reply": r.get("review") or 0,
            "favorites": r.get("favorites") or 0,
            "like": r.get("like") or 0,
            "desc": desc[:200],
            "tags": [t for t in (r.get("tag") or "").split(",") if t][:10],
            "tname": r.get("typename") or r.get("cate_name"),
            # 命中词数：<em> 的个数。用来判断"这条是不是真在讲这个议题"
            "kw_hits": len(re.findall(r"<em[^>]*>", raw_title)),
        })
    return {
        "keyword": keyword,
        "n": len(items),
        "items": items,
        # numResults 恒为封顶值 1000，只能说明"多到无法穷尽"，不能当总数
        "capped": (data.get("numResults") or 0) >= 1000,
        "num_pages": data.get("numPages"),
    }


def _degraded(*items) -> list[str]:
    """降级信号列表（与 mcp_server 的同名 helper 语义一致）。

    这里单独定义而不是从 mcp_server 导入：mcp_server 依赖 search，
    反向导入会成环。**降级信号必须在每个层面都齐**，所以两边各有一份。
    """
    return [i for i in items if i]


def _parse_date(s: str | None) -> int | None:
    """把 "YYYY-MM-DD" / "YYYYMMDD" 解析成 unix 时间戳；解析不了返回 None（不过滤）。"""
    if not s:
        return None
    t = str(s).strip().replace("-", "").replace("/", "")
    if len(t) != 8 or not t.isdigit():
        return None
    try:
        import datetime as _dt
        return int(_dt.datetime.strptime(t, "%Y%m%d").timestamp())
    except Exception:                                         # noqa: BLE001
        return None


def _resolve_families(keywords: list[str], families: dict | None) -> dict | None:
    """把关键词映射到语义族。返回 {关键词: 族名}；`families` 为空则返回 None。

    为什么需要它
    ------------
    `scan_topic` 原来的参数是**一个扁平列表**——代码里根本没有"词族"的概念，
    但字段名 `kw_families` 和全部文档都把它描述成"跨词族命中 = 在议题中心"。
    2026-10-21 实测：6 组词里有 3 组以同一主词 "deepseek" 为核心
    ⇒ Top 候选普遍命中 5–6 组 ⇒ **排序被冗余关键词放大**，"在议题中心"这个信号被高估。

    没有 `families` 时**不能假装知道族**：那 3 个同主词的关键词是不是一个族，
    只有 Agent 知道（Agent 知道"deepseek 刷b站"和"DSH deepseek harness"是同一个方向，
    而"原神 玩家"和"崩铁 玩家"不是）。所以这里返回 None，让调用方**如实报告算不出来**，
    而不是随便归族。
    """
    if not families:
        return None
    mapping: dict[str, str] = {}
    for fam_name, kws in families.items():
        for kw in (kws or []):
            mapping[str(kw)] = str(fam_name)
    # 没被归族的关键词：各自独立成一族，**不静默丢弃**
    for kw in keywords or []:
        mapping.setdefault(str(kw), f"未归族:{kw}")
    return mapping


def _term_variants(kw: str) -> list[str]:
    """把关键词拆成可匹配的变体：整串 + 空格/标点分隔的**有意义**片段。

    为什么要过滤两字母英文词：`AI Agent 自主学习` 会拆出 `ai`，而几乎任何
    标题里都有 "ai" 这两个字母 —— 那样 `_field_weight` 会把所有候选都判成
    "标题强命中"，等于没有权重。规则：纯 ASCII 片段要求 ≥3 字符（ai/ml 这类不单独匹配），
    中文片段 ≥2 字符。整串本身永远保留，所以长关键词仍有精确匹配的机会。
    """
    k = str(kw or "").strip().lower()
    if not k:
        return []
    parts = []
    for p in re.split(r"[\s,，/、|]+", k):
        if not p:
            continue
        if re.fullmatch(r"[a-z0-9]+", p):
            if len(p) >= 3:
                parts.append(p)
        elif len(p) >= 2:
            parts.append(p)
    return [k] + [p for p in parts if p != k]


def _field_weight(kw: str, it: dict) -> tuple[int, str]:
    """关键词在**哪个字段**字面命中 → `(权重, 字段名)`。

    为什么需要它（批次 D2）
    ----------------------
    B 站搜索对多词查询是**模糊 / OR 匹配**：一条视频只要**标签**沾边就可能命中
    很多关键词。实测 2026-10-21：一条纯 XP 赏析视频命中 6 个词族 / 16 个关键词，
    并排到 `scan_topic` 第 1 名——它和「二游社区」这个议题中心毫无关系。

    ⇒ **标签命中是弱信号，标题/简介命中才是强信号**。权重：
      title 3 / desc 2 / tag 1 / 三个字段都没有 0
    （0 = 只靠平台相关性命中，字面根本没出现，最弱的一档）

    注意这是**启发式**，不是判据：标题党也会把热词堆进标题。
    它的作用是把「明显靠标签蹭进来」的候选压下去，最终仍需人工分诊。
    """
    title = str(it.get("title") or "").lower()
    desc = str(it.get("desc") or "").lower()
    tags = " ".join(str(t) for t in (it.get("tags") or [])).lower()
    best, where = 0, "none"
    for t in _term_variants(kw):
        if t in title:
            return 3, "title"
        if t in desc and best < 2:
            best, where = 2, "desc"
        elif t in tags and best < 1:
            best, where = 1, "tag"
    return best, where


def _rank(c: dict) -> tuple:
    """阶段 1 的排序。

    为什么不直接按播放量：播放量衡量的是"这条火不火"，不是"这条在不在议题中心"。
    一条千万播放的泛财经视频可能完全没谈"普通人怎么投资"。

    排序键（批次 D2，在 B3 基础上加了字段权重）
    ------------------------------------------
    1. **强命中族数**（title/desc 里字面出现的不同语义族数）——抑制"标签沾边"型洪水。
       这是 D2 的核心修正：旧键只看「命中的不同族数」，而 tag floods 会让一条
       蹭标签的视频族数虚高（实测 6 族 / 16 词排第 1）。
    2. **命中的不同语义族数**（含仅标签命中的）——B3 的口径，保留
    3. **命中的关键词总数**——深度信号
    4. **字段权重和**（title×3 + desc×2 + tag×1）——同分时区分强弱
    5. **播放量**（log）——只做量级去噪，取 log 免得大 UP 一票通吃

    没提供 `families` 时算不出真正的族数 ⇒ 退回「关键词命中数 → 字段权重 → 播放量」，
    并在返回里**如实标注** `kw_families = null`（名不副实的字段不能继续用）。

    ⚠️ 强命中族数全为 0 时（关键词都很长、标题里不出现），第 1 键失效，
    自动退到第 2 键，行为与 B3 一致——不会因为新增键把排序搞坏。
    """
    play = -math.log10(max(c.get("play") or 1, 1))
    fams = c.get("kw_families")
    fw = c.get("field_weight") or 0
    if fams is None:
        return (-(c.get("kw_hits") or 1), -fw, play)
    strong = c.get("family_hits_strong") or 0
    return (-strong, -fams, -(c.get("kw_hits") or 1), -fw, play)


def scan_topic(keywords: list[str], per_query: int = 30, max_candidates: int = 40,
               comment_budget: int = 12, comment_n: int = 15,
               image_policy: str = "hot", image_top: int = 6,
               exclude_bvids: list[str] | None = None,
               families: dict | None = None,
               pages_per_keyword: int = 1,
               min_play: int | None = None,
               published_after: str | None = None,
               offset: int = 0,
               cookie: str | None = None) -> dict:
    """一站式广扫：多关键词搜索 → 去重 → 排序 → 对头部候选拉评论（附带低成本判图）。

    预算形态是**极端不对称**的：
      * 元数据：几乎免费（搜索结果自带），所以可以铺到 40 条以上；
      * 评论：按条计费，只给排序靠前的 `comment_budget` 条；默认读 15 条——
        8 条不足以看出"评论区有没有第二个声音"，15 条能覆盖到"高赞反方"这一层；
      * 图片：只取热评里带图的前 `image_top` 张拼 160px 微表（`image_policy="hot"`），
        没图的候选 0 请求。高赞图 = 已被大量观众看见，这个事实本身是信息；
      * 字幕：一条都不拉（这是"能广"的唯一原因）。

    image_policy：
      "hot"（默认）热评里有图就判；"off" 完全不判（最快）；"all" 不论有没有都尝试。

    families（批次 B3 新增，**强烈建议用**）：
      显式给出语义族，例如
      `{"上位词": ["AI Agent 自主学习 浏览器"], "产品名": ["DSH deepseek harness", ...]}`
      排序会改成「命中的不同族数 → 族内命中数 → 播放量」。
      **不传 `families` 时 `kw_families` 返回 null**——代码无法凭空知道哪些词属于同一族，
      与其假装知道，不如如实说算不出来。

    pages_per_keyword（批次 C4）：每个词翻几页。**长尾在第 2 页**——
      实测每个词 page_size=30 时各命中 26–30 条，恰好卡在上限，长尾根本没取到。
      调到 2 能把候选池翻一倍，代价是搜索调用数翻倍（元数据便宜，评论才是贵的）。

    min_play / published_after（批次 C4）：**收窄**候选池而不是扩大它。
      * `min_play`：排除播放量太低的（低播放的往往是刚发的、或长尾边缘）
      * `published_after`："YYYY-MM-DD" 或 "YYYYMMDD"，排除早于该日期的
        （实测 2023 年的老视频会混进来，**算法偏好新内容**，所以"老"不等于"好"）

    返回的结构就是阶段 1 的产出：一张候选表 + 关键词覆盖率 + 每条候选的评论定性摘要。
    """
    cookie = cookie or load_cookie()
    exclude = {b.upper() for b in (exclude_bvids or [])}
    per_query = max(1, min(per_query, PAGE_SIZE_MAX))
    fam_map = _resolve_families(keywords, families)
    pages_per_keyword = max(1, int(pages_per_keyword or 1))
    after_ts = _parse_date(published_after)

    per_kw = []
    merged: dict[str, dict] = {}
    for kw in keywords:
        n_hit = 0
        capped_any = False
        err = None
        for pg in range(1, pages_per_keyword + 1):
            res = search_keyword(kw, per_query, pg, cookie)
            n_hit += res.get("n", 0)
            capped_any = capped_any or bool(res.get("capped"))
            err = err or res.get("error")
            items = res.get("items") or []
            if not items:
                break                      # 这一页没结果，后面几页也不会有
            for it in items:
                bv = it["bvid"]
                if bv.upper() in exclude:
                    continue
                if min_play is not None and (it.get("play") or 0) < int(min_play):
                    continue
                if after_ts is not None and (it.get("pubdate") or 0) < after_ts:
                    continue
                rec = merged.get(bv)
                if rec is None:
                    rec = dict(it)
                    rec["kw_hits"] = 0
                    rec["hit_by"] = []
                    rec["hit_by_family"] = {}
                    rec["kw_families"] = 0 if fam_map else None
                    # 批次 D2：字段权重与"强命中族"
                    rec["field_weight"] = 0
                    rec["family_hits_strong"] = 0
                    rec["_strong_families"] = []
                    merged[bv] = rec
                if kw in rec["hit_by"]:
                    continue              # 同一页里重复出现不重复计数
                rec["kw_hits"] += 1
                rec["hit_by"].append(kw)
                w, _where = _field_weight(kw, it)
                rec["field_weight"] = (rec.get("field_weight") or 0) + w
                if fam_map:
                    fam = fam_map.get(str(kw), f"未归族:{kw}")
                    rec["hit_by_family"].setdefault(fam, []).append(kw)
                    rec["kw_families"] = len(rec["hit_by_family"])
                    # 只有 title/desc 字面命中的族才算"强命中"——
                    # 仅标签命中的族照样计入 kw_families，但不计入这里。
                    if w >= 2 and fam not in rec["_strong_families"]:
                        rec["_strong_families"].append(fam)
                        rec["family_hits_strong"] = len(rec["_strong_families"])
        per_kw.append({"keyword": kw, "n": n_hit, "pages": pages_per_keyword,
                       "error": err, "capped": capped_any})

    ranked = sorted(merged.values(), key=_rank)
    ranked_total = len(ranked)
    off = max(0, int(offset or 0))
    cands = ranked[off:off + max_candidates]
    rest = ranked[off + max_candidates:]
    for c in cands:
        # 内部字段不进返回（_strong_families 是 set 化的中间量，保留会让返回变脆）
        c.pop("_strong_families", None)
    for c in rest:
        c.pop("_strong_families", None)
    for c in cands:
        c["like_per_1k_play"] = round((c.get("like") or 0) / max(c.get("play") or 1, 1) * 1000, 1)
        c["reply_per_1k_play"] = round((c.get("reply") or 0) / max(c.get("play") or 1, 1) * 1000, 1)
        # 评论/弹幕比：只在两个统计量都存在时有意义（口径见 danmaku.py，不可单独归因）
        c["reply_over_danmaku"] = (round((c.get("reply") or 0) / c["danmaku"], 2)
                                   if c.get("danmaku") else None)

    head = cands[:comment_budget]

    def _dig(c: dict) -> dict:
        try:
            cmts, total = get_comments(int(c["aid"]), comment_n, "hot", cookie)
        except Exception as e:                                # noqa: BLE001
            return {"bvid": c["bvid"], "error": f"{type(e).__name__}: {e}"}
        st = comment_stats(cmts)
        # 判据深度不足 100 时，picture_verdict 与阈值不可比，必须置空而不是给个误导值
        verdict = st.get("picture_verdict") if st.get("judge_depth", 0) >= 100 else None
        out = {
            "bvid": c["bvid"],
            "reply_total": total,
            "stats": {k: st.get(k) for k in
                      ("n_top", "judge_depth", "picture_share", "picture_like_share",
                       "reply_intensity", "reply_to_like", "level_count", "locations")},
            "picture_verdict": verdict,
            "picture_verdict_note": None if verdict else "样本深度不足 100，暂不判读",
            "top": brief_comments(cmts, comment_n),
        }
        # 热评里可能混着针对 AI 的指令注入。标记出来（不改原文），
        # 否则广扫阶段选候选时就会把注入当成"观众的真实意见"。
        for row in out["top"]:
            risk = flag_injection(row.get("text") or "")
            if risk:
                row["injection_risk"] = risk["risk"]
                row["risk_reasons"] = risk["reasons"]
        # 低成本判图：热评里只要有图就拼一张 160px 微表。
        # 这一步不能省——纯文本采样会把带图热评压成"（纯图片）"三个字，
        # 而高赞图往往是这条视频里传播最广的一个信息单元。
        if image_policy != "off":
            n_pic = sum(1 for x in cmts if x.get("pictures") and not x.get("is_reply"))
            if n_pic:
                try:
                    out["images"] = micro_sheet(c["bvid"], cmts, OUT_ROOT / c["bvid"],
                                                top_n=image_top)
                except Exception as e:                        # noqa: BLE001
                    out["images"] = {"error": f"{type(e).__name__}: {e}"}
        return out

    digests = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        digests = list(ex.map(_dig, head))

    dmap = {d["bvid"]: d for d in digests}
    for c in cands:
        c["digest"] = dmap.get(c["bvid"])

    next_off = off + len(cands) if (off + len(cands)) < ranked_total else None
    rest_preview = [{
        "bvid": c.get("bvid"), "title": c.get("title"),
        "play": c.get("play"), "pubdate": c.get("pubdate"),
        "kw_families": c.get("kw_families"),
        "family_hits_strong": c.get("family_hits_strong"),
        "kw_hits": c.get("kw_hits"),
    } for c in rest[:30]]

    return {
        "query": {"keywords": keywords, "per_query": per_query,
                  "max_candidates": max_candidates, "comment_budget": comment_budget,
                  "comment_n": comment_n, "image_policy": image_policy,
                  "families": families or None,
                  "pages_per_keyword": pages_per_keyword,
                  "min_play": min_play, "published_after": published_after,
                  "offset": off},
        "coverage": {
            "per_keyword": per_kw,
            "unique_candidates": len(merged),
            "ranked_total": ranked_total,
            "offset": off,
            "returned": len(cands),
            "next_offset": next_off,
            "rest_preview": rest_preview,
            "rest_preview_note": (
                f"只给了未展示候选的前 {len(rest_preview)} 条摘要（不含评论/字幕，成本≈0）。"
                f"要看正文用 offset={next_off} 再调一次；"
                f"或用 exclude_bvids 把已看过的排除掉、缩小 max_candidates"
                if rest_preview else None),
            "multi_family_hits": sum(1 for c in cands if (c.get("kw_families") or 0) > 1),
            "families_declared": sorted(fam_map.keys()) if fam_map else None,
            "family_note": (
                "排序键（D2）：标题/简介**强命中**的不同族数 → 命中的不同族数 → "
                "命中关键词数 → 字段权重 → 播放量(log)。"
                "kw_families = 命中的不同族数（含仅标签命中），"
                "family_hits_strong = 其中在标题/简介里字面出现的族数。"
                if fam_map else
                "未提供 families：无法判定哪些关键词属于同一语义族，"
                "kw_families = null（不假装算得出来），排序退回「命中关键词数 → 字段权重 → 播放量」。"),
            "ranking_note": (
                "字段权重 title×3 / desc×2 / tag×1。为什么要它：B 站搜索是模糊匹配，"
                "一条视频靠**标签沾边**就能命中很多关键词（实测：纯 XP 赏析视频命中 6 族 16 词"
                "并排到第 1）。强命中族数把这类候选压下去，但它只是启发式，仍需人工分诊。"),
            "filter_note": _degraded(
                f"已按 pages_per_keyword={pages_per_keyword} 翻页"
                if pages_per_keyword > 1 else None,
                f"已按 min_play={min_play} 过滤低播放内容" if min_play is not None else None,
                f"已按 published_after={published_after} 过滤旧内容"
                if after_ts is not None else None,
                (f"候选池共 {ranked_total} 条，本页只展示第 {off + 1}–{off + len(cands)} 条，"
                 f"还剩 {len(rest)} 条未展示（rest_preview 是前 {len(rest_preview)} 条摘要；"
                 f"offset={next_off} 取下一页）") if rest else None),
            # 必须声明：搜索排序不是随机抽样，样本偏差参见 references/search-protocol.md
            "bias_note": "搜索结果按平台算法排序，不是随机抽样；跨词族命中越多，"
                         "越可能是议题中心，但也越可能都是同一批热门视频。",
        },
        "candidates": cands,
        "digested_bvids": [d["bvid"] for d in digests],
        "next": "按 references/source-grading.md 给每条打双轴评级 + 冗余度；"
                "入选者用 get_transcript / get_danmaku / get_comment_images 深挖。",
    }
