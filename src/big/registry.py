"""信源库与视频索引。

为什么要有它
------------
`sources.json` / `videos.json` 是长期资产，手写 JSON 容易漏逗号、并发覆盖、
字段名写错（写进去就一直在）。所以一律通过本模块读写：原子写 + 字段校验 + 评级变动检测。

最值钱的一点是**评级变动检测**：一个一直靠谱的 UP 突然发了一条不靠谱的，
这个"不一致"本身比任何单条评级都更有信息量，所以它被单独记进 `conflicts`。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import tempfile

from .trust import flag_injection

# 默认放在项目内的 .registry（已 gitignore）；换目录用环境变量
_HERE = pathlib.Path(__file__).resolve().parent.parent.parent
DEFAULT_DIR = str(_HERE / ".registry")
ROOT = pathlib.Path(os.environ.get("BIG_REGISTRY_DIR", DEFAULT_DIR))
SOURCES = ROOT / "sources.json"
VIDEOS = ROOT / "videos.json"
TOPICS = ROOT / "topics"
TOPICS_JSON = ROOT / "topics.json"
INSIGHTS = ROOT / "insights.md"
STAGING = ROOT / "staging"
# 调用账本（批次 D5）：一行一次工具调用，追加写。
# 回答的是「一轮议题到底花了多少上游调用 / 多少输出 token」——`cost` 字段只活在
# 单次返回里，任务一结束账就没了，P2 的效率优化因此没有依据。
COST_LOG = ROOT / "cost-log.jsonl"

SOURCE_FIELDS = {"mid", "name", "domains", "grade", "grade_basis",
                 "first_seen", "last_updated", "observations", "conflicts", "notes"}
VIDEO_FIELDS = {"bvid", "title", "mid", "analyzed_at", "topics", "grade", "redundancy",
                "key_findings", "transcript_pulled", "verdict",
                # 批次 B4 新增：让"为什么这条被选进来"在半年后仍可查
                "selected_because", "cross_checked_with", "degraded",
                # 批次 G 新增：同一 BV 被第二次评级时留痕（见 upsert_video 注释）
                "grade_history", "grade_basis"}
GRADE_SRC = set("ABCDEF")

# ---------------- 议题档案（kind="topic"，批次 B4） ----------------
#
# 为什么要有它：`videos.json` 的 `topics` 只是标签数组，**不保存"为什么这条被选进来"**；
# `insights.md` 是追加式 markdown、不可结构化查询。
# 议题档案是**可查询的**跨任务记忆层：core_questions 是这次要回答什么，
# covered/gaps 是覆盖到哪、open_conflicts 是还没收敛的冲突，
# `user_adopted` 记录**用户采纳了哪些发现**——这是反馈闭环的唯一落点
# （等价于 DSH 插件的"你点了赞"），让"下轮该往哪深挖"有外部信号而不是 Agent 的猜测。
TOPIC_FIELDS = {"topic", "core_questions", "searched_axes", "covered", "gaps",
                "open_conflicts", "user_adopted", "next_entry", "updated"}
TOPIC_SHAPES = {"topic": str, "core_questions": list, "searched_axes": list,
                "covered": list, "gaps": list, "open_conflicts": list,
                "user_adopted": list, "next_entry": str, "updated": str}

# ---------------- 检索协议（kind="protocol"，批次 B2） ----------------
#
# 仿 DSH 插件的 `duty.md` + `charter.sha256` 范式：
# 阶段 0 产出的五样东西（核心问题 / 关键词族 / 纳入排除 / 预算 / 已有资产）
# **只存在于 Agent 当轮的上下文里** ⇒ 报告 §五 是手写的、历史不可复核、同议题重复劳动。
# 落盘之后，"这次搜了什么"永远可回放。
PROTOCOL_FIELDS = {"topic", "core_questions", "keyword_families", "include_criteria",
                   "exclude_criteria", "budget", "known_assets", "run_funnel", "notes"}
PROTOCOL_SHAPES = {"topic": str, "core_questions": list, "keyword_families": list,
                   "include_criteria": list, "exclude_criteria": list,
                   "budget": dict, "known_assets": list, "run_funnel": dict, "notes": str}

# ---------------- 观点簇（kind="cluster"，批次 D4 · 去黑箱化） ----------------
#
# 为什么要有它
# ------------
# 项目一直坚持「判断由 LLM 做，不写进代码」——这是对的（判据取决于议题）。
# 但代价是：归簇的结果**只存在于当轮上下文**，报告里的阵营表因此无法复核：
# 半年后看到「A 派占据评论区最高赞」，既不知道它由哪几条评论支撑，
# 也无法判断换个模型会不会归出别的簇。
#
# 落盘不改变「谁做判断」，只改变「判断有没有留下痕迹」：
#   * `cluster`  簇名（Agent 自己起，同一议题内要能稳定复用）
#   * `stance`   一句话核心主张（**用簇自己的逻辑，不美化**）
#   * `member_rpids` 支撑评论的 rpid 列表（可回溯到原评论）
#   * `evidence` 证据行，格式「<赞数>赞/<楼层>楼｜<原文摘录>」（字符串列表，
#                不用 dict 列表：形状契约会把嵌套 list 摊平，见 _LIST_FIELDS）
#   * `axis`     三轴（prevalence / agreement / controversy），可选——
#                没有就是没人算，比填个假数字好
#
# 主键是 (topic, bvid, cluster) 三元组：同一视频下同名簇覆盖，不同视频分开记。
CLUSTER_FIELDS = {"topic", "bvid", "cluster", "stance", "member_rpids",
                  "evidence", "axis", "note", "created"}
CLUSTER_SHAPES = {"topic": str, "bvid": str, "cluster": str, "stance": str,
                  "member_rpids": list, "evidence": list, "axis": dict,
                  "note": str, "created": str}

# ---------------------------------------------------------------- 值的形状契约
#
# 为什么要这一层（2026-10-21 实测，不是推演）
# --------------------------------------------
# MCP 客户端层会把**记录内的值**改形：
#
#   ["A","B"]            -> {"item": ["A","B"]}
#   [["f1","f2"],"f3"]  -> {"item": [{"item": ["f1","f2"]}, "f3"]}
#   true                -> "true"
#   1                   -> "1"
#
# 而 `_check_fields` **只校验字段名、不校验值形状** ⇒ 脏值直入库。
# 后果是可查询性损坏：`list_videos(topic=...)` 用 `topic in (v.get("topics") or [])`，
# `topics` 变成 dict 后 `in` 变成查 dict 的 key ⇒ **按议题筛选全部失效**；
# `stats()` 的 `videos_by_topic` 还会冒出叫 `"item"` 的假议题。
# 而调用返回 {"action":"新增"} —— **看起来完全成功**。
#
# 所以在**写入边界**做两件事：
#   ① `_normalize_record` 把客户端的改形还原成规范形状（可逆的机械改形）；
#   ② `_check_shapes` 给剩下的形状问题直接报错，而不是静默修正。
VIDEO_SHAPES = {"topics": list, "key_findings": list, "transcript_pulled": bool,
                "grade": str, "redundancy": str, "verdict": str, "bvid": str,
                "selected_because": str, "cross_checked_with": list, "degraded": list,
                "grade_history": list, "grade_basis": str}
SOURCE_SHAPES = {"domains": list, "grade": str, "grade_basis": str,
                 "name": str, "notes": str}

# 形状是「字符串列表」的字段：值里再套一层 list 也要摊平
_LIST_FIELDS = ("topics", "domains", "key_findings", "cross_checked_with", "degraded",
                "core_questions", "searched_axes", "covered", "gaps", "open_conflicts",
                "user_adopted", "include_criteria", "exclude_criteria", "known_assets",
                "keyword_families", "member_rpids", "evidence", "grade_history")
# 形状是布尔的字段
_BOOL_FIELDS = ("transcript_pulled",)
# 形状是整数的字段（客户端会把 int 变成 str）
_INT_FIELDS = ("mid",)
# 形状是字典的字段
_DICT_FIELDS = ("budget", "run_funnel", "axis")


def today() -> str:
    return dt.date.today().isoformat()


def _load(path: pathlib.Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"{path} 不是合法 JSON：{e}\n手工修一下或从备份恢复。不要直接删——那是累积的资产。"
        ) from e


def _atomic_write(path: pathlib.Path, obj: dict) -> None:
    """先写临时文件再替换，避免写到一半崩溃毁掉整个库。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def init() -> dict:
    TOPICS.mkdir(parents=True, exist_ok=True)
    out = {}
    for path, key in ((SOURCES, "sources"), (VIDEOS, "videos"), (TOPICS_JSON, "topics")):
        if path.exists():
            out[str(path)] = "已存在"
            continue
        default = {"sources": [], "videos": {}, "topics": {}}
        _atomic_write(path, {"_meta": {"version": 1, "updated": today()},
                             key: default[key]})
        out[str(path)] = "新建"
    return {"root": str(ROOT), "files": out}


def _check_fields(rec: dict, allowed: set, kind: str) -> None:
    unknown = set(rec) - allowed
    if unknown:
        raise ValueError(f"{kind} 里有未知字段：{sorted(unknown)}；允许的字段：{sorted(allowed)}。"
                         f"字段名写错会被永久存进去，所以这里直接拦下。")


def _unwrap_item(v):
    """把客户端的 `{"item": X}` 包装还原成 X，可递归。

    客户端会把**记录里的任何数组**压成 `{"item": <原值>}`，而且会嵌套：
    `[["f1","f2"],"f3"]` 变成 `{"item": [{"item": ["f1","f2"]}, "f3"]}`。
    所以这里要一路递归到叶子，不能只剥一层。
    """
    if isinstance(v, dict):
        if len(v) == 1 and "item" in v:
            return _unwrap_item(v["item"])
        return {k: _unwrap_item(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_unwrap_item(x) for x in v]
    return v


def _normalize_record(rec: dict) -> dict:
    """把客户端可能施加的改形还原成规范形状。

    实测（2026-10-21）：MCP 入参层会把记录里的数组压成 `{"item": X}`、
    把布尔转成字符串、把整数转成字符串。这里做防御性归一化——
    宁可多写一行，也不能让脏形状进库。

    与任务书骨架的差异（有意为之，已在交付文档说明）
    ---------------------------------------------------
    骨架里对**任何**字符串做 `"true"/"false"` → bool 的转换；这里改成**按字段类型**转：
    只有 `transcript_pulled` 这种**声明为 bool** 的字段才转。
    理由：字段的合法取值是 `"true"` 的字符串时（例如某条结论原话就是 "true"），
    无差别转换会把内容改掉——**归一化只该还原机械改形，不该改写内容**。
    同理 `mid` 声明为 int，所以字符串数字会还原成 int。
    """
    out: dict = {}
    for k, v in rec.items():
        nv = _unwrap_item(v)
        if k in _LIST_FIELDS:
            if isinstance(nv, str):
                nv = [nv]                       # 曾被压成裸字符串的，还原成 list
            elif isinstance(nv, list):
                nv = [x for sub in nv for x in (sub if isinstance(sub, list) else [sub])]
        elif k in _BOOL_FIELDS and isinstance(nv, str):
            s = nv.strip().lower()
            if s in ("true", "false"):
                nv = (s == "true")
        elif k in _INT_FIELDS and isinstance(nv, str):
            s = nv.strip()
            if s.lstrip("-").isdigit():
                nv = int(s)
        elif k in _DICT_FIELDS and not isinstance(nv, dict):
            raise ValueError(f"{k} 期望 dict，收到 {type(nv).__name__}：{nv!r}")
        out[k] = nv
    return out


def _check_shapes(rec: dict, shapes: dict, kind: str) -> None:
    """值的类型契约：形状不对**直接报错**，而不是被静默修正。

    报错比"修一修"好，因为修过的值可能已经不是调用方想写的东西了；
    而"写进去了但形状不对"是本项目历史上一类静默失效（见文件头注释）。
    """
    for k, t in shapes.items():
        if k in rec and rec[k] is not None and not isinstance(rec[k], t):
            raise ValueError(
                f"{kind}.{k} 期望 {t.__name__}，收到 {type(rec[k]).__name__}：{rec[k]!r}")


def upsert_source(rec: dict) -> dict:
    # 归一化必须在 _check_fields **之前**：客户端连字段名一起改形时，
    # 归一化后字段名才是对的，否则会被当成"未知字段"报出来。
    rec = _normalize_record(rec)
    _check_fields(rec, SOURCE_FIELDS, "source record")
    _check_shapes(rec, SOURCE_SHAPES, "source record")
    if "mid" not in rec or "name" not in rec:
        raise ValueError("source record 至少要有 mid 与 name")
    grade = (rec.get("grade") or "").upper()[:1]
    if grade and grade not in GRADE_SRC:
        raise ValueError(f"grade 必须是 A–F 之一，收到 {rec.get('grade')!r}")

    db = _load(SOURCES, {"sources": []})
    arr = db.setdefault("sources", [])
    old = next((s for s in arr if str(s.get("mid")) == str(rec["mid"])), None)
    if old is None:
        rec.setdefault("first_seen", today())
        rec.setdefault("observations", 1)
        rec.setdefault("conflicts", [])
        arr.append(rec)
        action = "新增"
    else:
        rec["observations"] = old.get("observations", 0) + 1
        rec["first_seen"] = old.get("first_seen", today())
        rec["conflicts"] = old.get("conflicts", [])
        if old.get("grade") and grade and old["grade"] != grade:
            rec["conflicts"].append({
                "date": today(),
                "note": f"评级由 {old['grade']} 变为 {grade}；本次依据：{rec.get('grade_basis', '（未填）')}",
            })
        arr[arr.index(old)] = rec
        action = "更新"
    rec["last_updated"] = today()
    db.setdefault("_meta", {})["updated"] = today()
    _atomic_write(SOURCES, db)
    return {"action": action, "mid": rec["mid"], "name": rec.get("name"),
            "grade": rec.get("grade"), "conflicts": rec.get("conflicts")}


# ---------------- 议题档案（kind="topic"，批次 B4） ----------------

def upsert_topic(rec: dict) -> dict:
    """写/更新一个议题档案。

    与 `insights` 的分工：`insights.md` 是**跨议题**的追加式 markdown（给人读），
    议题档案是**单个议题**的结构化记录（给机器查）。
    两者互补，不要用其中一个顶替另一个。
    """
    rec = _normalize_record(rec)
    _check_fields(rec, TOPIC_FIELDS, "topic record")
    _check_shapes(rec, TOPIC_SHAPES, "topic record")
    name = str(rec.get("topic") or "").strip()
    if not name:
        raise ValueError("topic record 必须含 topic（议题名）")
    rec["topic"] = name
    rec["updated"] = today()
    db = _load(TOPICS_JSON, {"topics": {}})
    topics = db.setdefault("topics", {})
    action = "更新" if name in topics else "新增"
    topics[name] = rec
    db.setdefault("_meta", {})["updated"] = today()
    _atomic_write(TOPICS_JSON, db)
    return {"action": action, "topic": name, "updated": rec["updated"]}


def get_topic(topic: str) -> dict | None:
    return (_load(TOPICS_JSON, {"topics": {}}).get("topics") or {}).get(topic)


def list_topics() -> list[dict]:
    return list((_load(TOPICS_JSON, {"topics": {}}).get("topics") or {}).values())


# ---------------- 检索协议（kind="protocol"，批次 B2） ----------------

_BAD_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_dirname(topic: str) -> str:
    """议题名 → 合法目录名。Windows 不允许 <>:"/\\|?* 与控制字符。"""
    name = _BAD_FS.sub("-", str(topic or "").strip()).strip(". ")
    return name[:80] or "未命名议题"


def _cluster_path(topic: str) -> pathlib.Path:
    return TOPICS / _safe_dirname(topic) / "clusters.json"


def upsert_clusters(topic: str, records: list[dict]) -> dict:
    """写入某议题的观点簇（按 `(bvid, cluster)` 覆盖式合并）。

    这是**判断的落盘**，不是判断本身——谁归的簇、归成什么，由 Agent 决定；
    这里只保证「决定了的事有记录」，让下一个人（或下一次运行）能复核。
    """
    if not topic:
        raise ValueError("写簇需要 topic（议题名）")
    p = _cluster_path(topic)
    cur = _load(p, {"topic": topic, "clusters": []})
    rows = list(cur.get("clusters") or [])
    idx = {(str(r.get("bvid")), str(r.get("cluster"))): i for i, r in enumerate(rows)}
    added = updated = 0
    for rec in records:
        rec = _normalize_record(rec)
        rec["topic"] = topic
        _check_fields(rec, CLUSTER_FIELDS, "cluster record")
        _check_shapes(rec, CLUSTER_SHAPES, "cluster record")
        if not rec.get("bvid") or not rec.get("cluster"):
            raise ValueError("cluster record 必须含 bvid 与 cluster（簇名）")
        rec.setdefault("created", today())
        k = (str(rec["bvid"]), str(rec["cluster"]))
        if k in idx:
            rows[idx[k]] = {**rows[idx[k]], **rec}
            updated += 1
        else:
            idx[k] = len(rows)
            rows.append(rec)
            added += 1
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, {"topic": topic, "updated": today(), "clusters": rows})
    return {"action": "写入", "topic": topic, "added": added, "updated": updated,
            "n": len(rows), "path": str(p)}


def read_clusters(topic: str) -> dict:
    """读回某议题的观点簇，并附一份 `by_bvid` 索引（看某条视频被归成了几个簇）。"""
    p = _cluster_path(topic)
    if not p.exists():
        return {"exists": False, "topic": topic,
                "note": "还没有这个议题的簇记录。阶段 2 归簇后应写入"
                        "（registry_upsert(kind='cluster', topic=...)）。"}
    d = _load(p, {})
    rows = d.get("clusters") or []
    by_bvid: dict[str, list] = {}
    for r in rows:
        by_bvid.setdefault(str(r.get("bvid")), []).append(r.get("cluster"))
    return {"exists": True, "topic": topic, "n": len(rows),
            "n_bvid": len(by_bvid), "by_bvid": by_bvid,
            "clusters": rows, "updated": d.get("updated")}


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _render_protocol(topic: str, rec: dict) -> str:
    """把协议记录渲染成固定模板的 markdown。

    固定模板 = 阶段 0 的五样东西（核心问题 / 关键词族及各自命中 / 纳入排除 / 预算 / 已有资产）。
    之所以要**固定**：模板一变，历史协议的可比性就没了；而且报告 §五 要从这里读出来。
    """
    L: list[str] = []
    L.append(f"# 检索协议 · {topic}")
    L.append("")
    L.append("> 本文件是**阶段 0 的产物落盘**——「这次搜了什么、为什么这么搜」永远可回放。")
    L.append("> 报告的「§五 检索范围与偏差声明」**从这里读出来**，不是手写。")
    L.append("> 版本账本：同目录 `protocol.sha256`；每次运行的漏斗数字：`run-<YYYYMMDD>.json`。")
    L.append("")
    L.append(f"- 议题：{topic}")
    L.append(f"- 更新：{rec.get('updated') or today()}")
    L.append("")

    L.append("## 核心问题")
    L.append("")
    cq = rec.get("core_questions") or []
    L.extend([f"{i}. {q}" for i, q in enumerate(cq, 1)] or ["（未填）"])
    L.append("")

    L.append("## 关键词族")
    L.append("")
    L.append("| 族 | 关键词 | 各自命中 | 备注 |")
    L.append("|---|---|---|---|")
    for fam in (rec.get("keyword_families") or []):
        if not isinstance(fam, dict):
            L.append(f"| （格式不对） | {fam} |  |  |")
            continue
        fname = fam.get("family") or fam.get("name") or "（未命名族）"
        kws = fam.get("keywords") or []
        hits = fam.get("hits")
        if hits is None:
            hits = fam.get("hit_counts")
        if isinstance(hits, dict):
            hits = [f"{k}={v}" for k, v in hits.items()]
        kwstr = " / ".join(str(x) for x in (kws if isinstance(kws, list) else [kws]))
        hitstr = " / ".join(str(x) for x in (hits if isinstance(hits, list) else [hits]))
        L.append(f"| {fname} | {kwstr} | {hitstr} | {fam.get('note') or ''} |")
    L.append("")

    L.append("## 纳入标准")
    L.append("")
    L.extend([f"- {x}" for x in (rec.get("include_criteria") or [])] or ["（未填）"])
    L.append("")
    L.append("## 排除标准")
    L.append("")
    L.extend([f"- {x}" for x in (rec.get("exclude_criteria") or [])] or ["（未填）"])
    L.append("")

    L.append("## 预算")
    L.append("")
    budget = rec.get("budget") or {}
    if isinstance(budget, dict) and budget:
        for k, v in budget.items():
            L.append(f"- {k}：{v}")
        # 废弃口径当场指出来。实测代价：协议写「调用上限 30」，一开局就注定超支
        # （一次 scan_topic 带 12 个关键词就打 24 次上游），报告里只好写「预算超支」。
        bad = [k for k in budget if ("上限" in str(k) and "调用" in str(k))
               or "upstream" in str(k).lower()]
        if bad:
            L.append("")
            L.append("> ⚠️ **预算口径已废弃**："
                     + "、".join(f"`{k}`" for k in bad)
                     + " 不可用。一次 `scan_topic` 会按关键词数打上游"
                     "（12 个词 = 24 次），按「上游调用次数」定上限必然超支。"
                     "改用三件事计量：`scan_topic 次数` / `深挖条数` / `探针次数`。")
    else:
        L.append("（未填）")
    L.append("")

    L.append("## 已知资产（开工前查到的）")
    L.append("")
    L.extend([f"- {x}" for x in (rec.get("known_assets") or [])] or ["（未填）"])
    L.append("")

    if rec.get("notes"):
        L.append("## 备注")
        L.append("")
        L.append(str(rec["notes"]))
        L.append("")

    L.append("---")
    L.append("")
    L.append(f"（本文件由 `registry_upsert(kind=\"protocol\")` 写入；"
             f"改过之后哈希与账本不符，下次写入会把新版另存为 `protocol.md.new`，"
             f"保留你手工改的这份。）")
    return "\n".join(L)


def write_protocol(rec: dict) -> dict:
    """落盘检索协议（批次 B2）。

    升级规矩（对齐 DSH 插件的 `duty.md` + `charter.sha256` 范式）：
      * **没改过**（哈希与账本一致）→ 自动升级，直接覆盖
      * **改过**（哈希与账本不符）→ 保留用户那份，新版另存 `protocol.md.new`

    为什么要有这个分支：协议是「用户可以手工调」的判据载体。
    如果每次写入都直接覆盖，用户在 protocol.md 里手写的排除条件会被悄悄冲掉——
    而那正是"我上次调过，这次别再搜那些垃圾"的信息。
    """
    rec = _normalize_record(rec)
    _check_fields(rec, PROTOCOL_FIELDS, "protocol record")
    _check_shapes(rec, PROTOCOL_SHAPES, "protocol record")
    topic = str(rec.get("topic") or "").strip()
    if not topic:
        raise ValueError("protocol record 必须含 topic（议题名）")
    rec["topic"] = topic
    rec["updated"] = today()

    d = TOPICS / _safe_dirname(topic)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "protocol.md"
    ledger = d / "protocol.sha256"
    body = _render_protocol(topic, rec)

    # 每次运行的漏斗数字单独落一份（协议可能被用户改过，但漏斗不能丢）
    # ⚠️ 同一天第二次运行**不能覆盖第一次**：2026-10-22 同一议题先跑深入再跑简要，
    # 两次的漏斗（201 条 vs 132 条候选）本该各留一份，旧实现按 `run-<日期>.json`
    # 命名，谁后写谁赢——第一轮的分诊证据就没了。同名但内容不同 ⇒ 加序号另存。
    run = rec.get("run_funnel") or {}
    run_body = dict(run) if isinstance(run, dict) else {"value": run}
    run_body.setdefault("topic", topic)
    run_body.setdefault("date", today())
    run_text = json.dumps(run_body, ensure_ascii=False, indent=2)
    run_path = d / f"run-{today()}.json"
    n = 2
    while run_path.exists() and run_path.read_text(
            encoding="utf-8", errors="replace").strip() != run_text.strip():
        run_path = d / f"run-{today()}-{n}.json"
        n += 1
    run_path.write_text(run_text, encoding="utf-8")

    old_digest = None
    if ledger.exists():
        parts = ledger.read_text(encoding="utf-8").split()
        old_digest = parts[0] if parts else None

    if p.exists():
        current = p.read_text(encoding="utf-8")
        if old_digest and _sha256_text(current) != old_digest:
            newp = d / "protocol.md.new"
            newp.write_text(body, encoding="utf-8")
            return {"action": "保留用户版本", "topic": topic, "path": str(p),
                    "new_path": str(newp), "run_path": str(run_path),
                    "note": "检测到 protocol.md 被手工修改过（哈希与账本不符），"
                            "已保留你的版本，新版另存为 protocol.md.new。"}
        # 哈希对得上 = 用户没改过 → 自动升级
    p.write_text(body, encoding="utf-8")
    ledger.write_text(f"{_sha256_text(body)}  protocol.md\n", encoding="utf-8")
    return {"action": "写入", "topic": topic, "path": str(p),
            "ledger": str(ledger), "run_path": str(run_path)}


def read_protocol(topic: str) -> dict:
    """读回协议（报告 §五 的数据源）。"""
    d = TOPICS / _safe_dirname(topic)
    p = d / "protocol.md"
    if not p.exists():
        return {"exists": False, "topic": topic,
                "note": "这个议题还没有落盘检索协议。报告 §五 只能手写——"
                        "下次用 registry_upsert(kind='protocol') 落一次。"}
    raw = p.read_text(encoding="utf-8")
    runs = sorted(d.glob("run-*.json"))
    return {"exists": True, "topic": topic, "path": str(p), "content": raw,
            "runs": [str(x) for x in runs]}


def list_protocols() -> list[dict]:
    out = []
    if not TOPICS.is_dir():
        return out
    for d in sorted(TOPICS.iterdir()):
        if d.is_dir() and (d / "protocol.md").exists():
            out.append({"dir": str(d), "protocol": str(d / "protocol.md"),
                        "runs": [x.name for x in sorted(d.glob("run-*.json"))]})
    return out


def upsert_video(rec: dict) -> dict:
    """写入视频记录。**第二次分析同一条 BV 时是合并，不是覆盖。**

    为什么必须合并（2026-10-22 实测事故）
    ------------------------------------
    同一议题先跑「深入」再跑「简要」，两条命令都会 commit。旧实现是
    `vids[bvid] = rec` 整体替换，于是简要那一轮（**没读字幕、没读图**）
    把深入那一轮的 `key_findings`（评论+字幕+弹幕的结论）与 `grade` 一起冲掉，
    `transcript_pulled` 也从 `true` 变回 `false`。账面上"更新成功"，
    实际是把更贵的证据删了。实测该议题 11 条视频里 4 条被降级覆盖，
    同一批 14 条可比 BV 有 10 条两次评级不同（其中 7 条**来源字母**都不同）。

    合并规则（每条都可单独解释）
    ---------------------------
    * `key_findings`  **并集**（旧的前置，去重，上限 12 条）——旧的通常更深
    * `transcript_pulled`  **只升不降**（读过就是读过，不可撤销）
    * `topics`  **并集**
    * `grade`  **证据更少的一轮不覆盖**：旧记录读过视频本体、新记录没读，
      且议题集合是旧的子集 ⇒ 保留旧评级，新评级记进 `grade_history`
    * 其余字段新值优先（`verdict` / `redundancy` / `selected_because` 等随本轮判断走）
    """
    rec = _normalize_record(rec)
    bvid = rec.get("bvid")
    if not bvid:
        raise ValueError("video record 必须含 bvid")
    _check_fields(rec, VIDEO_FIELDS, "video record")
    _check_shapes(rec, VIDEO_SHAPES, "video record")
    db = _load(VIDEOS, {"videos": {}})
    vids = db.setdefault("videos", {})
    old = vids.get(bvid) or {}
    action = "更新" if old else "新增"
    rec.setdefault("analyzed_at", today())

    changes: dict = {}

    def _uniq(items: list) -> list:
        seen, out = set(), []
        for x in items:
            s = str(x).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    if old:
        # 先把「本轮到底读没读视频本体」记下来——下面 ② 会把 rec 里的值改成 OR 结果，
        # 若在那之后再判断"是不是更浅的一轮"，条件永远为假（实测踩到）。
        new_pulled = bool(rec.get("transcript_pulled"))
        old_pulled = bool(old.get("transcript_pulled"))

        # ① 结论并集：旧的在前（通常来自更深的一轮）
        merged = _uniq(list(old.get("key_findings") or []) + list(rec.get("key_findings") or []))
        if len(merged) > len(rec.get("key_findings") or []):
            changes["findings_kept"] = len(merged) - len(rec.get("key_findings") or [])
        rec["key_findings"] = merged[:12]
        if len(merged) > 12:
            changes["findings_truncated"] = len(merged) - 12

        # ② 读过视频本体这件事不可撤销
        pulled = old_pulled or new_pulled
        if pulled and not new_pulled:
            changes["transcript_preserved"] = True
        rec["transcript_pulled"] = pulled

        # ③ 议题并集（同一 BV 会被多个议题复用）
        merged_topics = _uniq(list(old.get("topics") or []) + list(rec.get("topics") or []))
        if len(merged_topics) > len(rec.get("topics") or []):
            changes["topics_added"] = len(merged_topics) - len(rec.get("topics") or [])
        rec["topics"] = merged_topics

        # ④ 评级：证据更少的一轮不覆盖
        og, ng = str(old.get("grade") or ""), str(rec.get("grade") or "")
        hist = [h for h in (old.get("grade_history") or []) if isinstance(h, dict)]
        if og and ng and og != ng:
            shallower = (old_pulled and not new_pulled
                         and set(rec.get("topics") or []) <= set(old.get("topics") or []))
            if shallower:
                # 当前值保持旧评级；被拒的那次记进历史（留痕，但标注它没生效）
                hist.append({"date": today(), "grade": ng, "kept": False,
                             "note": "未读视频本体，证据少于已有记录"})
                rec["grade"] = og
                changes["grade_kept"] = f"{ng} 被拒，保留 {og}"
                changes["grade_note"] = "本轮未读视频本体，证据少于已有记录，不覆盖评级"
            else:
                hist.append({"date": old.get("analyzed_at") or "", "grade": og})
                changes["grade"] = f"{og} → {ng}"
                changes["grade_note"] = f"新依据：{rec.get('grade_basis') or '（未填）'}"
        rec["grade_history"] = hist[-6:]
    vids[bvid] = rec
    db.setdefault("_meta", {})["updated"] = today()
    _atomic_write(VIDEOS, db)
    out = {"action": action, "bvid": bvid, "grade": rec.get("grade"),
           "redundancy": rec.get("redundancy")}
    if changes:
        out["changes"] = changes
    return out


def get_source(mid) -> dict | None:
    db = _load(SOURCES, {"sources": []})
    return next((s for s in db.get("sources", []) if str(s.get("mid")) == str(mid)), None)


def get_video(bvid: str) -> dict | None:
    return (_load(VIDEOS, {"videos": {}}).get("videos") or {}).get(bvid)


def list_sources(domain: str | None = None, grade: str | None = None) -> list[dict]:
    rows = _load(SOURCES, {"sources": []}).get("sources", [])
    if domain:
        rows = [s for s in rows if domain in (s.get("domains") or [])]
    if grade:
        rows = [s for s in rows if str(s.get("grade", "")).upper().startswith(grade.upper())]
    return sorted(rows, key=lambda x: str(x.get("grade", "Z")))


def list_videos(topic: str | None = None) -> list[dict]:
    items = list((_load(VIDEOS, {"videos": {}}).get("videos") or {}).values())
    if topic:
        items = [v for v in items if topic in (v.get("topics") or [])]
    return items


def stats() -> dict:
    srcs = _load(SOURCES, {"sources": []}).get("sources", [])
    vids = _load(VIDEOS, {"videos": {}}).get("videos", {})
    bygrade: dict[str, int] = {}
    for s in srcs:
        g = s.get("grade", "?")
        bygrade[g] = bygrade.get(g, 0) + 1
    bytop: dict[str, int] = {}
    for v in vids.values():
        for t in (v.get("topics") or ["（未标议题）"]):
            bytop[t] = bytop.get(t, 0) + 1
    ins = INSIGHTS.stat().st_size if INSIGHTS.exists() else 0
    topics_db = _load(TOPICS_JSON, {"topics": {}}).get("topics") or {}
    protos = list_protocols()
    cs = cost_summary(window="run") if COST_LOG.exists() else {}
    return {
        "root": str(ROOT),
        "sources": len(srcs),
        "sources_by_grade": bygrade,
        "conflicts": sum(len(s.get("conflicts") or []) for s in srcs),
        "videos": len(vids),
        "videos_by_topic": dict(sorted(bytop.items(), key=lambda kv: -kv[1])[:10]),
        "topics": len(topics_db),
        "topic_names": sorted(topics_db.keys()),
        "protocols": len(protos),
        "topics_files": len(list(TOPICS.glob("*"))) if TOPICS.is_dir() else 0,
        "insights_kb": round(ins / 1024, 1),
        "insights_note": "超过 64 KB 就该压缩合并了（见 registry.md 维护规则）" if ins > 64 * 1024 else None,
        # 上一轮跑了多少：开工前一眼看到"上次值不值"，比累计值有用
        "last_run": ({"run": cs.get("run"), "upstream_calls": cs.get("upstream_calls"),
                      "est_output_tokens": cs.get("est_output_tokens")}
                     if cs.get("has_marker") else None),
        "reports": _report_index(),
    }


def _report_index() -> dict:
    """报告库概览（只数，不读内容）。`kind="stats"` 里给人看"我有哪些产出"。

    ⚠️ 这里**延迟导入** reports：它要读 `BIG_REPORT_DIR`，而那个环境变量
    可能在测试里被 monkeypatch。模块级 import 会把路径快照在导入那一刻。
    """
    try:
        from . import reports
        root = reports.library_root()
        dirs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
        return {"root": str(root), "n_dirs": len(dirs),
                "recent": [p.name for p in dirs[-5:]]}
    except Exception as e:                                    # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------- 经验沉淀（insights） ----------------
#
# 为什么单开一个文件，而不是塞进 sources.json / videos.json
# --------------------------------------------------------
# 那两个是**结构化记录**（一条一条，字段固定），而经验是**跨议题的、带语境的判断**：
#   「B站财经区的收益预期普遍锚在 8–10%，所以任何"提前退休"测算都要先查这个锚」
# 它既不属于某个 UP，也不属于某条视频，删掉任何一条结构化记录都不该影响它。
# 写成 markdown 而不是 JSON，是为了用户能直接读、直接改——这是给人看的资产，不是机器的索引。

INSIGHT_SECTIONS = {"领域": "领域认知", "信源": "信源认知", "方法": "方法认知"}


def append_insights(topic: str, records: list[dict]) -> dict:
    """追加一次议题的经验条目。

    records 每条：{"section": "领域"|"信源"|"方法", "text": "..."}
      ｜领域｜这个议题本身的**事实性理解**（跨时间仍成立的那种），不是本次结论的复述
      ｜信源｜哪个 UP 在什么问题上可信/不可信，以及**为什么**
      ｜方法｜这次流程哪里失准：判据、关键词族、离场规则的偏差

    只写"下次能直接用"的话。写完自检：这条会不会在下一个议题里帮上忙？
    不会就别写——资产库最怕的不是空，是垃圾。

    指令注入拦截（2026-10-21 加）
    ----------------------------
    `text` 是**自由文本**，而 insights.md 是**跨任务**读的：
    一条被注入的句子被当成"洞察"写进来，就等于把注入**持久化**了——
    下个任务读 insights 时它又被读回来。所以这里对每条 text 跑 `flag_injection`，
    命中就**拒写**并说明原因。

    为什么在写入侧拦而不是在读取侧：读取侧拦只能保护"这一次"，
    而污染一旦落盘就会一直传播。**写侧是唯一的收口点。**
    """
    lines = []
    bad = []
    injected = []
    for r in records:
        sec = str(r.get("section", "")).strip()
        text = str(r.get("text", "")).strip()
        if sec not in INSIGHT_SECTIONS or not text:
            bad.append(r)
            continue
        risk = flag_injection(text)
        if risk:
            injected.append({"section": sec, "text": text,
                             "reasons": risk["reasons"],
                             "why": "该条目命中指令注入特征。经验库是跨任务读取的，"
                                    "写进来等于把注入持久化。"})
            continue
        lines.append(f"- **[{sec}]** {text}")
    if not lines:
        return {"error": "没有可写入的条目", "sections": list(INSIGHT_SECTIONS),
                "rejected": bad, "injection_rejected": injected,
                "note": ("若全部条目都因注入特征被拒，请把它们改写成**判断句**"
                         "（不要含祈使句与「忽略…规则」类措辞）后重试。")}

    ROOT.mkdir(parents=True, exist_ok=True)
    if not INSIGHTS.exists():
        INSIGHTS.write_text(
            "# 经验沉淀\n\n"
            "> 跨议题积累的认知，不是议题报告。三类：[领域] 事实理解 / [信源] 谁在什么问题上可信 / "
            "[方法] 流程哪里失准。\n> 写入标准只有一个：**它对下一个议题有用吗**。\n",
            encoding="utf-8")
    head = f"\n## {today()} · {topic}\n\n"
    # 去重：同一条经验**不写两遍**。
    # 为什么要：暂存区 commit 可能重复提交；经验库是**只增不压**的，
    # 重复条目会挤占 64 KB 的维护预算，还会污染"这条判断出现过几次"的直觉。
    existing = INSIGHTS.read_text(encoding="utf-8") if INSIGHTS.exists() else ""
    seen = {ln.strip() for ln in existing.splitlines()}
    fresh, dup = [], []
    for line in lines:
        if line in seen:
            dup.append(line)
            continue
        seen.add(line)
        fresh.append(line)
    if not fresh:
        return {"ok": True, "written": 0, "rejected": bad,
                "injection_rejected": injected, "duplicates": len(dup),
                "file": str(INSIGHTS), "size_kb": round(INSIGHTS.stat().st_size / 1024, 1),
                "note": "这些条目已经写过了，没有重复追加。"}
    with INSIGHTS.open("a", encoding="utf-8") as f:
        f.write(head + "\n".join(fresh) + "\n")
    return {"ok": True, "written": len(fresh), "rejected": bad,
            "injection_rejected": injected, "duplicates": len(dup),
            "file": str(INSIGHTS), "size_kb": round(INSIGHTS.stat().st_size / 1024, 1)}


def read_insights(topic: str | None = None, max_kb: int = 24) -> dict:
    """读经验沉淀。默认全量（文件本来就不该大），给了 topic 就只返回命中的小节。

    超过 max_kb 时只返回**最后**那部分（最近的更相关），并明确告知被截断——
    静默截断会让人以为"以前没记过"。
    """
    if not INSIGHTS.exists():
        return {"exists": False, "note": "还没有任何沉淀。第一次任务结束后就该写第一条。"}
    raw = INSIGHTS.read_text(encoding="utf-8")
    truncated = False
    if len(raw.encode("utf-8")) > max_kb * 1024:
        raw = raw[-max_kb * 1024:]
        truncated = True
    if topic:
        blocks = raw.split("\n## ")
        hit = [b for b in blocks[1:] if topic in b.split("\n", 1)[0]]
        if hit:
            raw = "## " + "\n## ".join(hit)
        else:
            return {"exists": True, "topic": topic, "match": False,
                    "note": "经验库里没有这个议题的记录。先读全量，别假设没有。"}
    return {"exists": True, "match": True, "truncated": truncated,
            "content": raw.strip()}


# ---------------- 写侧闸门：暂存区（批次 C1） ----------------
#
# 为什么要有这一层
# --------------
# DSH 插件的做法是「能力最小化」：一轮里只给模型几个只读工具，
# 写侧的工具**连名字都看不到**。本项目 14 个工具平铺暴露，
# `registry_upsert` 是唯一有副作用的——现在的约束是"软规则"
# （SKILL.md 硬性规则 13 要求"每次任务结束必须写 insights"），
# 但**代码不阻止 Agent 在任意时刻写**。
#
# 任务书给的两个方案里选**延迟合并**（不是阶段闸门）：
#   * 阶段闸门 = 非收尾阶段直接拒绝写入 ⇒ "边挖边记"变麻烦，且拒绝理由靠猜
#   * 延迟合并 = 非收尾阶段先落 `.registry/staging/`，收尾时 `kind="commit"` 一次合并
#     ⇒ 写侧**始终可用**（不打断流程），但**碰不到正式库**；
#       正式库只在收尾那一次被改动，改动内容可预览、可回滚。
#
# 关键性质：**任务书明确要求"别两种都做"**——这里只做延迟合并。
STAGE_SOURCES = STAGING / "sources.json"
STAGE_VIDEOS = STAGING / "videos.json"
STAGE_INSIGHTS = STAGING / "insights.json"
STAGE_TOPICS = STAGING / "topics.json"
STAGE_PROTOCOLS = STAGING / "protocols.json"
# 每类记录的：暂存文件名 / 字段白名单 / 形状契约 / 主键字段
# （insight 没有单一主键——它是 {topic, section, text} 的集合，单独走 stage_insights）
#
# ⚠️ 这里只存**文件名**，不存完整路径：路径必须在调用时从 `STAGING` 现算，
# 否则模块导入那一刻就把路径**快照**下来了，之后改 `STAGING` 也不生效——
# 那会让测试（和任何想换暂存位置的调用方）写到意料之外的目录。
_STAGE_FILES = {
    "source": "sources.json", "video": "videos.json", "insight": "insights.json",
    "topic": "topics.json", "protocol": "protocols.json", "cluster": "clusters.json",
}
_STAGE_SPEC = {
    "source": (SOURCE_FIELDS, SOURCE_SHAPES, "mid"),
    "video": (VIDEO_FIELDS, VIDEO_SHAPES, "bvid"),
    "topic": (TOPIC_FIELDS, TOPIC_SHAPES, "topic"),
    "protocol": (PROTOCOL_FIELDS, PROTOCOL_SHAPES, "topic"),
    "insight": (None, None, None),
    # 簇的主键是三元组：同一视频下的不同簇要分开存，同名簇才覆盖。
    "cluster": (CLUSTER_FIELDS, CLUSTER_SHAPES, ("topic", "bvid", "cluster")),
}


def _stage_path(kind: str) -> pathlib.Path:
    return STAGING / _STAGE_FILES[kind]


def _stage_load(kind: str) -> list:
    return _load(_stage_path(kind), {"records": []}).get("records") or []


def _stage_save(kind: str, records: list) -> None:
    STAGING.mkdir(parents=True, exist_ok=True)
    _atomic_write(_stage_path(kind),
                  {"_meta": {"updated": today(), "kind": kind},
                   "records": records})


def _rec_key(rec: dict, key_field) -> str:
    """取记录的主键。支持复合主键（tuple/list）——簇是 (topic, bvid, cluster)。"""
    if isinstance(key_field, (tuple, list)):
        return " | ".join(str(rec.get(f)) for f in key_field)
    return str(rec.get(key_field))


def stage_record(kind: str, rec: dict) -> dict:
    """把一条记录放进暂存区（**不碰正式库**）。

    形状契约在**入暂存区时**就要过一遍：脏形状不该在暂存区里过夜，
    更不该在 commit 时才被发现（那时已经改到一半了）。
    """
    if kind not in _STAGE_SPEC:
        raise ValueError(f"暂存区不支持 kind={kind}")
    allowed, shapes, key_field = _STAGE_SPEC[kind]
    if allowed is None:
        raise ValueError("kind=insight 请用 stage_insights（它按 {topic,section,text} 去重）")
    rec = _normalize_record(rec)
    _check_fields(rec, allowed, f"{kind} record")
    _check_shapes(rec, shapes, f"{kind} record")
    if isinstance(key_field, (tuple, list)):
        missing = [f for f in key_field if not rec.get(f)]
        if missing:
            raise ValueError(f"{kind} record 必须含 {' / '.join(key_field)}")
    elif not rec.get(key_field):
        raise ValueError(f"{kind} record 必须含 {key_field}")
    if kind == "source" and "name" not in rec:
        raise ValueError("source record 至少要有 mid 与 name")
    key = _rec_key(rec, key_field)

    recs = _stage_load(kind)
    # 同一条记录重复暂存 → 覆盖而不是堆叠（暂存区是"待提交清单"，不是流水账）
    recs = [r for r in recs if _rec_key(r, key_field) != key]
    rec["_staged_at"] = dt.datetime.now().isoformat(timespec="seconds")
    recs.append(rec)
    _stage_save(kind, recs)
    return {"action": "暂存", "kind": kind, "key": key,
            "staged": len(recs),
            "note": "已进暂存区，正式库未被改动。收尾时调用 "
                    "registry_upsert(kind='commit') 一次合并。"}


def stage_insights(topic: str, records: list[dict]) -> dict:
    """暂存经验条目（同样做注入拦截——拦在暂存区，正式库就永远干净）。"""
    if not topic:
        raise ValueError("暂存经验需要 topic（议题名）")
    good, rejected, injected = [], [], []
    for r in records:
        sec = str(r.get("section", "")).strip()
        text = str(r.get("text", "")).strip()
        if sec not in INSIGHT_SECTIONS or not text:
            rejected.append(r)
            continue
        risk = flag_injection(text)
        if risk:
            injected.append({"section": sec, "text": text, "reasons": risk["reasons"]})
            continue
        good.append({"section": sec, "text": text,
                     "staged_at": dt.datetime.now().isoformat(timespec="seconds")})
    recs = _stage_load("insight")
    for g in good:
        recs = [r for r in recs
                if not (r.get("topic") == topic and r.get("section") == g["section"]
                        and r.get("text") == g["text"])]
        g["topic"] = topic
        recs.append(g)
    _stage_save("insight", recs)
    return {"action": "暂存", "kind": "insight", "topic": topic,
            "staged": len(good), "rejected": rejected,
            "injection_rejected": injected,
            "note": "已进暂存区，正式库未被改动。收尾时调用 "
                    "registry_upsert(kind='commit') 一次合并。"}


def list_staging() -> dict:
    """看暂存区里有什么（Agent 收尾前自查用）。"""
    out = {}
    _kinds = ("source", "video", "insight", "topic", "protocol", "cluster")
    for kind in _kinds:
        recs = _stage_load(kind)
        out[kind] = {
            "n": len(recs),
            "keys": [str(r.get("mid") or r.get("bvid") or r.get("topic")
                         or r.get("text") or "")[:40] for r in recs][:20],
            "topics": sorted({r.get("topic") for r in recs if r.get("topic")}),
        }
    out["total"] = sum(out[k]["n"] for k in _kinds)
    out["note"] = ("暂存区非空时，正式库尚未被本次任务改动。"
                   "收尾时调用 registry_upsert(kind='commit') 合并。")
    return out


def commit_staging() -> dict:
    """把暂存区合并进正式库，并清空暂存区。

    合并语义：
      * source / video：走**正常的 upsert 路径**（评级变动检测、原子写都照旧生效）
      * insight：追加进 `insights.md`，**按正文去重**（同一条经验不会被写两遍）
      * topic：写入议题档案；protocol：写入协议文件（走各自的正常路径）
    """
    out = {"source": [], "video": [], "insight": [], "topic": [], "protocol": [],
           "cluster": [], "committed": 0}
    for kind in ("source", "video"):
        recs = _stage_load(kind)
        for r in recs:
            clean = {k: v for k, v in r.items() if not k.startswith("_")}
            try:
                res = (upsert_source(clean) if kind == "source"
                       else upsert_video(clean))
                out[kind].append(res)
                out["committed"] += 1
            except Exception as e:                            # noqa: BLE001
                out[kind].append({"error": f"{type(e).__name__}: {e}",
                                  "input": clean})
        if recs:
            _stage_save(kind, [])

    recs = _stage_load("insight")
    by_topic: dict[str, list[dict]] = {}
    for r in recs:
        by_topic.setdefault(r.get("topic") or "未指定议题", []).append(
            {"section": r.get("section"), "text": r.get("text")})
    for topic, items in by_topic.items():
        res = append_insights(topic, items)
        out["insight"].append({"topic": topic, "written": res.get("written"),
                                "rejected": res.get("rejected"),
                                "injection_rejected": res.get("injection_rejected")})
        out["committed"] += res.get("written") or 0
    if recs:
        _stage_save("insight", [])

    for kind, fn in (("topic", upsert_topic), ("protocol", write_protocol)):
        recs = _stage_load(kind)
        for r in recs:
            clean = {k: v for k, v in r.items() if not k.startswith("_")}
            try:
                out[kind].append(fn(clean))
                out["committed"] += 1
            except Exception as e:                            # noqa: BLE001
                out[kind].append({"error": f"{type(e).__name__}: {e}",
                                  "input": clean})
        if recs:
            _stage_save(kind, [])

    # 观点簇按议题分组落盘（`topics/<议题>/clusters.json`）——判断的痕迹要和报告放一起
    recs = _stage_load("cluster")
    by_topic_c: dict[str, list[dict]] = {}
    for r in recs:
        clean = {k: v for k, v in r.items() if not k.startswith("_")}
        by_topic_c.setdefault(clean.get("topic") or "未指定议题", []).append(clean)
    for topic, items in by_topic_c.items():
        try:
            res = upsert_clusters(topic, items)
            out["cluster"].append(res)
            out["committed"] += (res.get("added") or 0) + (res.get("updated") or 0)
        except Exception as e:                                # noqa: BLE001
            out["cluster"].append({"error": f"{type(e).__name__}: {e}", "topic": topic})
    if recs:
        _stage_save("cluster", [])

    out["ok"] = True
    out["note"] = "暂存区已清空，正式库已更新。"
    return out


def discard_staging() -> dict:
    """丢弃暂存区（不合并）。用于"这轮白跑了，不要写进资产库"。"""
    n = 0
    for kind in ("source", "video", "insight", "topic", "protocol", "cluster"):
        recs = _stage_load(kind)
        n += len(recs)
        if recs:
            _stage_save(kind, [])
    return {"ok": True, "discarded": n,
            "note": "暂存区已清空，正式库未被改动。"}


# ---------------- 调用账本（批次 D5 · 批次 F 加轮转与单轮窗口） ----------------
#
# 账本回答的问题只有一个：**跑一轮议题到底花了多少**。
# 这个数字以前拿不到，后果是「深入分析值不值」无法判断——
# §五 的偏差声明里只能写"跑了很久"，写不出"84 次上游调用 / 9.6 万输出 token"。
#
# 单轮怎么界定（这是本轮唯一需要新增的协议）
# ------------------------------------------
# 靠**显式标记**，不靠时间启发式：`mark_run_start()` 往账本里写一行
# `{"tool": "__run_start__", "run": <id>}`，`cost_summary(window="run")`
# 取最后一条标记之后的所有行。
# 为什么不用"两条记录间隔小于 N 分钟算同一轮"：ASR 一次就可能跑 10 分钟，
# 真实的一轮里天然存在长间隔；用时间切片会把一轮切成两轮，数字看着像半途而废。
#
# 为什么必须轮转
# --------------
# 一行一次工具调用，单轮 30–90 行。长期用下来文件无限长，
# 每读一次 `cost_summary` 都要把整份文件读进内存（现在只是 `[-limit:]` 遮住了症状）。
# 按月归档 + 只留最近 `COST_KEEP_ARCHIVES` 份，增长有上界。
COST_ROTATE_BYTES = 512 * 1024          # 超过 512 KB 就归档（约 3–5 千行）
COST_KEEP_ARCHIVES = 12                 # 最近 12 个月
RUN_START_TOOL = "__run_start__"
RUN_END_TOOL = "__run_end__"
# 为什么要 run-end（2026-10-22 实测）
# --------------------------------
# 只有 run-start 时，`window="run"` 的右边界是「现在」。Agent 在写报告时会先取一次
# md_line 贴进 §五，然后才 commit / 渲染——**这几步本身也记账**，于是贴进报告的
# 数字永远小于账本里同一条命令后来的读数。实测差 6.8%（88,515 vs 94,916），
# 且两次查询都"正常返回"，没人会发现。
# ⇒ 收尾时打 run-end，单轮窗口**闭合**，此后任何时刻查询都得到同一个数字。
#    `registry_upsert(kind="commit")` 会自动打这个标记并回传最终 md_line。

_COST_KEYS = ("calls", "comment_rows", "transcript_chars", "est_output_tokens")


def _append_cost_row(row: dict) -> None:
    """原子追加一行。失败静默——记账不该让工具调用失败。"""
    try:
        COST_LOG.parent.mkdir(parents=True, exist_ok=True)
        for k in _COST_KEYS:
            row[k] = int(row.get(k) or 0)
        with open(COST_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:                                         # noqa: BLE001
        pass


def rotate_cost_log(force: bool = False) -> str | None:
    """账本超过阈值就按月份归档，返回归档路径（没轮转则 None）。

    同月归档已存在时**追加**，不覆盖——否则一个月内轮转两次会丢前半段。
    """
    try:
        if not COST_LOG.exists():
            return None
        if not force and COST_LOG.stat().st_size < COST_ROTATE_BYTES:
            return None
        arc = COST_LOG.parent / f"cost-log-{dt.date.today().strftime('%Y%m')}.jsonl"
        with open(COST_LOG, "r", encoding="utf-8", errors="replace") as src, \
                open(arc, "a", encoding="utf-8") as dst:
            for line in src:
                if line.strip():
                    dst.write(line if line.endswith("\n") else line + "\n")
        COST_LOG.unlink()
        for old in sorted(COST_LOG.parent.glob("cost-log-*.jsonl"))[:-COST_KEEP_ARCHIVES]:
            try:
                old.unlink()
            except OSError:
                pass
        return str(arc)
    except Exception:                                         # noqa: BLE001
        return None


def append_cost(tool: str, cost: dict) -> None:
    """追加一条调用记录。**失败必须静默**——记账不该让工具调用失败。"""
    rotate_cost_log()
    _append_cost_row({"ts": dt.datetime.now().isoformat(timespec="seconds"),
                      "tool": str(tool or "unknown"),
                      **{k: int((cost or {}).get(k) or 0) for k in _COST_KEYS}})


def mark_run_start(topic: str = "", note: str = "") -> dict:
    """在账本里打一个「这一轮从这里开始」的标记。

    调用时机：阶段 0 结束（和落检索协议同一次收尾动作）。
    零上游调用、零成本，换来的是一份可复算的单轮账。
    """
    now = dt.datetime.now()
    run = now.strftime("%Y%m%dT%H%M%S")
    # 秒级 id 会在"同一秒内起两轮"时撞车（自动化/测试里真会发生），
    # 而 run-end 是按 id 匹配的——撞车会让两条窗口互相闭合。撞了就加序号。
    rows_now, _, _ = _read_cost_rows(500)
    existing = {r.get("run") for r in rows_now if r.get("tool") == RUN_START_TOOL}
    if run in existing:
        n = 2
        while f"{run}-{n}" in existing:
            n += 1
        run = f"{run}-{n}"
    _append_cost_row({"ts": now.isoformat(timespec="seconds"), "tool": RUN_START_TOOL,
                      "run": run, "topic": str(topic or ""),
                      "note": str(note or "")})
    return {"action": "标记", "run": run, "topic": str(topic or ""),
            "log": str(COST_LOG),
            "note": "收尾时先 registry_upsert(kind='commit')——它会打 run-end 并回传最终 md_line；"
                    "把那个 md_line 原样贴进报告 §五，数字此后不会再变。"}


def mark_run_end(note: str = "") -> dict:
    """闭合当前这一轮：之后 `window="run"` 不再把新行算进来。

    没有 run-start 时返回 `closed: False`，不写标记——宁可没有结束线，
    也不要写一条无主的 `__run_end__` 把别人的窗口切歪。
    """
    rows, _, _ = _read_cost_rows(500)
    starts = [i for i, r in enumerate(rows) if r.get("tool") == RUN_START_TOOL]
    if not starts:
        return {"action": "跳过", "closed": False,
                "note": "账本里没有 run-start，无法闭合；本轮开销请按 window='all' 说明。"}
    run = rows[starts[-1]].get("run")
    # 已经有比这条 start 更晚的 end ⇒ 重复收尾，保持幂等
    if [i for i, r in enumerate(rows)
            if r.get("tool") == RUN_END_TOOL and i > starts[-1]]:
        return {"action": "已收尾", "closed": True, "run": run,
                "note": "这一轮已经闭合过，不重复写标记。"}
    _append_cost_row({"ts": dt.datetime.now().isoformat(timespec="seconds"),
                      "tool": RUN_END_TOOL, "run": run, "note": str(note or "")})
    return {"action": "收尾", "closed": True, "run": run,
            "note": "单轮窗口已闭合：此后 registry_get(kind='cost', window='run') "
                    "返回的是固定数字，可复现。"}


def _fmt_int(n: int | float) -> str:
    return f"{int(n or 0):,}"


def _read_cost_rows(limit: int) -> tuple[list[dict], int, bool]:
    """读账本尾部。返回 (rows, 总行数, 是否被截断)。坏行跳过。"""
    if not COST_LOG.exists():
        return [], 0, False
    lines = COST_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    rows = []
    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except Exception:                                     # noqa: BLE001
            continue
    return rows, len(lines), len(lines) > limit


def _agg_cost(rows: list[dict]) -> dict:
    """把一组账本行加总。标记行（`__` 开头）不计入。"""
    stat = [r for r in rows if not str(r.get("tool") or "").startswith("__")]
    out = {"n_calls": len(stat)}
    for k in _COST_KEYS:
        out["upstream_calls" if k == "calls" else k] = sum(int(r.get(k) or 0) for r in stat)
    out["since"] = stat[0].get("ts") if stat else None
    out["until"] = stat[-1].get("ts") if stat else None
    return out


def _cost_segments(limit: int = 500) -> list[dict]:
    """把账本切成"一轮一段"，每段给出自己的开销。

    为什么需要：`window="run"` 只有最近一轮，而"深入 vs 简要差多少"恰恰要比较
    两次运行——2026-10-22 两轮跑完，回头查深入那一轮已经查不到了。
    """
    rows, _, _ = _read_cost_rows(limit)
    starts = [i for i, r in enumerate(rows) if r.get("tool") == RUN_START_TOOL]
    segs: list[dict] = []
    for n, i in enumerate(starts):
        run = rows[i].get("run")
        nxt = starts[n + 1] if n + 1 < len(starts) else len(rows)
        ends = [j for j in range(i + 1, nxt)
                if rows[j].get("tool") == RUN_END_TOOL and rows[j].get("run") == run]
        stop = ends[0] if ends else nxt
        closed_by = "run-end" if ends else ("next-start" if nxt < len(rows) else "open")
        agg = _agg_cost(rows[i + 1:stop])
        seg = {"run": run, "topic": rows[i].get("topic"),
               "closed_by": closed_by, **agg}
        seg["md_line"] = (
            f"**单轮开销**：上游调用 {_fmt_int(agg['upstream_calls'])} 次"
            f"｜评论行 {_fmt_int(agg['comment_rows'])}"
            f"｜转写字符合计 {_fmt_int(agg['transcript_chars'])}"
            f"｜输出估算约 {_fmt_int(agg['est_output_tokens'])} token"
            f"（口径 CJK×0.7+其它×0.25，仅用于横向比较，不是账单）")
        if closed_by == "open":
            seg["md_line"] += "｜⚠️ 本轮未收尾，数字可能偏低"
        segs.append(seg)
    return segs


def cost_summary(limit: int = 500, window: str = "all") -> dict:
    """读最近 `limit` 条调用记录，按工具汇总。

    window：
      "all"  全部（最近 limit 行，默认 500）
      "run"  **最近一轮**——最后一条 `__run_start__` 之后、对应 `__run_end__` 之前的行
      "runs" **每一轮各一行**（按时间序）——用来对比"深入 vs 简要"花在哪

    ⚠️ `est_output_tokens` 是**输出侧估算**，不是账单金额；
    真实费用还要乘供应商单价，且输入侧 token（上下文里读到的返回）没算进来。
    它的用途是**横向比较**（哪个工具最费、一轮跑了几次），不是报账。
    """
    w = (window or "all").lower()
    if w not in ("all", "run", "runs"):
        raise ValueError(f'window 只能是 "all" / "run" / "runs"，收到 {window!r}')
    if w == "runs":
        return {"exists": True, "window": "runs", "runs": _cost_segments(limit)}
    rows, total_lines, truncated = _read_cost_rows(limit)
    if not rows:
        return {"exists": False, "window": w,
                "note": "还没有调用账本（cost-log.jsonl）。"}

    marked = [i for i, r in enumerate(rows) if r.get("tool") == RUN_START_TOOL]
    run_id = None
    closed = False
    if w == "run":
        if not marked:
            return {"exists": True, "window": "run", "has_marker": False,
                    "note": "账本里没有「本轮开始」标记。先调 "
                            "registry_upsert(kind='run-start')，否则无法把这一轮"
                            "与历史记录分开。下面给出的是全部历史（window='all' 的口径）。",
                    "fallback": cost_summary(limit, "all")}
        run_id = rows[marked[-1]].get("run")
        head = marked[-1] + 1
        ends = [i for i, r in enumerate(rows)
                if r.get("tool") == RUN_END_TOOL and i > head
                and r.get("run") == run_id]
        if ends:
            closed = True
            rows = rows[head:ends[0]]
        else:
            rows = rows[head:]
        if not rows:
            return {"exists": True, "window": "run", "has_marker": True,
                    "closed": closed, "run": run_id,
                    "n_calls": 0, "upstream_calls": 0, "est_output_tokens": 0,
                    "by_tool": {}, "md_line": "**单轮开销**：本轮还没有任何工具调用记录。"}

    by_tool: dict[str, dict] = {}
    for r in rows:
        t = r.get("tool") or "?"
        if t.startswith("__"):                                # 标记行不计入统计
            continue
        b = by_tool.setdefault(t, {"n": 0, "calls": 0, "comment_rows": 0,
                                   "transcript_chars": 0, "est_output_tokens": 0})
        b["n"] += 1
        for k in _COST_KEYS:
            b[k] += int(r.get(k) or 0)
    stat_rows = [r for r in rows if not str(r.get("tool") or "").startswith("__")]

    calls = sum(r.get("calls") or 0 for r in stat_rows)
    comments = sum(r.get("comment_rows") or 0 for r in stat_rows)
    chars = sum(r.get("transcript_chars") or 0 for r in stat_rows)
    tokens = sum(r.get("est_output_tokens") or 0 for r in stat_rows)
    md_line = (f"**单轮开销**：上游调用 {_fmt_int(calls)} 次｜评论行 {_fmt_int(comments)}"
               f"｜转写字符合计 {_fmt_int(chars)}｜输出估算约 {_fmt_int(tokens)} token"
               f"（口径 CJK×0.7+其它×0.25，仅用于横向比较，不是账单）")
    if w == "run" and not closed:
        # 没闭合时必须自己说出来：这个数字还会涨，读者不该把它当定稿。
        md_line += "｜⚠️ 本轮未收尾，数字可能偏低"
    return {"exists": True, "window": w, "run": run_id,
            "has_marker": bool(marked) if w == "run" else None,
            "closed": closed if w == "run" else None,
            "n_calls": len(stat_rows),
            "upstream_calls": calls,
            "comment_rows": comments,
            "transcript_chars": chars,
            "est_output_tokens": tokens,
            "since": stat_rows[0].get("ts") if stat_rows else None,
            "until": stat_rows[-1].get("ts") if stat_rows else None,
            "lines_total": total_lines, "truncated": truncated,
            "by_tool": dict(sorted(by_tool.items(),
                                   key=lambda kv: -kv[1]["est_output_tokens"])),
            "md_line": md_line,
            "note": "est_output_tokens 是输出侧估算，用于横向比较，不是账单金额。"
                    "单轮口径 = 最后一条 run-start 之后、最后一条 run-end 之前的行；"
                    "`closed=true` 表示这一轮已收尾，数字可复现。"}
