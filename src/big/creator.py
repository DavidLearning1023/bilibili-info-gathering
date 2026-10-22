"""UP 主通道：档案 / 投稿 / 合集系列 / 动态。

为什么要有这条通道
------------------
议题收敛到 2–3 个核心 UP 之后，"**这个 UP 后续又发了什么**"是自然的下一步追问。
本项目原来没有这个能力，只能靠 `search_videos` 反复搜关键词——但搜索是**按内容**召回的，
召回不到"某人后来发的某条"。

接口形态对齐上游 `bilibili-mcp` 的 `get_bilibili_creator_content`，
但**端点与本项目实测可用性**有关（见下面的实测表）。

实测（2026-10-21，本机 cookie + WBI 签名）
------------------------------------------
| 端点 | 签名 | 非签名 | 说明 |
|---|---|---|---|
| `/x/space/wbi/acc/info` | ✅ code=0 | ❌ -799 | UP 档案 |
| `/x/space/wbi/arc/search` | ✅ code=0 | ❌ -799 | 投稿列表 |
| `/x/web-interface/card` | ✅ code=0 | — | 粉丝/投稿数 |
| `/x/space/upstat` | ✅ code=0 | — | 投稿/获赞统计 |
| `/x/polymer/web-dynamic/v1/feed/space` | ✅ code=0 | ✅ | 动态流 |
| `/x/polymer/web-space/seasons_series_list` | ✅ code=0 | ✅ | 合集/系列（**非** `polymer/space/...`，那个 404） |

⇒ **space 系列端点必须带 WBI 签名**——不带就是 -799「请求过于频繁」。
这正是 http.py 里 WBI 逻辑存在的理由（红线 R1：**不要改那套签名逻辑**）。
"""

from __future__ import annotations

from .http import fetch, wbi_fetch

_SECTIONS = ("overview", "videos", "collections", "series", "dynamics")


def _overview(mid: int, cookie: str | None) -> dict:
    st, d, err = wbi_fetch("/x/space/wbi/acc/info", {"mid": mid}, cookie)
    if st != 200 or not d or d.get("code") != 0:
        # 兜底：card 接口在部分情况下还能用（它不带 WBI 也能通）
        st2, d2, err2 = fetch("/x/web-interface/card", {"mid": mid}, cookie)
        if st2 == 200 and d2 and d2.get("code") == 0:
            card = (d2.get("data") or {}).get("card") or {}
            return {"mid": mid, "name": card.get("name"), "sign": card.get("sign"),
                    "face": card.get("face"), "level": card.get("level_info"),
                    "source": "card（acc/info 不可用时的兜底）",
                    "follower": (d2.get("data") or {}).get("follower"),
                    "archive_count": (d2.get("data") or {}).get("archive_count")}
        return {"error": err or err2 or "获取 UP 档案失败"}
    info = d.get("data") or {}
    return {
        "mid": info.get("mid"), "name": info.get("name"), "sign": info.get("sign"),
        "face": info.get("face"), "level": info.get("level"),
        "coins": info.get("coins"), "official": info.get("official"),
        "profession": info.get("profession"), "school": info.get("school"),
        "vip": info.get("vip"), "fans_badge": info.get("fans_badge"),
        "top_photo": info.get("top_photo"),
        "joined_at": info.get("jointime"),
    }


def _videos(mid: int, page: int, page_size: int, order: str,
            cookie: str | None) -> dict:
    st, d, err = wbi_fetch("/x/space/wbi/arc/search",
                            {"mid": mid, "pn": page, "ps": page_size,
                             "order": order, "platform": "web"}, cookie)
    if st != 200 or not d or d.get("code") != 0:
        return {"error": err or (d or {}).get("message") or "获取投稿列表失败"}
    data = d.get("data") or {}
    lst = data.get("list") or {}
    vlist = lst.get("vlist") if isinstance(lst, dict) else (lst or [])
    items = []
    for v in (vlist or []):
        if not isinstance(v, dict):
            continue
        items.append({
            "bvid": v.get("bvid"), "aid": v.get("aid"),
            "title": v.get("title"), "created": v.get("created"),
            "length": v.get("length"), "play": v.get("play"),
            "comment": v.get("comment"), "pic": v.get("pic"),
            "typeid": v.get("typeid"), "author": (v.get("author") or {}).get("name")
            if isinstance(v.get("author"), dict) else v.get("author"),
        })
    return {"items": items, "page": data.get("page") or {},
            "count": (data.get("page") or {}).get("count")}


def _seasons_series(mid: int, page: int, page_size: int,
                     cookie: str | None) -> dict:
    st, d, err = fetch("/x/polymer/web-space/seasons_series_list",
                       {"mid": mid, "page_num": page, "page_size": page_size}, cookie)
    if st != 200 or not d or d.get("code") != 0:
        return {"error": err or (d or {}).get("message") or "获取合集/系列失败"}
    lists = (d.get("data") or {}).get("items_lists") or {}
    out = {}
    for key, val in (lists.items() if isinstance(lists, dict) else []):
        items = []
        for it in (val or []):
            if not isinstance(it, dict):
                continue
            meta = it.get("meta") or {}
            items.append({
                "id": meta.get("series_id") or meta.get("season_id"),
                "title": meta.get("name") or meta.get("title"),
                "total": meta.get("total") or meta.get("media_count"),
                "cover": meta.get("cover"),
                "updated": meta.get("update_time") or meta.get("latest_aid_time"),
            })
        out[key] = items
    return out


def _dynamics(mid: int, cookie: str | None) -> dict:
    st, d, err = fetch("/x/polymer/web-dynamic/v1/feed/space",
                       {"host_mid": mid}, cookie)
    if st != 200 or not d or d.get("code") != 0:
        return {"error": err or (d or {}).get("message") or "获取动态失败"}
    data = d.get("data") or {}
    items = []
    skipped = 0
    for it in (data.get("items") or []):
        if not isinstance(it, dict):
            continue
        mod = it.get("modules") or {}
        dyn = mod.get("module_dynamic") or {}
        major = dyn.get("major") or {}
        desc = dyn.get("desc") or {}
        # 只保留能读出"说了什么"或"发了什么视频"的动态
        text = (desc.get("text") or "").strip()
        archive = major.get("archive") or {}
        kind = "text"
        title = ""
        if not text and archive.get("bvid"):
            kind = "video"
            title = archive.get("title") or ""
        elif major.get("type") not in ("MAJOR_TYPE_NONE", None):
            kind = str(major.get("type"))
        else:
            skipped += 1
            continue
        items.append({
            "id": it.get("id_str"), "type": it.get("type"), "kind": kind,
            "text": text[:300], "title": title, "bvid": archive.get("bvid"),
            "created": (it.get("basic") or {}).get("comment_type"),
            "author": ((it.get("modules") or {}).get("module_author") or {}).get("name"),
        })
    return {"items": items, "skipped_count": skipped,
            "total": data.get("total"), "has_more": data.get("has_more")}


def creator_content(mid: int, section: str = "overview", page: int = 1,
                    page_size: int = 30, order: str = "pubdate",
                    cookie: str | None = None) -> dict:
    """读 UP 主的某一节内容。返回结构随 section 变化，但都有 `section` / `mid` / `degraded`。"""
    sec = (section or "overview").lower()
    if sec not in _SECTIONS:
        return {"error": f"未知 section={section}",
                "allowed": list(_SECTIONS)}
    if sec == "overview":
        body = _overview(mid, cookie)
    elif sec == "videos":
        body = _videos(mid, page, page_size, order, cookie)
    elif sec in ("collections", "series"):
        body = _seasons_series(mid, page, page_size, cookie)
        # 合集与系列在同一个接口里，返回时按 section 拆开
        want = "seasons_lists" if sec == "collections" else "series_lists"
        other = "series_lists" if sec == "collections" else "seasons_lists"
        body = {want: body.get(want) or []}
    else:
        body = _dynamics(mid, cookie)

    deg = []
    if isinstance(body, dict) and body.get("error"):
        deg.append(f"{sec} 读取失败：{body['error']}")
    out = {"mid": mid, "section": sec}
    if isinstance(body, dict):
        out.update(body)
    out["degraded"] = deg
    out["ok"] = not deg
    return out
