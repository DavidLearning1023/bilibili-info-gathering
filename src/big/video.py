"""视频元数据与字幕。"""

from __future__ import annotations

from .http import fetch, wbi_fetch

AI_LANGS = ["ai-zh", "ai-en", "ai-ja", "ai-ko", "ai-es", "ai-fr", "ai-de", "ai-pt", "ai-ar"]

_STAT_KEYS = ("view", "danmaku", "reply", "favorite", "coin", "share", "like")


def get_info(bvid: str, cookie: str | None = None) -> dict:
    """元数据。WBI 优先、无签名兜底，两条都失败才抛异常。"""
    st, d, err = wbi_fetch("/x/web-interface/wbi/view", {"bvid": bvid}, cookie)
    if st != 200 or not d or d.get("code") != 0:
        st2, d2, err2 = fetch("/x/web-interface/view", {"bvid": bvid}, cookie)
        if st2 != 200 or not d2 or d2.get("code") != 0:
            raise RuntimeError(f"获取视频信息失败：{err or err2 or (d or {}).get('message')}")
        d = d2
    v = d["data"]
    owner = v.get("owner") or {}
    stat = v.get("stat") or {}
    return {
        "bvid": v.get("bvid"),
        "aid": v.get("aid"),
        "title": v.get("title"),
        "author": owner.get("name"),
        # mid 是 UP 主唯一数字身份。注意它**不能**用来反查弹幕发送者——
        # 「弹幕 p[6] = crc32(mid)」已实测否证（见 danmaku.py 顶部）。
        "author_mid": owner.get("mid"),
        "duration": v.get("duration"),
        "pubdate": v.get("pubdate"),
        "desc": (v.get("desc") or "").strip(),
        "pic": v.get("pic"),
        "tname": v.get("tname") or v.get("tname_v2") or None,
        "stats": {k: stat.get(k) for k in _STAT_KEYS},
        "pages": [{"page": p.get("page"), "cid": p.get("cid"), "title": p.get("part"),
                   "duration": p.get("duration")} for p in (v.get("pages") or [])],
        "tags": get_tags(bvid, cookie),
    }


def get_tags(bvid: str, cookie: str | None = None) -> list[str]:
    """视频标签（分区之外的第二层分类）。取不到返回 []，不拖垮主流程。

    ⚠️ 顺序问题（2026-10-19 实测）：`/x/tag/archive/tags` 返回 200、data 是 **list**；
    `/x/web-interface/view/detail` 在当前环境恒 412。所以必须轻接口优先、detail 只兜底。
    反过来写每次取数都白打一次 412——既慢又平白多一次风控暴露。
    另：data 为 list 时用 .get() 会抛 AttributeError 被 except 吞掉、变成静默返回 []，
    这类"异常被吞"最难查，所以这里显式判类型。
    """
    for path in ("/x/tag/archive/tags", "/x/web-interface/view/detail"):
        try:
            st, d, _ = fetch(path, {"bvid": bvid}, cookie)
            if st != 200 or not d or d.get("code") != 0:
                continue
            data = d.get("data")
            if isinstance(data, list):
                tags = data
            elif isinstance(data, dict):
                tags = data.get("Tags") or data.get("tags") or []
            else:
                continue
            out = []
            for t in tags:
                if not isinstance(t, dict):
                    continue
                name = t.get("tag_name") or t.get("name")
                if name:
                    out.append(name)
            if out:
                return out
        except Exception:                                     # noqa: BLE001
            continue
    return []


def get_transcript(bvid: str, cid: int, cookie: str | None = None,
                   prefer: str = "zh-Hans") -> dict | None:
    """字幕。人工 CC 优先，其次 AI 字幕。

    返回 {data_source, language, lines:[{from,to,content}]} 或 None。
    `data_source` 是下游判断"能否直接引用"的唯一依据——AI 字幕的术语可能整体错一个，
    技术类视频里后面所有推论都会建立在错的前提上。
    """
    st, d, err = wbi_fetch("/x/player/wbi/v2", {"bvid": bvid, "cid": cid}, cookie)
    if st != 200 or not d or d.get("code") != 0:
        st, d, err = fetch("/x/player/v2", {"bvid": bvid, "cid": cid}, cookie)
        if st != 200 or not d or d.get("code") != 0:
            return None
    subs = ((d.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
    if not subs:
        return None
    human = [s for s in subs if not str(s.get("lan", "")).startswith("ai-")]
    ai = [s for s in subs if str(s.get("lan", "")).startswith("ai-")]
    target = None
    for s in human:
        if str(s.get("lan", "")).startswith("zh"):
            target = s
            break
    if not target and human:
        target = human[0]
    source = "subtitle"
    if not target:
        for lang in AI_LANGS:
            for s in ai:
                if s.get("lan") == lang:
                    target = s
                    break
            if target:
                break
        source = "ai_subtitle"
    if not target:
        return None
    url = target.get("subtitle_url") or ""
    if url.startswith("//"):
        url = "https:" + url
    if not url:
        return None
    import json
    import urllib.request
    from .http import UA
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            body = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:                                         # noqa: BLE001
        return None
    lines = []
    for row in body.get("body") or []:
        txt = (row.get("content") or "").strip()
        if txt:
            lines.append({"from": row.get("from"), "to": row.get("to"), "content": txt})
    if not lines:
        return None
    return {"data_source": source, "language": target.get("lan"), "lines": lines}


def fmt_ts(sec: float | None) -> str:
    if sec is None:
        return "??:??"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
