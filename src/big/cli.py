"""命令行入口：给不想装 MCP 的场景用，也方便调试。

    python -m big.cli scan "普通人如何投资" "投资入门" --per-query 30 --budget 12
    python -m big.cli video BV1xxx --comments 100 --danmaku --images
    python -m big.cli video BV1xxx --transcript-query 收益率
    python -m big.cli registry stats
    python -m big.cli report-path --topic "普通人如何投资" --mode deep --mkdir
    python -m big.cli cost --window run
    python -m big.cli setup          # 自检：接口通不通、依赖全不全
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import registry, reports
from .comments import comment_stats, get_comments
from .danmaku import analyze as analyze_danmaku
from .danmaku import fetch_xml, parse as parse_danmaku
from .http import load_cookie, parse_bvid
from .media import OUT_ROOT, contact_sheet
from .search import scan_topic
from .video import fmt_ts, get_info, get_transcript


def _w(s: str = "") -> None:
    print(s)


def cmd_scan(a) -> None:
    res = scan_topic(a.keywords, a.per_query, a.max_candidates, a.budget, a.comment_n,
                     a.image_policy, 6, a.exclude, load_cookie())
    cov = res["coverage"]
    _w(f"== 覆盖率 ==  唯一候选 {cov['unique_candidates']}｜返回 {cov['returned']}"
       f"｜跨词族命中 {cov['multi_family_hits']}")
    for k in cov["per_keyword"]:
        flag = ""
        if k.get("error"):
            flag = f"  [ERR] {k['error']}"
        elif k.get("capped"):
            flag = "  （结果数封顶 1000）"
        _w(f"   {k['keyword']}：{k['n']} 条{flag}")
    _w()
    _w(f"== 候选表（{len(res['candidates'])}）==")
    _w(f"{'#':>3} {'BV':<14}{'播放':>10}{'弹幕':>7}{'评论':>7}{'赞/千播':>8} {'词族':<5}标题")
    for i, c in enumerate(res["candidates"], 1):
        mark = "*" if c["bvid"] in res["digested_bvids"] else " "
        _w(f"{i:>3}{mark}{c['bvid']:<14}{c['play']:>10}{c['danmaku']:>7}{c['reply']:>7}"
           f"{c['like_per_1k_play']:>8} {str(c['kw_families']):<5}{c['title'][:44]}"
           f"  ·{c['author'][:14]}")
    _w("\n（* = 本轮花了评论预算的候选）")
    for c in res["candidates"]:
        d = c.get("digest")
        if not d or d.get("error"):
            continue
        st = d.get("stats") or {}
        _w(f"\n--- {c['bvid']} {c['title'][:50]}")
        _w(f"    评论 {d.get('reply_total')}｜图片占赞 {st.get('picture_like_share')}%"
           f"（{d.get('picture_verdict') or '样本不足 100，不判读'}）"
           f"｜回复强度 {st.get('reply_intensity')}｜楼/赞 {st.get('reply_to_like')}%"
           f"｜等级 {st.get('level_count')}")
        for t in d.get("top") or []:
            _w(f"    {t['likes']:>6}赞 r{t['rcount']:<3} L{t['lv']} {t['loc'] or '--'}：{t['text']}")
        im = d.get("images")
        if im and im.get("sheets"):
            _w(f"    [图] 热评带图 {im.get('n')} 张（成功 {im.get('downloaded')}）"
               f"｜微表 {im['sheets'][0]}")
    if a.json:
        # 目录不存在就建：CLI 在管道被提前关闭时（如 `| head`）会静默丢输出，
        # 这里至少保证正常结束时文件一定落盘。
        parent = os.path.dirname(os.path.abspath(a.json))
        os.makedirs(parent, exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        _w(f"\n[OK] 完整结果已写入 {a.json}")


def cmd_video(a) -> None:
    cookie = load_cookie()
    bv = parse_bvid(a.video)
    info = get_info(bv, cookie)
    st = info["stats"]
    _w(f"== {info['title']}")
    _w(f"   {info['author']}（mid={info['author_mid']}）｜{fmt_ts(info['duration'])}"
       f"｜{info['tname']}｜播放 {st['view']} 赞 {st['like']} 币 {st['coin']}"
       f" 藏 {st['favorite']} 评 {st['reply']} 弹 {st['danmaku']}")
    if info.get("tags"):
        _w(f"   标签：{' / '.join(info['tags'][:12])}")

    if a.transcript_query is not None or a.head:
        cid = info["pages"][0]["cid"] if info["pages"] else None
        tr = get_transcript(bv, cid, cookie) if cid else None
        if not tr:
            _w("\n[WARN] 无字幕")
        else:
            lines = tr["lines"]
            if a.transcript_query:
                keys = a.transcript_query.split()
                idx = [i for i, l in enumerate(lines) if any(k in l["content"] for k in keys)]
                keep = set()
                for i in idx:
                    keep.update(range(max(0, i - 2), min(len(lines), i + 3)))
                lines = [lines[i] for i in sorted(keep)]
                _w(f"\n== 字幕命中 {len(idx)} 处（{tr['data_source']}）==")
            else:
                lines = lines[:max(1, int(len(lines) * a.head))]
                _w(f"\n== 字幕前 {int(a.head * 100)}%（{tr['data_source']}）==")
            for l in lines[: a.max_lines]:
                _w(f"  [{fmt_ts(l['from'])}] {l['content']}")

    if a.comments:
        cmts, total = get_comments(int(info["aid"]), a.comments, "hot", cookie)
        s = comment_stats(cmts)
        _w(f"\n== 评论 {len(cmts)} 条（全站 {total}）==")
        _w(f"   顶层 {s['n_top']} + 楼中楼 {s['n_replies']}；判据深度 {s['judge_depth']}"
           f"｜图片 {s['with_picture']} 条（{s['picture_share']}%）占赞 {s['picture_like_share']}%"
           f" ⇒ {s['picture_verdict']}")
        _w(f"   回复强度 {s['reply_intensity']}｜楼/赞 {s['reply_to_like']}%"
           f"｜等级 {s['level_count']}｜IP 属地 {s['locations']}")
        for c in sorted(cmts, key=lambda x: -x["likes"])[: a.max_lines]:
            tag = "（楼）" if c["is_reply"] else ""
            pics = f"[图×{len(c['pictures'])}]" if c.get("pictures") else ""
            body = c["content"] or ("（纯图片）" if c.get("kind") == "picture" else "（表情）")
            _w(f"   {c['likes']:>6}赞 {c['author'][:12]}{tag} {pics}{body[:70]}")
        if s["picture_verdict"] != "skip":
            _w(f"\n[提示] 图片占比不低，建议加 --images 走联系表")

    if a.images:
        cmts, total = get_comments(int(info["aid"]), max(a.comments or 100, 100), "hot", cookie)
        s = comment_stats(cmts)
        res = contact_sheet(bv, cmts, s, _out_dir() / bv)
        _w(f"\n== 评论区图片 ==  {res.get('verdict')}｜{res.get('n')} 张"
           f"（成功 {res.get('downloaded')}）")
        for p in res.get("sheets") or []:
            _w(f"   联系表：{p}")
        for g in res.get("grid") or []:
            _w(f"   #{g['#']:02d} {g['likes']:>6}赞 {g['size']:>11} {g['text'][:40]}")

    if a.danmaku:
        cid = info["pages"][0]["cid"] if info["pages"] else None
        if not cid:
            _w("\n[WARN] 无 cid，跳过弹幕")
        else:
            items = parse_danmaku(fetch_xml(cid, bv))
            res = analyze_danmaku(items, info.get("duration") or 0, top=a.top)
            _w(f"\n== 弹幕 {res['n']} 条（stat {st['danmaku']}）｜粒度 {res.get('granularity')} ==")
            if st["danmaku"]:
                _w(f"   评论/弹幕 = {st['reply'] / st['danmaku']:.2f}:1"
                   f"（受题材/时长/UP 设置共同影响，不要单独归因）")
            for p in res.get("peaks") or []:
                _w(f"   【{p['span']}】{p['n']} 条")
                for s2 in p["samples"]:
                    _w(f"      {s2}")
            if res.get("repeated"):
                _w("   高重复句：" + "；".join(f"{r['count']}×{r['text']}" for r in res["repeated"][:6]))
            d = res.get("disclosure") or {}
            if d.get("n"):
                _w(f"   披露线索 {d['n']} 条，首现 {d['first_at']}：" + "；".join(d["items"][:4]))
            else:
                _w("   披露线索：无")


def _out_dir():
    return OUT_ROOT


def cmd_registry(a) -> None:
    if a.sub == "stats":
        _w(json.dumps(registry.stats(), ensure_ascii=False, indent=2))
    elif a.sub == "init":
        _w(json.dumps(registry.init(), ensure_ascii=False, indent=2))
    elif a.sub == "sources":
        for s in registry.list_sources(a.domain):
            _w(f"{str(s.get('mid')):>12}  {str(s.get('grade')):<3} obs{s.get('observations', 0):<3} "
               f"{s.get('name', '')[:20]:<20} {'/'.join(s.get('domains') or [])}")
    elif a.sub == "videos":
        for v in registry.list_videos(a.topic):
            _w(f"{v.get('bvid', ''):<14} {str(v.get('grade')):<4} {str(v.get('redundancy')):<4} "
               f"{v.get('title', '')[:46]}")


def cmd_report_path(a) -> None:
    """算出一份报告的目录与三件产物路径。

    存在的理由：日期 / 模式 / 议题三段拼接**手写必漂**（`2026-10-22` vs `20261022`、
    `深入分析` vs `深入`、议题里带 `/` 或 `:`）。漂了不会报错，只会让报告散落在
    两三个目录里。所以路径一律由代码算。
    """
    try:
        p = reports.paths(a.topic, a.mode, a.date, mkdir=a.mkdir)
    except ValueError as e:
        _w(f"[FAIL] {e}")
        sys.exit(2)
    if a.json:
        _w(json.dumps(p, ensure_ascii=False, indent=2))
        return
    _w(f"目录   {p['dir']}{'' if a.mkdir else '   （未创建，加 --mkdir）'}")
    _w(f"md     {p['md']}")
    _w(f"html   {p['html']}")
    _w(f"pdf    {p['pdf']}")
    _w(f"标题   {p['title']}")


def cmd_cost(a) -> None:
    """打印调用账本的汇总。`--line` 直接给出可贴进报告 §五 的那一行。"""
    try:
        s = registry.cost_summary(a.limit, a.window)
    except ValueError as e:
        _w(f"[FAIL] {e}")
        sys.exit(2)
    if a.window == "run" and s.get("exists") and not s.get("has_marker"):
        # 没有标记时**不假装知道单轮**：先说清楚，再退化为全部历史。
        # 静默给一个数字比不给更糟——那个数字会被贴进报告，然后没人再核对。
        print("[WARN] 账本里没有「本轮开始」标记，下面退化为全部历史。"
              "先跑 `registry_upsert(kind='run-start')` 或 `cost_report.py --mark`。",
              file=sys.stderr)
        s = s.get("fallback") or s
    if a.line:
        _w(s.get("md_line") or s.get("note") or "")
        return
    _w(json.dumps(s, ensure_ascii=False, indent=2))


def cmd_setup(a) -> None:
    ok = True
    _w("== 依赖 ==")
    try:
        import PIL  # noqa: F401
        _w("   Pillow         OK（评论区联系表可用）")
    except ImportError:
        ok = False
        _w("   Pillow         缺失 → pip install pillow（只影响 get_comment_images）")
    _w(f"   Python         {sys.version.split()[0]}")
    _w("\n== 接口 ==")
    from .http import wbi_fetch
    cookie = load_cookie()
    _w(f"   凭据           {'已配置' if cookie else '未配置（元数据/评论/弹幕不需要；字幕需要）'}")
    st, d, err = wbi_fetch("/x/web-interface/wbi/view", {"bvid": "BV1r2ey6EEh5"}, cookie)
    if st == 200 and d and d.get("code") == 0:
        _w(f"   WBI 签名       OK（{d['data']['title'][:30]}）")
    else:
        ok = False
        _w(f"   WBI 签名       FAIL：{err or (d or {}).get('message')}")
    from .search import search_keyword
    r = search_keyword("投资", 3, 1, cookie)
    if r.get("items"):
        _w(f"   搜索接口       OK（{r['n']} 条）")
    else:
        ok = False
        _w(f"   搜索接口       FAIL：{r.get('error')}")
    _w(f"\n   资产库目录     {registry.ROOT}")
    _w(f"   报告库目录     {reports.library_root()}")
    _w(f"\n{'== 全绿 ==' if ok else '== 有项目未通过，见上 =='}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="big", description="B站信息获取：取数与资产库")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="阶段 1 广扫（多关键词）")
    p.add_argument("keywords", nargs="+")
    p.add_argument("--per-query", type=int, default=30)
    p.add_argument("--max-candidates", type=int, default=40)
    p.add_argument("--budget", type=int, default=12, help="花评论预算的候选条数")
    p.add_argument("--comment-n", type=int, default=15, help="每条候选读多少条热评")
    p.add_argument("--image-policy", choices=["off", "hot", "all"], default="hot",
                   help="热评判图策略：hot=有图就判（默认）/ off=不判 / all=都尝试")
    p.add_argument("--exclude", nargs="*", default=None, help="排除的 BV 号")
    p.add_argument("--json", help="把完整结果写成 JSON")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("video", help="阶段 2 深挖单条视频")
    p.add_argument("video")
    p.add_argument("--comments", type=int, default=0)
    p.add_argument("--transcript-query", help="只取命中关键词的句子")
    p.add_argument("--head", type=float, help="只取字幕前 N 比例，如 0.2")
    p.add_argument("--max-lines", type=int, default=40)
    p.add_argument("--danmaku", action="store_true")
    p.add_argument("--images", action="store_true")
    p.add_argument("--top", type=int, default=4)
    p.set_defaults(func=cmd_video)

    p = sub.add_parser("registry", help="资产库")
    rsub = p.add_subparsers(dest="sub", required=True)
    rsub.add_parser("stats")
    rsub.add_parser("init")
    q = rsub.add_parser("sources"); q.add_argument("--domain")
    q = rsub.add_parser("videos"); q.add_argument("--topic")
    p.set_defaults(func=cmd_registry)

    p = sub.add_parser("report-path", help="算出报告库里的产出路径（日期+模式+议题）")
    p.add_argument("--topic", required=True, help="议题名（照录用户的说法）")
    p.add_argument("--mode", default="deep", help="deep（深入）或 brief（简要）")
    p.add_argument("--date", default=None, help="默认今天；可给 2026-10-22 或 20261022")
    p.add_argument("--mkdir", action="store_true", help="顺手把目录建出来")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_report_path)

    p = sub.add_parser("cost", help="调用账本汇总（默认全部；--window run 只看这一轮）")
    p.add_argument("--window", choices=["all", "run"], default="all")
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--line", action="store_true", help="只输出可贴进简报的那一行")
    p.set_defaults(func=cmd_cost)

    p = sub.add_parser("setup", help="自检")
    p.set_defaults(func=cmd_setup)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
