"""把议题报告 markdown 渲染成单文件 HTML，并（可选）打印成 PDF。

用法
----
    python scripts/render_briefing.py --md "<报告.md>" --pdf
    python scripts/render_briefing.py --md "<报告.md>" --check     # 只做结构自检
    python scripts/render_briefing.py --md "<报告.md>" --html "<指定.html>"

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
* **kicker 与标题后缀按模式区分**：深入分析 → 「B站信息深入报告」，
  简要 → 「B站信息简报」。两种模式的耗时与样本量差一个量级，
  产物却叫同一个名字，读者无法从标题判断能不能当依据。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

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

def kicker_for(title: str) -> str:
    """按标题后缀选报头小标。深入模式的产物不能自称「简报」。"""
    if "深入报告" in title:
        return "B 站议题深入报告"
    if "简报" in title:
        return "B 站议题情报简报"
    return "B 站议题情报报告"


def split_masthead(md_text: str) -> tuple[str, str]:
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
            f'<p class="kicker">{_esc(kicker_for(title))}</p>',
            f"<h1>{_esc(title)}</h1>"]
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

def style_tables(html: str) -> str:
    """给表格包一层 .tw；列数多的加 .wide（信源地图 8 列在 780px 里要缩字号）。

    另外给「冲突与未收敛处」章节内的表格加 `.clash`——那一节的表是**对照**语义
    （A 方 / B 方 / 我的处理），普通斑马纹表格读起来会漏掉"两边在打架"这件事。
    """
    def repl(m: re.Match) -> str:
        table = m.group(0)
        ncol = table.count("<th")
        cls = ' class="wide"' if ncol >= 7 else ""
        inner = re.sub(r"^<table>", f"<table{cls}>", table)
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

def render(md_path: str, html_path: str | None = None, title: str | None = None) -> str:
    import markdown

    for p in (SHELL, CSS):
        if not os.path.exists(p):
            raise SystemExit(f"[FAIL] 模板缺失：{p}")

    md_text = open(md_path, encoding="utf-8").read()
    head, rest = split_masthead(md_text)

    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
    body = md.convert(rest)
    body = style_tables(body)
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
    body = f'{head}\n{build_legend()}\n{overview}\n<div class="body">{body}</div>'
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

def _section_pos(text: str, prefix: str) -> int:
    i = text.find(prefix)
    return i


def _report_dir_check(md_path: str, text: str) -> tuple[list[str], list[str]]:
    """目录名（日期+模式+议题）与标题后缀是否一致。

    只在文件确实落在报告库目录里时才检查——检查临时文件、旧路径文件时静默跳过，
    否则 `--check` 会对任何目录下的 md 报一堆无关的错。
    """
    problems: list[str] = []
    warnings: list[str] = []
    sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "src")))
    try:
        from big import reports
    except Exception:                                         # noqa: BLE001
        return problems, warnings
    info = reports.locate(md_path)
    if not info or not info.get("in_library"):
        return problems, warnings
    title = text.lstrip().splitlines()[0].lstrip("# ").strip()
    want = "深入报告" if info["mode"] == "deep" else "简报"
    if want not in title:
        problems.append(
            f"标题与所在目录的模式不符：目录是「{info['mode_label']}」，"
            f"标题里应含「{want}」，实际是「{title}」")
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


def _library_rating_conflicts(md_path: str, text: str) -> list[str]:
    """同一 BV 在报告库里的其他报告中评级不同 → 提醒。

    ⚠️ 这是**提醒**不是错误：同一 BV 在不同议题里的「可用度」本来就该不同
    （可用度是相对议题的）。但**来源字母**（A–F）不该因为议题或模式而变。
    所以只在字母不同时措辞更强，数字不同时只提一句。
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
        msgs: list[str] = []
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
            msgs.append(f"与《{other.parent.name}》对同一 BV 的来源评级不一致："
                        + "、".join(letter[:4]) + ("…" if len(letter) > 4 else "")
                        + tail)
        return msgs
    except Exception:                                         # noqa: BLE001
        return []


def check(md_path: str) -> int:
    """结构自检：只报问题与提醒，不写文件。交付前跑一次。

    **问题是硬错（返回 1），提醒是软错（返回 0）**。
    旧版编排的报告会得到一组提醒而不是报错——它们的内容没错，
    只是版式落后于 2026-10-22 的新标准，不该被判成"不合格"。
    """
    text = open(md_path, encoding="utf-8").read()
    problems: list[str] = []
    warnings: list[str] = []

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
            warnings.append(msg)

    # ⑤ 视觉约定（缺了 HTML 会静默退化）
    if "✅" not in text and "⚠️" not in text and "◐" not in text:
        warnings.append("全文没有交叉验证标记（✅/◐/⚠️）——概览条会缺失")
    if not re.search(r"<td[^>]*>\s*[A-F][1-4]\s*</td>", text) and \
       not re.search(r"\|\s*\**([A-F][1-4])\**\s*\|", text):
        warnings.append("没有找到评级（如 `A2`/`B2`）——概览条会缺失")

    # ⑥ 深入模式必须交代这一轮花了多少（路线图 P1 的验收点）
    title = text.lstrip().splitlines()[0] if text.strip() else ""
    if "深入报告" in title and "单轮开销" not in text:
        problems.append("§五 缺「单轮开销」一行——深入模式必须能回答"
                        "「这一轮花了多少」，取法：registry_upsert(kind='commit') 返回的"
                        " final_cost.md_line 原样贴进去")

    # ⑥b 上下文隔离：两种模式都要说「评论/字幕有没有进主上下文」。
    # 这不是格式洁癖——它是"为什么这一轮便宜"的唯一凭据；不声明，
    # 下次跑就会退回到把 700 条评论堆进上下文的老路。
    if "上下文隔离" not in text:
        warnings.append("§五 没有「上下文隔离」声明——请写明子代理读了几条、"
                        "主上下文收到的是摘要还是原文（见 references/context-isolation.md）")

    # ⑥c 信源地图必须分小节：只有一张大表的话，读者无法区分
    # 「深挖过的」和「只是入选没读的」，两者的可信度差一个量级。
    if "## 六、信源地图" in text and "### 6.1" not in text:
        warnings.append("§六 没有 `### 6.1` 子小节——深挖对象与未深挖候选必须分开列")

    # ⑥d 同一 BV 在别的报告里评级不同 → 评级缺少锚点的信号（2026-10-22 实测：
    # 同一议题两轮 14 条可比 BV 里 10 条不一致，其中 7 条来源字母都不同）
    for msg in _library_rating_conflicts(md_path, text):
        warnings.append(msg)

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
            warnings.append(f"章节编号与新版编排不同（{len(misses)} 节按新编号找不到），"
                            "内容未缺失；迁移方法见 references/briefing-template.md")
        warnings += [p + "（旧版编排，按提醒处理）" for p in rest]
        problems = must_keep

    for w in warnings:
        print("[WARN]", w)
    if problems:
        print("[FAIL] 结构自检未通过：")
        for p in problems:
            print("  -", p)
        return 1
    print("[OK] 结构自检通过" + (f"（{len(warnings)} 条提醒）" if warnings else ""))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="议题报告 md → 单文件 HTML（可选 PDF）")
    ap.add_argument("--md", required=True)
    ap.add_argument("--html", default=None, help="默认与 md 同名")
    ap.add_argument("--title", default=None, help="默认取 md 的一级标题")
    ap.add_argument("--pdf", action="store_true")
    ap.add_argument("--pdf-out", default=None)
    ap.add_argument("--check", action="store_true", help="只做结构自检")
    args = ap.parse_args()

    if args.check:
        sys.exit(check(args.md))

    out = render(args.md, args.html, args.title)
    n_style = open(out, encoding="utf-8").read().count("<style>")
    print(f"[OK] HTML {out}  ({os.path.getsize(out) / 1024:.0f} KB, style 块 {n_style} 个)")
    if n_style != 1:
        print("[WARN] style 块不是 1 个", file=sys.stderr)
    if args.pdf:
        to_pdf(out, args.pdf_out)


if __name__ == "__main__":
    main()
