"""把议题报告 markdown 渲染成单文件 HTML，并（可选）打印成 PDF。

用法
----
    python scripts/render_briefing.py --md "<报告.md>" --pdf
    python scripts/render_briefing.py --md "<报告.md>" --check     # 只做结构自检
    python scripts/render_briefing.py --md "<报告.md>" --html "<指定.html>"

`--check` 的四个开关（存量基线，见文件末尾「存量基线」一节）
-----------------------------------------------------------
    --check                       对照报告库的 .check-baseline.json，**只报新增**（默认）
    --check --baseline-update     把当前提醒冻结为存量（单份生效）
    --check --no-baseline         忽略基线，全量报出（排查 / 盘点）
    --check --strict              新增提醒也返回 1（当交付闸门）

设计原则（与姊妹 skill `bilibili-video-analysis-summary` 一致）
--------------------------------------------------------------
1. **样式固定在 `assets/template/briefing.css`**，模型只写 md。
   ⇒ CSS 不进每次的输出 token（每篇省 4–5k），版式不会因重写而漂移。
2. **md 是唯一真源**：HTML 与 PDF 都由它机械生成，三者天然一致。
3. **不做「md 子集之外」的语法**：只用标题 / 段落 / 表格 / 列表 / 引用 / 代码块。
   需要强调的语义（评级、交叉验证、缺口类型）用**约定的纯文本**，脚本识别后上样式：
      评级      `A2` / `B2` / `C3`（字母 A–F + 数字 1–4，独立单元格）
      交叉验证  `✅`（多源独立）/ `◐`（部分印证）/ `⚠️`（单一来源）
      缺口类型  `结构性边界` / `检索未覆盖` / `本次未做`

为什么不用「让模型直接写 HTML」
------------------------------
试过姊妹 skill 的 body 片段法：模型写 HTML 更自由，但**每次结构都会漂**，
而且 HTML 标签本身要占 token。议题报告的表结构是固定的（信源地图 8–9 列、
冲突 3 列、缺口 4 列），用 md 表格 + 后处理更稳，也更容易人工改。

2026-10-22 的三处改动（都是"读者看不懂/找不到"引出来的）
---------------------------------------------------------
* **章节顺序改为「结论→发现→冲突→缺口与建议→检索范围→信源地图」**。
  原先把信源地图放在 §二，读者读完第一屏就是一张 140 行的候选表，
  真正要看的判断被推到后面。信源地图现在当附录用（同时吸收原「可追溯清单」）。
* **自动生成读图口径（legend）**。评级 `B2` 以前没有任何说明，
  读者只能猜。现在渲染时插入一块口径说明，并给每个徽章加 `title` 悬停提示。
  **口径由脚本生成，不占 md 的 token，也不会各篇不一致。**
* **kicker 按模式区分**：深入分析 → 「B 站议题深入报告」，
  简要 → 「B 站议题情报简报」。两种模式的耗时与样本量差一个量级，
  产物却叫同一个名字，读者无法从标题判断能不能当依据。
  **但模式只留在 kicker 与文件名里**：一级标题只写议题名（2026-10-29）。
  标题里再带一遍后缀，既重复又把标题撑长——长议题在 40px 下换行，
  右端离正文右边界还差一大截，读者会以为"标题比内容窄、内容提前换行"。

2026-10-29 的两处改动（都是"标题与正文宽度对不齐"引出来的）
---------------------------------------------------------
* **标题去后缀**：`doc_title()` 不再拼 `· B站信息深入报告`，见上。
* **字号自适应 + 弃用 `text-wrap:balance`**：`h1_size()` 按标题显示宽度选字号，
  让 22 字的长议题也能一行放下（34–36px）；同时把 `balance` 换成 `pretty`——
  `balance` 为了让两行等长会把第一行提前截断，实测两行只占 591/595px（容器 780），
  右边空 190px，与正文完全对不齐。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
from typing import NamedTuple

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.normpath(os.path.join(HERE, "..", "assets", "template"))
SHELL = os.path.join(TPL, "shell.html")
CSS = os.path.join(TPL, "briefing.css")

GRADE_IN_TD = re.compile(
    r"<td([^>]*)>\s*(?:<(?:strong|b)>\s*)?([A-F])([1-4])(?:\s*</(?:strong|b)>)?\s*</td>")
# 日期与时长列不能断行：`2026-05` 会在连字符处被拆成两行（实测）。
DATE_TD = re.compile(r"<td>(\d{4}-\d{2}(?:-\d{2})?)</td>")
TIME_TD = re.compile(r"<td>(\d{1,2}:\d{2}(?::\d{2})?)</td>")
GAP_WORDS = (
    ("结构性边界", "gap-struct"),
    ("检索未覆盖", "gap-search"),
    ("本次未做", "gap-todo"),
)
CV_WORDS = (("✅", "cv-strong"), ("◐", "cv-mid"), ("⚠️", "cv-weak"), ("⚠", "cv-weak"))
# 交叉验证用**一次正则替换**，不用四轮 str.replace。
# 为什么：`⚠️` 是 `⚠` + U+FE0F，两轮替换会把同一个符号数两遍
# （第一轮把它包成 span，第二轮又在 span 里数到一次）⇒
# 「证据强度分布」里的"单一来源"永远是真实值的两倍，而 HTML 看起来完全正常。
# 这个数字会进报告的概览条，属于"看起来对、其实错"的那类问题，必须靠计数断言兜住。
CV_RE = re.compile("|".join(re.escape(w) for w, _ in CV_WORDS))
CV_CLASS = {w: c for w, c in CV_WORDS}

# ---- 读图口径（legend 与徽章 title 共用同一份定义，避免两处各写一套） ----
SRC_MEANING = {
    "A": "一手/权威", "B": "专业二手", "C": "一般二手",
    "D": "观点型", "E": "未列入常规档", "F": "无法判断",
}
USAB_MEANING = {"1": "直接可用", "2": "可参考", "3": "仅背景", "4": "无关/标题党"}
SRC_NOTE = "来源可靠性：这个信源值不值得信（与内容质量无关）"
USAB_NOTE = "可用度：对本议题有没有用（永远相对于本议题，不是质量评分）"

# ---- 章节顺序：结构自检与渲染提示共用 ----
REQUIRED_SECTIONS = (
    ("## 一、结论", "结论"),
    ("## 二、发现", "发现"),
    ("## 三、冲突", "冲突与未收敛处"),
    ("## 四、缺口", "缺口与建议"),
    ("## 五、检索范围", "检索范围与偏差声明"),
    ("## 六、信源地图", "信源地图"),
)
LEGACY_MARKERS = (
    ("## 二、信源地图", "旧版编排：信源地图应在 §六（当附录用）"),
    ("### 二·D", "旧版编排：阵营表应作为 §二「发现」的子节"),
    ("## 七、可追溯清单", "旧版编排：可追溯清单已并入 §六「信源地图」"),
)
# 旧版七节编号的**指纹**。用组合计数而不是单条标记判"这是旧版"：
# 单条标记会让"新报告写错顺序"也被当成旧版放过——那正是重启自检要抓的错。
LEGACY_FINGERPRINT = ("## 二、信源地图", "## 三、发现", "## 四、冲突",
                      "## 五、缺口", "## 六、检索范围", "## 七、可追溯清单")
LEGACY_MIN_HITS = 4


# ------------------------------------------------------------------ md → 报头

def _mode_of(md_path: str | None, title: str = "") -> str:
    """这份报告是深入还是简要：**目录 → 文件名 → 标题**，逐级回退。

    2026-10-29 起标题不再带「· B站信息深入报告」后缀（报头 kicker 已经标了模式），
    所以判断顺序必须先从**落盘位置**取——目录名里的 `-深入分析-`、文件名里的
    `·B站信息深入报告.md`——标题只作最后兜底（人工临时文件、旧报告）。
    """
    if md_path:
        try:
            sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "src")))
            from big import reports
            info = reports.parse_folder(os.path.dirname(os.path.abspath(md_path)))
            if info:
                return info["mode"]
        except Exception:                                     # noqa: BLE001
            pass
        stem = os.path.basename(md_path)
        if "简报" in stem:
            return "brief"
        if "深入报告" in stem or "深入分析" in stem:
            return "deep"
    return "brief" if "简报" in title else "deep"


def kicker_for(title: str, md_path: str | None = None) -> str:
    """报头左上角那行小字。深入模式的产物不能自称「简报」。"""
    return "B 站议题情报简报" if _mode_of(md_path, title) == "brief" else "B 站议题深入报告"


def h1_size(title: str) -> int:
    """按标题的**显示宽度**选字号，目标是让标题**一行放下**（正文宽 780px）。

    为什么由脚本算而不是写死 40px：议题名长度差得很远（「社保催缴争议」6 字、
    「电脑三大件价格行情的大众态度及近六个月变化」22 字）。写死 40px 时长标题必然
    换行，而换行后的两行右侧留白远大于正文 ⇒ 读者看到"标题比内容窄"。

    780 / (字宽 × 0.985) 是这一行能放多少字（0.985 补 `letter-spacing:-.015em`）。
    目标宽度取 740 而不是 780：留 40px 余量，避免标题顶到右边界显得贴边。
    下限 26px——再小就不像报头了；真超出就让它两行（`text-wrap:pretty` 避免孤字）。
    """
    w = sum(0.55 if ord(c) < 128 else 1.0 for c in title) or 1.0
    return int(min(40, max(26, round(740 / (w * 0.985)))))


def split_masthead(md_text: str, md_path: str | None = None) -> tuple[str, str]:
    """把开头的 `# 标题` + 若干 `> 元信息` 摘出来，做成报头；其余留给 markdown。

    返回 (head_html, rest_md)。识别不到就返回 ("", 原文)——**不要因为格式不合规就报错**，
    人工写的报告可能没有那一块。
    """
    lines = md_text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    title = None
    if i < len(lines) and lines[i].lstrip().startswith("# "):
        title = lines[i].lstrip()[2:].strip()
        i += 1
    if not title:
        return "", md_text

    meta_rows: list[str] = []
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith(">"):
            meta_rows.append(s.lstrip("> ").strip())
            i += 1
            continue
        if s in ("", "---"):
            i += 1
            continue
        break

    spans = []
    for row in meta_rows:
        row = re.sub(r"\*\*(.+?)\*\*", r"\1", row)          # 去掉粗体标记
        if not row:
            continue
        parts = re.split(r"[：:]", row, maxsplit=1)
        if len(parts) == 2 and len(parts[0]) <= 16:
            spans.append(f"<span>{_esc(parts[0])} <b>{_esc(parts[1])}</b></span>")
        else:
            spans.append(f"<span>{_esc(row)}</span>")

    head = ['<header class="masthead">',
            f'<p class="kicker">{_esc(kicker_for(title, md_path))}</p>',
            f'<h1 style="--h1:{h1_size(title)}px">{_esc(title)}</h1>']
    if spans:
        head.append('<p class="meta">' + "".join(spans) + "</p>")
    head.append("</header>")
    return "\n".join(head), "\n".join(lines[i:])


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ------------------------------------------------------------------ 读图口径

def build_legend() -> str:
    """读图口径块：评级 / 证据强度 / 缺口类型各一行。

    为什么由脚本生成而不是写进 md：
    ① md 里写一遍，HTML 里还要再写一遍，两处必然漂；
    ② 每篇都重写这份口径是纯 token 浪费（约 200 字/篇），而内容永远一样；
    ③ 这是**版式的一部分**（和 CSS 同性质），不是这篇报告的观点。
    """
    def kv(code: str, text: str, cls: str = "grade") -> str:
        return f'<span class="lg-item"><span class="{cls}">{code}</span>{_esc(text)}</span>'

    src = "".join(kv(k, v) for k, v in SRC_MEANING.items())
    usab = "".join(
        f'<span class="lg-item"><b class="lg-num">{k}</b>{_esc(v)}</span>'
        for k, v in USAB_MEANING.items())
    cv = ('<span class="lg-item"><span class="cv cv-strong">✅</span>多源独立</span>'
          '<span class="lg-item"><span class="cv cv-mid">◐</span>部分印证</span>'
          '<span class="lg-item"><span class="cv cv-weak">⚠️</span>单一来源</span>')
    gap = ('<span class="lg-item"><span class="gap gap-struct">结构性边界</span>平台不承载</span>'
           '<span class="lg-item"><span class="gap gap-search">检索未覆盖</span>可再搜</span>'
           '<span class="lg-item"><span class="gap gap-todo">本次未做</span>可直接补</span>')
    # 五条压缩成三行 + 一句读法：报头下方的说明块每多一行，
    # 「§一 结论」就往下掉一行——而结论才是这份报告最该先被看到的东西。
    return (
        '<div class="legend"><dl>'
        '<div class="lg-row"><dt>来源 A–F</dt>'
        f'<dd><p class="lg-note">{_esc(SRC_NOTE)}</p>{src}</dd></div>'
        '<div class="lg-row"><dt>可用度 1–4</dt>'
        f'<dd><p class="lg-note">{_esc(USAB_NOTE)}</p>{usab}</dd></div>'
        f'<div class="lg-row"><dt>证据 / 缺口</dt><dd>{cv}<span class="lg-sep">｜</span>{gap}</dd></div>'
        '</dl>'
        '<p class="lg-hint">评级读作「<b>来源字母</b> × <b>可用度数字</b>」：'
        '<span class="grade g-B">B2</span> ＝ 专业二手 + 可参考。'
        '字母管「信不信得过这个来源」，数字管「对本议题有多少用」，两者不一定同向'
        '（大 UP 跨领域发言就是 A4，无名用户贴出原始统计可以是 F1）。</p>'
        '</div>')


# ------------------------------------------------------------------ 后处理

# 宽表（≥7 列）的列宽表。为什么必须给：浏览器自动布局会把宽度优先给
# **最长且不可断**的单元格（BV 号、链接），于是「标题」列被压到 12 字宽、
# 断成三四行——而标题恰恰是信源地图里最该读的一列（2026-10-29 实测截图）。
# 值为 None 的列吃掉剩余宽度（通常是「标题」）。
COL_WIDTH = {
    "#": 24, "BV": 86, "标题": None, "UP 主": 70, "评级": 40,
    "冗余度": 52, "时长": 44, "发布": 54, "一句话定性": 170,
    "播放/赞/藏/评": 96, "播放": 64, "总评论": 58,
}

# 「标题」列的最低宽度。9 列时它自然能拿到 240；10 列（信源地图多一列
# 「播放/赞/藏/评」）时只剩 72px，比 UP 主列还窄——而它是这张表里唯一
# 需要读内容的列。所以固定列要让位（见 _colgroup）。
TITLE_MIN = 200


# 「关键列」：只有认出其中一列，才说明这是「信源地图型」的表，可以按名分配列宽。
# 判据不能是「认出任意一列」——中土那张「视频 / 总评论 / 围观·玩梗 / …」只能认出
# 「总评论」，照正常分支走的话第 1 列（视频）会变成 auto、和数值列一样窄。
KEY_HEADERS = {"#", "BV", "标题"}


def _colgroup(headers: list[str], total: int = 780) -> str:
    """按表头名生成 <colgroup>。**总会返回非空**——因为调用方给表加了
    `table-layout:fixed`，没有 colgroup 的 fixed 表会变成"所有列等宽"，
    对「标题 / 数值」混排的表比自动布局还糟（2026-10-29 实测：中土那张
    「视频 / 总评论 / 围观·玩梗 / 支持对线 / …」被 7 等分成 111px）。
    """
    widths = [COL_WIDTH.get(h) for h in headers]
    n = max(len(headers), 1)
    if not (set(headers) & KEY_HEADERS):
        # 兜底：列名全不认识（例：评论簇矩阵）。这类表的第 1 列是主体
        # （视频标题或 BV），必须比其余列宽；其余是数值/短标签，均分即可。
        first = min(200, max(120, round(total * 0.24)))
        rest = max(60, round((total * 0.96 - first) / max(n - 1, 1)))
        return ("<colgroup>"
                + f'<col style="width:{first / total * 100:.2f}%">'
                + f'<col style="width:{rest / total * 100:.2f}%">' * (n - 1)
                + "</colgroup>")
    fixed = sum(w for w in widths if w)
    auto_n = sum(1 for w in widths if not w)
    scale = 1.0
    if auto_n and "标题" in headers and (total - fixed) / auto_n < TITLE_MIN:
        # 列太多，剩余宽度不够分 → 固定列等比让位，先保证标题能读
        scale = (total - TITLE_MIN) / fixed if fixed else 1.0
    # **用百分比不用 px**：屏幕容器 780px、A4 内容宽 673px，写死 px 的话
    # 打印时固定列一个不缩、全让标题列承担（实测标题从 240 被压到 133）。
    cols = "".join("<col>" if not w else
                   f'<col style="width:{w * scale / total * 100:.2f}%">'
                   for w in widths)
    return f"<colgroup>{cols}</colgroup>"


def style_tables(html: str) -> str:
    """给表格包一层 .tw；列数多的加 .wide 并按表头注入 colgroup。

    另外给「冲突与未收敛处」章节内的表格加 `.clash`——那一节的表是**对照**语义
    （A 方 / B 方 / 我的处理），普通斑马纹表格读起来会漏掉"两边在打架"这件事。
    """
    def repl(m: re.Match) -> str:
        table = m.group(0)
        headers = [re.sub(r"<[^>]+>", "", h).strip()
                   for h in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)]
        cls = ' class="wide"' if len(headers) >= 7 else ""
        inner = re.sub(r"^<table>", f"<table{cls}>", table)
        if len(headers) >= 7:
            cg = _colgroup(headers)
            if cg:
                inner = inner.replace("<thead>", cg + "<thead>", 1)
        return f'<div class="tw">{inner}</div>'
    html = re.sub(r"<table>.*?</table>", repl, html, flags=re.S)

    # 按 h2 切块，只给「冲突」那一节的 .tw 加类
    parts = re.split(r"(<h2[^>]*>.*?</h2>)", html, flags=re.S)
    out, in_clash = [], False
    for p in parts:
        if p.startswith("<h2"):
            in_clash = "冲突" in p
            out.append(p)
        elif in_clash:
            out.append(p.replace('<div class="tw">', '<div class="tw clash">'))
        else:
            out.append(p)
    return "".join(out)


def link_bv(html: str) -> str:
    """把正文里的**裸 BV 号**变成可点链接。

    为什么放在渲染层而不是要求 md 里一律写 `[BVxxx](url)`：
    ① 链接语法一个就要 45 个字符，信源地图一张表 40 行 = 1,800 字符的纯噪声；
    ② 手写必然漏（2026-10-26「二游超人气角色（终版）」整份 0 个链接，
       因为作者当时用的是纯文本 BV，读者只能复制粘贴）。
    渲染时统一补，md 保持干净。

    做法是**先把已有的 `<a>` 抠出来占位**再替换：否则会把
    `[BV1xxx @03:12](https://…?t=192)` 转成的 `<a>` 再包一层 `<a>`，
    产生嵌套锚点（浏览器渲染会错位）。
    """
    stash: list[str] = []

    def keep(m: re.Match) -> str:
        stash.append(m.group(0))
        return f"\x00{len(stash) - 1}\x00"

    html = re.sub(r"<a\b[^>]*>.*?</a>", keep, html, flags=re.S)
    html = re.sub(r"(?<![0-9A-Za-z])(BV[0-9A-Za-z]{10})(?![0-9A-Za-z])",
                  r'<a href="https://www.bilibili.com/video/\1/">\1</a>', html)
    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], html)


def apply_badges(html: str) -> tuple[str, dict]:
    """识别约定文本 → 上样式，并统计分布（给概览条用）。

    徽章同时带 `title`：`B2` → 「来源 B · 专业二手｜可用度 2 · 可参考」。
    这一条是给"读者不知道 B2 是什么"打的补丁——**不占版面**，
    鼠标停在徽章上就有解释；PDF 里看不到悬停，所以顶部还有一块 legend。
    """
    grades: dict[str, int] = {}
    cvs = {"cv-strong": 0, "cv-mid": 0, "cv-weak": 0}

    # 日期/时长整格 nowrap：`2026-05` 会在连字符处断成两行（实测）
    html = DATE_TD.sub(r'<td class="n">\1</td>', html)
    html = TIME_TD.sub(r'<td class="n">\1</td>', html)

    def g_repl(m: re.Match) -> str:
        attrs, letter, num = m.group(1), m.group(2), m.group(3)
        grades[letter] = grades.get(letter, 0) + 1
        tip = (f"来源 {letter} · {SRC_MEANING.get(letter, '?')}"
               f"｜可用度 {num} · {USAB_MEANING.get(num, '?')}")
        return (f'<td{attrs}><span class="grade g-{letter}" title="{tip}">'
                f"{letter}{num}</span></td>")
    html = GRADE_IN_TD.sub(g_repl, html)

    def cv_repl(m: re.Match) -> str:
        sym = m.group(0)
        cls = CV_CLASS[sym]
        cvs[cls] += 1
        return f'<span class="cv {cls}">{sym}</span>'
    html = CV_RE.sub(cv_repl, html)

    for word, cls in GAP_WORDS:
        html = html.replace(word, f'<span class="gap {cls}">{word}</span>')

    return html, {"grades": grades, "cvs": cvs}


def build_overview(stats: dict) -> str:
    """证据概览：评级分布 + 交叉验证分布。没有数据就整块不出现。"""
    grades, cvs = stats["grades"], stats["cvs"]
    if not grades and not any(cvs.values()):
        return ""
    cells = []

    total_g = sum(grades.values())
    if total_g:
        rows = []
        for letter in "ABCDEF":
            n = grades.get(letter, 0)
            if not n:
                continue
            pct = max(3, round(n / total_g * 100))
            rows.append(
                f'<div class="ov-row"><span class="ov-key">'
                f'<span class="grade g-{letter}">{letter}</span></span>'
                f'<span class="ov-bar"><i class="lv-{letter.lower()}" '
                f'style="width:{pct}%"></i></span>'
                f'<span class="ov-n">{n}</span></div>')
        cells.append('<div class="ov-cell"><dt>'
                     + ("信源评级分布" if stats.get("grades_basis") else "评级出现次数")
                     + (f'<span class="ov-sub">（{stats["grades_basis"]}）</span>'
                        if stats.get("grades_basis") else
                        '<span class="ov-sub">（无信源地图，按徽章出现次数）</span>')
                     + '</dt><dd>'
                     + "".join(rows) + "</dd></div>")

    total_c = sum(cvs.values())
    if total_c:
        label = {"cv-strong": "多源独立", "cv-mid": "部分印证", "cv-weak": "单一来源"}
        rows = []
        for cls in ("cv-strong", "cv-mid", "cv-weak"):
            n = cvs.get(cls, 0)
            if not n:
                continue
            pct = max(3, round(n / total_c * 100))
            shade = {"cv-strong": "lv-a", "cv-mid": "lv-b", "cv-weak": "lv-c"}[cls]
            rows.append(
                f'<div class="ov-row"><span class="ov-key">{label[cls][:2]}</span>'
                f'<span class="ov-bar"><i class="{shade}" style="width:{pct}%"></i></span>'
                f'<span class="ov-n">{n}</span></div>')
        cells.append('<div class="ov-cell"><dt>证据强度分布</dt><dd>'
                     + "".join(rows) + "</dd></div>")

    return '<div class="overview">' + "".join(cells) + "</div>"


# ------------------------------------------------------------------ 渲染

def _insert_before_second_h2(block: str, body: str) -> str:
    """把读图口径 / 证据概览插到**第二个 `<h2>` 之前**（= §一 结论之后、§二 之前）。

    2026-10-29 之前它插在报头正下方，实测把「一、结论」推到 y≈920——
    读者打开报告的第一屏全是「B2 是什么意思」，看不到任何判断。
    但口径又不能挪到文末：它必须在**第一次出现评级之前**，而第一次出现是 §二 的表。
    §一 结论不需要评级就能读，所以插在两者之间是唯一同时满足两头的位点。

    ⚠️ 是**第二个**不是第一个：第一个 h2 就是「一、结论」，插在它前面等于没挪。
    只有一个 h2 的畸形文件就放到末尾，至少不挡结论。
    """
    marks = list(re.finditer(r"<h2[^>]*>", body))
    if not marks:
        return block + body
    if len(marks) == 1:
        return body + block
    pos = marks[1].start()
    return body[:pos] + block + body[pos:]


def render(md_path: str, html_path: str | None = None, title: str | None = None) -> str:
    import markdown

    for p in (SHELL, CSS):
        if not os.path.exists(p):
            raise SystemExit(f"[FAIL] 模板缺失：{p}")

    md_text = open(md_path, encoding="utf-8").read()
    head, rest = split_masthead(md_text, md_path)

    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
    body = md.convert(rest)
    body = style_tables(body)
    body = link_bv(body)                    # 裸 BV → 链接（md 里不必写一长串 url）
    body, stats = apply_badges(body)

    # 评级分布按**信源地图里的唯一 BV** 算，不按徽章出现次数算。
    # 实测：简要轮 25 条唯一信源，徽章出现 43 次（正文引用行也带评级，
    # 而且同一条视频的「评论」与「本体」可以是两个等级）——按次数画图会把
    # 8/14/15/6 当成"有 43 条信源"，比真实值多 72%。
    uniq = _ratings_by_bv(md_text)
    if uniq:
        counts: dict[str, int] = {}
        for g in uniq.values():
            counts[g[0]] = counts.get(g[0], 0) + 1
        stats["grades"] = counts
        stats["grades_basis"] = f"信源地图去重后 {len(uniq)} 条"
    overview = build_overview(stats)
    # legend 必须**在 apply_badges 之后**拼进去：否则它自己的 ✅/⚠️ 会被
    # 统计算进「证据强度分布」，概览条就会多出一截无中生有的计数。
    # 位置：§一 之后、§二 之前（见 _insert_before_second_h2 的注释）。
    block = _insert_before_second_h2(build_legend() + overview, body)
    body = f'{head}\n<div class="body">{block}</div>'
    body += ('<p class="foot">本报告由 bilibili-info-gathering 生成；'
             '所有结论均可回溯到 BV 号与时间戳。评级与交叉验证状态由分析者判定，'
             '不代表平台立场。</p>')

    shell = open(SHELL, encoding="utf-8").read()
    css = open(CSS, encoding="utf-8").read().rstrip("\n")
    if title is None:
        m = re.search(r"<h1>(.*?)</h1>", head or body, re.S)
        title = re.sub(r"<[^>]+>", "", m.group(1)) if m else os.path.basename(md_path)
    html = (shell.replace("{{CSS}}", css)
                 .replace("{{TITLE}}", _esc(title))
                 .replace("{{BODY}}", body))

    if html_path is None:
        html_path = os.path.splitext(md_path)[0] + ".html"
    os.makedirs(os.path.dirname(os.path.abspath(html_path)) or ".", exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    return html_path


def to_pdf(html_path: str, pdf_path: str | None = None) -> str | None:
    """用 Playwright 的 Chromium 打印 PDF。纸张与边距由 CSS 的 @page 决定。

    没有 playwright / 浏览器时**返回 None 而不是抛异常**——报告已经写好了，
    缺 PDF 是可降级的，不该让整条交付链失败。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[WARN] 未安装 playwright，跳过 PDF。"
              "安装：pip install playwright && playwright install chromium-headless-shell",
              file=sys.stderr)
        return None
    import time
    html_abs = os.path.abspath(html_path)
    if pdf_path is None:
        pdf_path = os.path.splitext(html_abs)[0] + ".pdf"
    t0 = time.time()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            pg.goto("file:///" + html_abs.replace("\\", "/"), wait_until="networkidle")
            pg.emulate_media(media="print")
            pg.pdf(path=pdf_path, format="A4", print_background=True,
                   prefer_css_page_size=True)
            b.close()
    except Exception as e:                                    # noqa: BLE001
        print(f"[WARN] PDF 渲染失败（HTML 已生成）：{type(e).__name__}: {e}", file=sys.stderr)
        return None
    print(f"[OK] PDF {pdf_path}  {time.time() - t0:.1f}s  "
          f"{os.path.getsize(pdf_path) / 1024:.0f} KB")
    return pdf_path


# ------------------------------------------------------------------ 结构自检

# ---- 内容体检（2026-10-29）----
# 这几条查的不是"结构齐不齐"，而是"读起来像不像报告"：
# 结论是否变成了流水账、单元格是否当段落用、编号能不能对上、称谓是否中性。
# 全部是**软提醒**——它们衡量的是判断与写作，不是可机械判定的事实。
LINK_MD = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _plain(s: str) -> str:
    """去掉 markdown 链接与强调标记，只留可见文字（统计字数用）。

    `**` 与反引号必须去掉：一段 200 字的判断里有 8 处粗体就是 32 个字符，
    按原文数字数会让"结论是不是太长"这个问题虚高 5%–8%。
    """
    s = LINK_MD.sub(r"\1", s)
    return re.sub(r"[*`]", "", s)


def _section_text(text: str, prefix: str) -> str:
    """取 `## x` 到下一个 `## ` 之间的原文；找不到返回空串。"""
    i = text.find(prefix)
    if i < 0:
        return ""
    j = text.find("\n## ", i + len(prefix))
    return text[i:j] if j > 0 else text[i:]


def _is_evidence_list(cell: str) -> bool:
    """BV 号成串出现的单元格 = **证据清单**（「这些是相关信源」），长是应该的。

    2026-10-29 实测：8 个 >150 字的单元格里有 6 个是 BV/时间戳/赞数的罗列
    （如「BV1kYYF6xE6r, BV1ZEp96uE83, BV1icay6AEV2…」），把它们当"段落"报出来
    是误报——真正该移出表格的只有解释性长段。判据用"≥2 个 BV"，
    因为单条 BV + 长解释仍然是段落。
    """
    return len(re.findall(r"BV[0-9A-Za-z]{10}", cell)) >= 2


def _long_cells(text: str, limit: int = 80) -> list[tuple[str, int]]:
    """表格单元格里塞长段落 → 在 780px 的表里会变成又窄又高的柱子。"""
    out: list[tuple[str, int]] = []
    for line in text.splitlines():
        if not line.startswith("|") or re.match(r"^\|[\s:|-]+\|$", line):
            continue
        for cell in line.strip().strip("|").split("|"):
            if _is_evidence_list(cell):
                continue
            plain = _plain(cell.strip()).strip("*").strip()
            if len(plain) > limit:
                out.append((plain[:26], len(plain)))
    return out


def _source_map_numbers(text: str) -> tuple[list[int], list[int]]:
    """§六 里 `| N |` 的编号：返回 (重复号, 缺失号)。

    只取 §六 之后的表格，且只认**行首第一格**——正文里的 `| 2026 |` 之类不算。
    """
    i = text.find("## 六、信源地图")
    if i < 0:
        return [], []
    nums = [int(m.group(1)) for m in re.finditer(r"^\|\s*(\d+)\s*\|", text[i:], re.M)]
    if not nums:
        return [], []
    dup = sorted({n for n in nums if nums.count(n) > 1})
    miss = [n for n in range(min(nums), max(nums) + 1) if n not in nums]
    return dup, miss

def _section_pos(text: str, prefix: str) -> int:
    i = text.find(prefix)
    return i


def _report_dir_check(md_path: str, text: str) -> tuple[list[str], list[str]]:
    """文件名 / 目录 / 标题三者的模式是否一致。

    2026-10-29 起分工变了：**标题只写议题名**（模式由报头 kicker 表达），
    模式靠**目录名与文件名**承载。所以这里查两件事：
    ① 文件名里的后缀（深入报告 / 简报）与目录模式一致——**硬错**，
       不一致会让读者按错误证据强度理解这份报告；
    ② 标题里**不该**再出现「B站信息深入报告」——**软提醒**，
       旧报告还有，不必判死，但新报告的写法不对。

    只在文件确实落在报告库目录里时才检查——检查临时文件、旧路径文件时静默跳过，
    否则 `--check` 会对任何目录下的 md 报一堆无关的错。
    """
    problems: list[str] = []
    warnings: list[Warn] = []
    sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "src")))
    try:
        from big import reports
    except Exception:                                         # noqa: BLE001
        return problems, warnings
    info = reports.locate(md_path)
    # 只要求**目录名符合** `<日期>-<模式>-<议题>`，不要求它一定在 library_root 之下：
    # `BIG_REPORT_DIR` 指向外部资产库时，工作区里的同名副本会被判成"不在库里"而
    # 静默跳过——2026-10-29 实测就是这条路让 22 份报告的模式校验一直没跑。
    if not info:
        return problems, warnings
    stem = os.path.basename(md_path)
    want = "深入报告" if info["mode"] == "deep" else "简报"
    if want not in stem:
        problems.append(
            f"文件名与所在目录的模式不符：目录是「{info['mode_label']}」，"
            f"文件名里应含「{want}」，实际是「{stem}」")
    title = text.lstrip().splitlines()[0].lstrip("# ").strip() if text.strip() else ""
    if "B站信息深入报告" in title or "B站信息简报" in title:
        warnings.append(Warn(
            "dir.title_suffix",
            "标题不必再带「· B站信息深入报告」——报头左上角的 kicker 已经标了模式；"
            "标题只写议题名（2026-10-29 起）"))
    return problems, warnings


def _ratings_by_bv(text: str) -> dict[str, str]:
    """从**信源地图**里抽 `BV → 评级`。

    只取 §六 之后的表格：正文引用行里的评级是**那一条证据的来源**评级
    （同一条视频的评论区与视频本体可以是 B2 / D2 两个等级），
    拿正文首行去跨报告比对，会产生大量假冲突（2026-10-22 实测过）。
    信源地图才是"这条视频整体怎么评"的唯一权威位置。
    """
    i = text.find("## 六、信源地图")
    if i < 0:
        return {}
    body = text[i:]
    out: dict[str, str] = {}
    for line in body.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip().strip("*") for c in line.strip("|").split("|")]
        bv = re.search(r"(BV[0-9A-Za-z]{8,})", line)
        grade = next((c for c in cells if re.fullmatch(r"[A-F][1-4]", c)), None)
        if bv and grade:
            out.setdefault(bv.group(1), grade)
    return out


def _library_rating_conflicts(md_path: str, text: str) -> list[tuple[tuple[str, ...], str]]:
    """同一 BV 在报告库里的其他报告中评级不同 → 提醒。

    ⚠️ 这是**提醒**不是错误：同一 BV 在不同议题里的「可用度」本来就该不同
    （可用度是相对议题的）。但**来源字母**（A–F）不该因为议题或模式而变。
    所以只在字母不同时措辞更强，数字不同时只提一句。

    返回 `[(涉及的 BV 元组, 提示语)]`。BV 元组是**基线用的身份**——
    只记"有几条冲突"的话，"修好 2 条又新增 2 条"会被判成没变化。
    """
    try:
        sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "src")))
        from big import reports
        info = reports.locate(md_path)
        if not info or not info.get("in_library"):
            return []
        root = pathlib.Path(reports.library_root())
        mine = _ratings_by_bv(text)
        if not mine:
            return []
        out: list[tuple[tuple[str, ...], str]] = []
        for other in sorted(root.glob("*/*.md")):
            if os.path.normcase(str(other)) == os.path.normcase(str(md_path)):
                continue
            theirs = _ratings_by_bv(other.read_text(encoding="utf-8", errors="replace"))
            diff = {bv: (mine[bv], theirs[bv]) for bv in mine
                    if bv in theirs and theirs[bv] != mine[bv]}
            if not diff:
                continue
            letter = [f"{bv}（{a} vs {b}）" for bv, (a, b) in diff.items()
                      if a[0] != b[0]]
            if not letter:
                continue
            other_info = reports.parse_folder(other.parent) or {}
            same_topic = other_info.get("topic") == info.get("topic")
            tail = ("。**同一议题内来源字母不应随模式变化**（见 "
                    "references/source-grading.md 第九节）" if same_topic else
                    "。跨议题时字母可能合理（同一条视频在不同议题里角色不同），"
                    "若确认属同一议题的判断请校准")
            out.append((
                tuple(bv for bv, (a, b) in diff.items() if a[0] != b[0]),
                f"与《{other.parent.name}》对同一 BV 的来源评级不一致："
                + "、".join(letter[:4]) + ("…" if len(letter) > 4 else "") + tail))
        return out
    except Exception:                                         # noqa: BLE001
        return []


# ---------------------------------------------------------------- 存量基线
#
# 为什么需要基线（2026-10-29）
# ---------------------------
# 自检的判据是从存量报告里反推出来的，所以一上线就"全库皆红"：22 份报告
# 118 处长单元格、7 份编号跳号、5 份结论偏长……这些是**历史产物**，
# 按项目原则「存量报告不改写，只在新规范与新自检上收敛」，它们不该被判死。
# 但也不能一律降级成提示——那样真出现新问题时同样看不见。
#
# 所以把"此刻已有的软提醒"冻结成一份基线，之后 `--check` 只报**基线之外的**。
# 三条硬约束：
#   ① **硬错永不进基线**。[FAIL] 意味着这份报告交不了，必须修，
#      进基线等于把体检报告上的红字用涂改液盖掉。
#   ② **身份优先于计数**。评级冲突 / 编号跳号这类按"是哪一条"记账，
#      不按"有几条"——否则修好两条又坏两条会被判成"没变化"。
#   ③ **基线跟着报告库走**，不放在 skill 目录。报告搬到别的盘
#      （`BIG_REPORT_DIR`）时基线必须一起搬，否则换台机器就满屏新告警。

BASELINE_NAME = ".check-baseline.json"
BASELINE_VERSION = 1


class Warn(NamedTuple):
    """一条软提醒。

    `code` 是**稳定分类码**：基线按它聚合，所以它只描述"哪一类问题"，
    不带任何随内容变化的字（字数、BV 号、编号都放进 `msg`）——
    否则改一个错别字就会让整条基线失效，退化成"每次都是新问题"。

    `keys` 是**身份**：`("*",)` 表示这是"同类合并计数"的提醒（用 `n` 记账），
    其余表示"具体是哪几条"（如冲突的 BV 号、跳号的编号）。
    """

    code: str
    msg: str
    keys: tuple[str, ...] = ("*",)
    n: int = 1


def _baseline_file(md_path: str) -> pathlib.Path | None:
    """基线文件位置：报告目录的**上一级**（报告库根）。

    不在 `<日期>-<模式>-<议题>` 目录里的 md（草稿、测试夹具）没有基线——
    它们不是交付物，不该占用基线条目。
    """
    sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "src")))
    try:
        from big import reports
    except Exception:                                         # noqa: BLE001
        return None
    p = pathlib.Path(md_path).resolve()
    if not reports.parse_folder(p.parent):
        return None
    return p.parent.parent / BASELINE_NAME


def _rel_key(md_path: str, root: pathlib.Path) -> str:
    """基线里的键：相对报告库根的 posix 路径（Windows 反斜杠会让 JSON 难读）。"""
    try:
        return pathlib.Path(md_path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:                                        # pragma: no cover
        return pathlib.Path(md_path).name


def _read_baseline(path: pathlib.Path) -> dict | None:
    """读基线；不存在 / 损坏 / 版本不符都当作"没有基线"（宁可多报，不可漏报）。"""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if int(data.get("version") or 0) != BASELINE_VERSION:
        return None
    data.setdefault("reports", {})
    return data


def _write_baseline(path: pathlib.Path, md_path: str, warns: list[Warn]) -> None:
    """把当前提醒写进基线（只动这一份报告，其他报告的条目原样保留）。"""
    data = _read_baseline(path) or {
        "version": BASELINE_VERSION,
        "note": ("存量基线：冻结 2026-10-29 时报告库已有的软提醒，--check 只报此后"
                 "新增的。硬错（[FAIL]）永不进基线。刷新单份："
                 "--check --baseline-update"),
        "reports": {},
    }
    key = _rel_key(md_path, path.parent)
    idx = _index(warns)
    if idx:
        data["reports"][key] = idx
    else:
        data["reports"].pop(key, None)
    data["updated_at"] = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")


def _index(warns: list[Warn]) -> dict:
    """`[Warn]` → `{code: {"n": 累计数} | {"keys": [身份…]}}`。"""
    out: dict[str, dict] = {}
    for w in warns:
        slot = out.setdefault(w.code, {"n": 0, "keys": []})
        if w.keys == ("*",):
            slot["n"] += w.n
        else:
            for k in w.keys:
                if k not in slot["keys"]:
                    slot["keys"].append(k)
    # 计数型不留空 keys，身份型不留空 n——JSON 里少一半噪音
    for slot in out.values():
        if slot["keys"]:
            slot.pop("n", None)
        else:
            slot.pop("keys", None)
    return out


def _total(idx: dict) -> int:
    """基线索引里一共多少"处"（计数型取 n，身份型取 key 个数）。

    统一口径是为了让「存量 30 处已抑制」与「已记为存量 30 处」对得上——
    一个按提醒条数、一个按处数报，用户会以为少记了 27 处。
    """
    return sum(int(v.get("n") or 0) + len(v.get("keys") or []) for v in idx.values())


def _compare(warns: list[Warn], entry: dict | None) -> tuple[list[Warn], int, int]:
    """对照基线：返回 (要显示的提醒, 被抑制的处数, 存量里已修好的处数)。

    `entry is None` = 这份报告还没进基线（新报告，或基线文件不存在）→ 全部报。
    """
    if entry is None:
        return warns, 0, 0
    cur = _index(warns)
    shown: list[Warn] = []
    suppressed = 0
    for w in warns:
        b = entry.get(w.code) or {}
        if w.keys == ("*",):
            base_n = int(b.get("n") or 0)
            if base_n >= w.n:
                suppressed += w.n
            elif base_n == 0:
                shown.append(w)              # 整类是新的：不加「较存量」注解
            else:
                suppressed += base_n         # 重合的那部分仍算抑制
                shown.append(w._replace(msg=f"{w.msg}（较存量 +{w.n - base_n}）"))
        else:
            known = set(b.get("keys") or [])
            fresh = tuple(k for k in w.keys if k not in known)
            suppressed += len(w.keys) - len(fresh)
            if not fresh:
                pass
            elif len(fresh) == len(w.keys):
                shown.append(w)
            else:
                shown.append(w._replace(msg=f"{w.msg}（新增 {', '.join(fresh)}）"))
    resolved = 0
    for code, b in entry.items():
        c = cur.get(code) or {}
        resolved += max(0, int(b.get("n") or 0) - int(c.get("n") or 0))
        for k in b.get("keys") or []:
            if k not in (c.get("keys") or []):
                resolved += 1
    return shown, suppressed, resolved


def check(md_path: str, *, baseline: bool = True, update: bool = False,
          show_all: bool = False, strict: bool = False) -> int:
    """结构自检：只报问题与提醒，不写文件。交付前跑一次。

    **问题是硬错（返回 1），提醒是软错（返回 0）**。旧版编排的报告会得到
    一组提醒而不是报错——它们的内容没错，只是版式落后于 2026-10-22 的新标准。

    **存量基线（2026-10-29）**：默认读报告库根目录的 `.check-baseline.json`，
    只报基线之外的新提醒；`update=True` 把当前状态记为存量；`show_all=True`
    忽略基线报全部（排查用）；`strict=True` 时新增提醒也返回 1（当交付闸门用）。
    """
    text = open(md_path, encoding="utf-8").read()
    problems: list[str] = []
    warnings: list[Warn] = []

    if not text.lstrip().startswith("# "):
        problems.append("第一行不是 `# 标题`——报头会退化成普通段落")

    # ① 六节齐不齐
    found: list[tuple[int, str]] = []
    for prefix, label in REQUIRED_SECTIONS:
        i = _section_pos(text, prefix)
        if i < 0:
            problems.append(f"缺 `{prefix}`（{label}）——新版编排的六节之一")
        else:
            found.append((i, label))
    # ② 顺序对不对（这才是"重排"的验收点，缺节时不重复报错）
    if len(found) == len(REQUIRED_SECTIONS):
        order = [label for _, label in sorted(found)]
        want = [label for _, label in REQUIRED_SECTIONS]
        if order != want:
            problems.append("章节顺序不对：应为 " + " → ".join(want)
                            + "；实际是 " + " → ".join(order))

    # ③ 阵营表的位置（仅态度类才有；方法类没有就不查）
    if "阵营表" in text:
        i_camp = _section_pos(text, "阵营表")
        i_find = _section_pos(text, "## 二、发现")
        i_conf = _section_pos(text, "## 三、冲突")
        if i_find >= 0 and i_conf >= 0 and not (i_find < i_camp < i_conf):
            problems.append("「阵营表」必须在 §二「发现」之内（发现之后、冲突之前）"
                            "——它是对发现的归并，不是信源清单的一部分")

    # ④ 旧版编排的痕迹
    for marker, msg in LEGACY_MARKERS:
        if marker in text:
            warnings.append(Warn("legacy.marker", msg, keys=(marker,)))

    # ⑤ 视觉约定（缺了 HTML 会静默退化）
    if "✅" not in text and "⚠️" not in text and "◐" not in text:
        warnings.append(Warn("visual.markers",
                             "全文没有交叉验证标记（✅/◐/⚠️）——概览条会缺失"))
    if not re.search(r"<td[^>]*>\s*[A-F][1-4]\s*</td>", text) and \
       not re.search(r"\|\s*\**([A-F][1-4])\**\s*\|", text):
        warnings.append(Warn("visual.rating",
                             "没有找到评级（如 `A2`/`B2`）——概览条会缺失"))

    # ⑥ 深入模式必须交代这一轮花了多少（路线图 P1 的验收点）。
    # 判据不能再看标题后缀——2026-10-29 起标题只写议题名，模式看目录名 / 文件名。
    title = text.lstrip().splitlines()[0] if text.strip() else ""
    if _mode_of(md_path, title) == "deep" and "单轮开销" not in text:
        problems.append("§五 缺「单轮开销」一行——深入模式必须能回答"
                        "「这一轮花了多少」，取法：registry_upsert(kind='commit') 返回的"
                        " final_cost.md_line 原样贴进去")

    # ⑥b 上下文隔离：两种模式都要说「评论/字幕有没有进主上下文」。
    # 这不是格式洁癖——它是"为什么这一轮便宜"的唯一凭据；不声明，
    # 下次跑就会退回到把 700 条评论堆进上下文的老路。
    if "上下文隔离" not in text:
        warnings.append(Warn("ctx.isolation",
                             "§五 没有「上下文隔离」声明——请写明子代理读了几条、"
                             "主上下文收到的是摘要还是原文（见 references/context-isolation.md）"))

    # ⑥c 信源地图必须分小节：只有一张大表的话，读者无法区分
    # 「深挖过的」和「只是入选没读的」，两者的可信度差一个量级。
    if "## 六、信源地图" in text and "### 6.1" not in text:
        warnings.append(Warn("map.section",
                             "§六 没有 `### 6.1` 子小节——深挖对象与未深挖候选必须分开列"))

    # ⑥d 同一 BV 在别的报告里评级不同 → 评级缺少锚点的信号（2026-10-22 实测：
    # 同一议题两轮 14 条可比 BV 里 10 条不一致，其中 7 条来源字母都不同）
    for keys, msg in _library_rating_conflicts(md_path, text):
        warnings.append(Warn("rating.conflict", msg, keys=tuple(sorted(keys))))

    # ⑨ 结论变成了「本轮汇报」而不是「判断」。
    # 判据放宽是有实证的：2026-10-26「二游终版」结论 6 条编号判断共 1203 字、
    # 没有一句废话，按 1000 字判它是误报；2026-10-21「二游第二轮」896 字里
    # 「本轮发现…」是**判断的归属**（区分本轮与首轮），也不是过程汇报。
    # 真正该抓的只有中土那种：1722 字、「补做轮」×6、「首轮」×4，一半篇幅在讲
    # "这一轮补到了什么"。所以硬线定在 1600 字 / 过程词 ≥4 且 >1400。
    # 模板里写的**目标**仍是「3–5 条、≤1000 字」——目标是写给写的人看的，
    # 硬线是给自检用的，两者不该是同一个数。
    concl_raw = _section_text(text, "## 一、结论")
    n_concl = len(re.sub(r"\s", "", _plain(concl_raw)))
    process_hits = sum(concl_raw.count(w) for w in ("本轮", "补做轮", "首轮", "上一轮"))
    if n_concl > 1600 or (process_hits >= 4 and n_concl > 1400):
        warnings.append(Warn(
            "concl.process",
            f"§一 结论 {n_concl} 字，其中「本轮/首轮」出现 {process_hits} 次——"
            "这是**过程记录**不是判断。请把「补做轮补了什么 / 冲突从 N 增到 M」"
            "移到独立附录（如 `## 附：与上一轮的差异`），结论只留读者要拿走的判断"))

    # ⑩ 表格单元格当段落用（780px 的表格里，120 字的单元格会变成一根窄柱子）
    long_cells = _long_cells(text)
    if long_cells:
        sample = "、".join(f"「{s}…」{n} 字" for s, n in long_cells[:3])
        warnings.append(Warn(
            "cell.long",
            f"{len(long_cells)} 个表格单元格超过 80 字（{sample}）——"
            "单元格是格子不是段落：长解释移到表下「必要的解读」，"
            "表格只留可核对的事实与数字",
            n=len(long_cells)))

    # ⑪ 信源地图编号（跨表重号 / 跳号，都会让正文「#5」指代不唯一）
    dup, miss = _source_map_numbers(text)
    if dup:
        warnings.append(Warn(
            "map.dup",
            f"§六 编号重复 {len(dup)} 个（{dup[:5]}）——"
            "6.1/6.2/6.3 应共用**一套连续编号**，否则正文里「#5」指的是哪张表的 5 说不清",
            keys=tuple(str(x) for x in dup)))
    elif miss:
        warnings.append(Warn(
            "map.miss",
            f"§六 编号跳号（缺 {miss[:5]}）——"
            "多半是删行或用「指代行占号」补位；编号必须连续，"
            "同一 BV 只出现一次（指代写进定性列：「同 6.1 #5」）",
            keys=tuple(str(x) for x in miss)))

    # ⑫ 关于第二人称：**不做检查**（2026-10-29 试过，撤回）。
    # 报告里 80% 以上的「你」出现在「」引用的原话与视频标题里
    # （「你简介怎么照抄隔壁的」「手把手教你用 DSH 部署」），引用必须原样保留；
    # 剩下的是 UP 主昵称（「o你跺你也麻o」）。用正则区分不了"引用"与"叙述"，
    # 报出来 90% 是误报，反而会淹没真问题。规范改为写进模板：
    # 「叙述用『用户／委托方』，引用照录」——交给写的人判断，不交给正则。

    # ⑦ 目录名 / 模式 / 标题三者一致
    p2, w2 = _report_dir_check(md_path, text)
    problems += p2
    warnings += w2

    # ⑧ 旧版编排：**降级为提醒**。
    # 2026-10-22 之前产出的报告用的是「信源地图在 §二 / 可追溯清单独立成节」，
    # 内容没错，只是版式落后于新标准。把它们判成"不合格"会让历史产物
    # 一跑自检就红一片，反而掩盖真正的新问题。
    legacy = sum(1 for m in LEGACY_FINGERPRINT if m in text) >= LEGACY_MIN_HITS
    if legacy and problems:
        must_keep = [p for p in problems if p.startswith("第一行不是")]
        # 「缺 §N」是**新版编号**判据，对旧版报告一定全红；逐条报只会刷屏，
        # 合成一条说明更准确：内容是齐的，编号不同。
        misses = [p for p in problems if p.startswith("缺 `## ")]
        rest = [p for p in problems if p not in must_keep and p not in misses]
        if misses:
            warnings.append(Warn(
                "legacy.order",
                f"章节编号与新版编排不同（{len(misses)} 节按新编号找不到），"
                "内容未缺失；迁移方法见 references/briefing-template.md"))
        warnings += [Warn("legacy.problem", p + "（旧版编排，按提醒处理）", keys=(p,))
                     for p in rest]
        problems = must_keep

    # ---- 存量基线 ----
    base_file = _baseline_file(md_path) if baseline else None
    entry: dict | None = None
    baseline_known = False
    if base_file is not None:
        data = _read_baseline(base_file)
        if data is not None:
            baseline_known = True
            entry = data["reports"].get(_rel_key(md_path, base_file.parent))
        if update:
            _write_baseline(base_file, md_path, warnings)
            entry = _index(warnings)
    shown, suppressed, resolved = _compare(warnings, entry)
    if show_all:
        shown, suppressed = warnings, 0

    for w in shown:
        print("[WARN]", w.msg)
    if problems:
        print("[FAIL] 结构自检未通过：")
        for p in problems:
            print("  -", p)
        return 1
    if strict and shown:
        print(f"[FAIL] 新增 {len(shown)} 条提醒（--strict 把新增当失败）")
        return 1

    bits: list[str] = []
    if shown:
        bits.append(f"新增 {len(shown)} 条提醒")
    if suppressed:
        bits.append(f"存量 {suppressed} 处已抑制")
    if resolved:
        bits.append(f"存量已修好 {resolved} 处")
    print("[OK] 结构自检通过" + (f"（{'；'.join(bits)}）" if bits else ""))

    if update and base_file is not None:
        print(f"[INFO] 已把当前 {_total(_index(warnings))} 处提醒记为存量：{base_file}")
    elif resolved:
        print(f"[INFO] 有 {resolved} 处存量提醒已消失，`--baseline-update` 可刷新基线")
    if baseline and base_file is None and warnings:
        print("[INFO] 这份 md 不在报告库目录里，跳过基线（临时文件不做存量比对）")
    elif baseline and base_file is not None and not baseline_known and not update:
        print(f"[INFO] 未找到基线 {base_file.name}——`--baseline-update` 可把当前 "
              f"{_total(_index(warnings))} 处提醒冻结为存量")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="议题报告 md → 单文件 HTML（可选 PDF）")
    ap.add_argument("--md", required=True)
    ap.add_argument("--html", default=None, help="默认与 md 同名")
    ap.add_argument("--title", default=None, help="默认取 md 的一级标题")
    ap.add_argument("--pdf", action="store_true")
    ap.add_argument("--pdf-out", default=None)
    ap.add_argument("--check", action="store_true", help="只做结构自检")
    ap.add_argument("--baseline-update", action="store_true",
                    help="把当前提醒冻结为存量基线（配 --check；只影响这一份报告）")
    ap.add_argument("--no-baseline", action="store_true",
                    help="忽略存量基线，报出全部提醒（排查用）")
    ap.add_argument("--strict", action="store_true",
                    help="新增提醒也算失败（返回 1）；当交付闸门用")
    args = ap.parse_args()

    if args.check:
        if not os.path.exists(args.md):
            raise SystemExit(f"[FAIL] 找不到 {args.md}")
        sys.exit(check(args.md, baseline=not args.no_baseline,
                       update=args.baseline_update, show_all=args.no_baseline,
                       strict=args.strict))

    out = render(args.md, args.html, args.title)
    n_style = open(out, encoding="utf-8").read().count("<style>")
    print(f"[OK] HTML {out}  ({os.path.getsize(out) / 1024:.0f} KB, style 块 {n_style} 个)")
    if n_style != 1:
        print("[WARN] style 块不是 1 个", file=sys.stderr)
    if args.pdf:
        to_pdf(out, args.pdf_out)


if __name__ == "__main__":
    main()
