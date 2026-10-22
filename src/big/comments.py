"""评论区：游标分页取数 + 便宜指标。

为什么评论是"第二信源"而不只是"读者反馈"
------------------------------------------
B站评论区常年出现一手材料：政府统计原文截图、逐项测算、从业者反向证词、当事方澄清。
这些在视频本体里往往没有。所以评论不是"看看大家怎么说"，而是**独立的信息通道**。

两类结构性偏差，不处理就会误判：
  * **点赞 ≠ 认同**。热评排序把"高赞神评"顶上来，而它常常是**反对**视频的。
  * **条数 ≠ 权重**。用"图片评论占总赞"而不是"含图条数"，判读结果会反过来（见 stats()）。
"""

from __future__ import annotations

import re

from .http import fetch, wbi_fetch

PS_MAX = 30          # 上游单页上限（实测 30）


def get_comments(oid: int, limit: int = 100, sort: str = "hot",
                 cookie: str | None = None) -> tuple[list[dict], int | None]:
    """游标分页 + rpid 去重，返回 (评论列表, 全站评论总数)。

    ⚠️ limit 的语义是**顶层评论条数**，楼中楼是附加的、不占名额。
    旧实现按「总条数」截断，导致 `--comments 120` 实际只拿到 45 条顶层
    （75 条楼中楼吃掉 62.5% 的配额），观点分布与「图片占总赞」的口径都被稀释。
    现在返回长度可能 > limit，但顶层恒 <= limit。

    ⚠️ 必须用响应里的 cursor.next 翻页。`pn` 页码参数已被上游废弃，
    传了会被忽略，每次返回同一批数据 → 结果成倍重复。
    """
    path = "/x/v2/reply/wbi/main"
    use_wbi = True
    mode = 3 if sort == "hot" else 2
    out, seen, cursor = [], set(), None
    max_pages = (limit // 20) + 5
    top_count = 0
    d: dict | None = None
    for _ in range(max_pages):
        if top_count >= limit:
            break
        params = {"oid": oid, "type": 1, "mode": mode, "ps": PS_MAX}
        if cursor:
            params["next"] = cursor
        if use_wbi:
            st, d, err = wbi_fetch(path, params, cookie)
            if st != 200 or not d or d.get("code") != 0:
                use_wbi = False
                st, d, err = fetch("/x/v2/reply/main", params, cookie)
        else:
            st, d, err = fetch("/x/v2/reply/main", params, cookie)
        if st != 200 or not d or d.get("code") != 0:
            break

        def walk(node, is_reply=False):
            """展平主评论与楼中楼。

            2026-10-19 修：原实现只取 content.message，会把「纯图片评论」整条丢掉
            （message 为空 ⇒ `if msg` 为假），并把 [doge] 这类表情占位符清洗成空串。
            实测某期 178 条里 87 条带图、占样本赞量 96.8%，最高赞 21,055 就是一张图，
            全部被降级成空字符串。现在保留 pictures / emotes / level / location / ctime，
            并允许「有图无文」的评论进入样本。
            """
            if not isinstance(node, dict):
                return False
            member = node.get("member") or {}
            content = node.get("content") or {}
            msg = (content.get("message") or "").strip()
            rpid = node.get("rpid")

            pics = []
            for p in (content.get("pictures") or []):
                if not isinstance(p, dict):
                    continue
                src = p.get("img_src") or p.get("img_url")
                if not src:
                    continue
                if src.startswith("//"):
                    src = "https:" + src
                pics.append({"url": src, "w": p.get("img_width"),
                             "h": p.get("img_height"), "size": p.get("img_size")})
            emotes = [k for k in (content.get("emote") or {}) if isinstance(k, str)]

            added = False
            if rpid is not None and rpid not in seen and (msg or pics or emotes):
                seen.add(rpid)
                added = True
                rc = node.get("reply_control") or {}
                loc = (rc.get("location") or "").replace("IP属地：", "").replace("IP属地:", "").strip()
                li = member.get("level_info") or {}
                # 表情占位符一律从正文里去掉，表情名单独存进 emotes。
                # 旧正则只认英文，`[抓狂]` 这类中文表情会留在正文里，与 emotes 重复，
                # 也让「清洗后为空 = 纯表情」的判断失效。B站表情固定 `[名称]`（≤16 字无空格）。
                clean_text = re.sub(r"\[[^\[\]\n]{1,16}\]", "", msg).strip()
                kind = "picture" if pics else ("emote" if emotes else "text")
                out.append({
                    "rpid": rpid,
                    "author": member.get("uname") or "匿名用户",
                    "likes": node.get("like") or 0,
                    # rcount = 该楼的楼中楼总数（仅顶层有）。点赞是"认同"，回复是"争议"，
                    # 两种不同信号，不能互相替代——观点分布的争议度轴靠它。
                    "rcount": node.get("rcount") or 0,
                    "is_reply": is_reply,
                    "content": clean_text,
                    "kind": kind,
                    "pictures": pics,
                    "emotes": emotes,
                    "level": li.get("current_level"),
                    "location": loc or None,
                    "ctime": node.get("ctime"),
                    "is_senior": member.get("is_senior_member") or 0,
                })
            if not is_reply:
                replies = node.get("replies") or []
                if isinstance(replies, list):
                    for rp in replies[:3]:
                        walk(rp, True)
            # 必须返回"这一次是否真的新增了顶层"，不能只看 is_reply：
            # 游标翻页偶有重叠，去重跳过的条目若仍计数，top_count 会虚增、提前 break 少拿。
            return added and (not is_reply)

        data = d.get("data") or {}
        for rep in (data.get("replies") or []):
            if top_count >= limit:
                break
            if walk(rep):
                top_count += 1
        cur = data.get("cursor") or {}
        cursor = cur.get("next")
        if not cursor or cur.get("is_end"):
            break
    total = ((d.get("data") or {}).get("cursor") or {}).get("all_count") if d else None
    return out, total


def _top_locations(cmts: list[dict], n: int = 8) -> dict:
    from collections import Counter
    c = Counter(x["location"] for x in cmts if x.get("location"))
    return dict(c.most_common(n))


def comment_stats(cmts: list[dict]) -> dict:
    """评论区的便宜指标——不读图就能算，用来决定「要不要读图」。

    picture_like_share 是读图判据：实测某期 57.0% 的条数占 96.8% 的赞，
    另一期只有 5.0% 的条数却占 28.3% 的赞（必须看），还有一期 7.0% 只占 0.8%（可跳过）。
    所以判据用「图片评论占总赞」，**不用**「含图条数占比」——条数口径会漏掉
    「少数几张图拿走大部分注意力」这种最典型的情况。

    ⚠️ 两条口径纪律：
    1. **一律按顶层评论算**。楼中楼每楼只取 3 条（截断），样本有偏，
       混进来会稀释图片占比、压平等级分布。
    2. **判据固定按「前 100 条顶层」计算**，与实际采样量解耦。同一条视频实测：
       取 50/100/200/300 条时含图条数恒为 7，但「图片占赞」是 1.4%/0.8%/0.7%/0.6%——
       **随样本深度单调下降**，因为分子不变、分母随长尾膨胀。不固定深度，
       同一个视频会因采样量不同判出 must / skip 两种结果。阈值 15% 是在 100 条深度下标定的。
    """
    if not cmts:
        return {}
    top = [c for c in cmts if not c.get("is_reply")]
    alltop = top or cmts

    base = alltop[:100]
    total_like = sum(c["likes"] for c in base) or 1
    with_pic = [c for c in base if c.get("pictures")]
    pic_like = sum(c["likes"] for c in with_pic)
    pure_pic = [c for c in with_pic if not c["content"]]
    pure_emo = [c for c in base if c.get("kind") == "emote"]

    lv_count, lv_like = {}, {}
    body_like = sum(c["likes"] for c in alltop) or 1
    for c in alltop:
        lv = c.get("level") or 0
        lv_count[lv] = lv_count.get(lv, 0) + 1
        lv_like[lv] = lv_like.get(lv, 0) + c["likes"]
    return {
        "n_top": len(alltop),
        "n_replies": len(cmts) - len(top),
        "judge_depth": len(base),
        "total_likes": total_like,
        "with_picture": len(with_pic),
        "picture_share": round(len(with_pic) / len(base) * 100, 1),
        "picture_like_share": round(pic_like / total_like * 100, 1),
        "pure_picture": len(pure_pic),
        "pure_picture_like_share": round(sum(c["likes"] for c in pure_pic) / total_like * 100, 1),
        # 纯表情（无图无文）对正文分析零贡献，但不该被静默丢掉——
        # 它的条数与赞量是「表演场」形态的直接证据。
        "pure_emote": len(pure_emo),
        "pure_emote_like_share": round(sum(c["likes"] for c in pure_emo) / total_like * 100, 1),
        # reply_intensity = 平均每层楼中楼数 → 争论强度（用 rcount 真值，不用抓到的截断条数）
        # reply_to_like   = 楼中楼总数 ÷ 赞总数（%）→ 评论活跃度
        "reply_intensity": round(sum(c.get("rcount") or 0 for c in alltop) / max(len(alltop), 1), 2),
        "reply_to_like": round(sum(c.get("rcount") or 0 for c in alltop)
                               / max(sum(c["likes"] for c in alltop), 1) * 100, 1),
        "picture_verdict": ("must" if pic_like / total_like >= 0.15
                            else "sample" if pic_like / total_like >= 0.05
                            else "skip"),
        "level_count": dict(sorted(lv_count.items(), reverse=True)),
        "level_like_share": {k: round(v / body_like * 100, 1)
                             for k, v in sorted(lv_like.items(), reverse=True)},
        "locations": _top_locations(alltop),
        "senior_members": sum(1 for c in alltop if c.get("is_senior")),
    }


def opinion_comments(cmts: list[dict], n: int = 100) -> list[dict]:
    """观点分布模式：**只取顶层评论** + 最小字段集。

    为什么只取顶层
    --------------
    三轴全部只依赖顶层：

    * 普遍度 = 该簇条数 ÷ 有效样本条数
    * 认同度 = 该簇点赞合计 ÷ 全部点赞合计
    * 争议度 = 该簇 ``rcount`` 合计 ÷ 全部 ``rcount`` 合计

    而 ``rcount``（该楼的楼中楼总数）**只存在于顶层**——楼中楼自身恒为 0。
    所以算三轴根本不需要楼中楼正文。楼中楼只影响"读争论内容"，
    不影响"分布"。

    体积（实测：一条 2014 评论的视频，取前 100 顶层 + 150 楼中楼）
    ------------------------------------------------------------------
    ============================  ==========  ==========
    口径                          字符         token(估)
    ============================  ==========  ==========
    full（全字段 250 条）          71,922      ~25,300
    brief（正文截断 90 字）         7,374      ~4,280
    **本模式（仅顶层 100 条）**     ~12,000     **~7,200**
    ============================  ==========  ==========

    省下的 71% **不是内容，是 JSON 结构开销**：full 里正文只占 16%，
    其余是字段名、pictures 的 URL 明细、rpid 等。

    为什么不直接用 brief 省更多
    --------------------------
    实测顶层 100 条里 24% 的正文超过 90 字，截断会丢掉 **35% 的正文量**。
    而观点簇的判断依赖完整语义——"反对并给出理由"被截成"反对"，
    正好毁掉认同度与争议度的区分。**省 token 不能省在语义上。**

    不带的字段
    ----------
    ``loc``（IP 属地）：观点分布不按属地分层；且它属个人信息，
    产物侧本就不该带（见 README 隐私节）。
    """
    tops = [c for c in cmts if not c.get("is_reply")][:n]
    out = []
    for c in tops:
        d = {
            "text": c["content"],
            "likes": c["likes"],
            "rcount": c.get("rcount") or 0,
            "author": c.get("author") or "匿名用户",
            "lv": c.get("level"),
            "pics": len(c.get("pictures") or []),
        }
        if not d["text"]:
            d["text"] = ("（纯图片）" if c.get("kind") == "picture"
                         else f"（仅表情 {' '.join(c.get('emotes') or [])})"
                         if c.get("kind") == "emote" else "（空）")
        out.append(d)
    return out


def brief_comments(cmts: list[dict], n: int = 10, width: int = 90) -> list[dict]:
    """广扫阶段的评论摘要：只要前 n 条顶层，正文截断，去掉图片 URL 明细。

    阶段 1 的目的是「给每条候选定性」，不是读懂评论。把 20 条候选 × 完整评论
    塞进上下文，等于把"广度优先"变成"每条都读一遍"。
    """
    tops = [c for c in cmts if not c.get("is_reply")][:n]
    out = []
    for c in tops:
        txt = c["content"]
        if len(txt) > width:
            txt = txt[:width] + "…"
        if not txt:
            txt = "（纯图片）" if c.get("kind") == "picture" else \
                  (f"（仅表情 {' '.join(c.get('emotes') or [])}）" if c.get("kind") == "emote" else "（空）")
        out.append({
            "likes": c["likes"],
            "rcount": c.get("rcount") or 0,
            "lv": c.get("level"),
            "loc": c.get("location"),
            "pics": len(c.get("pictures") or []),
            "text": txt,
        })
    return out
