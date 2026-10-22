"""产出文档的落盘位置——「分析报告库」。

为什么要把产出从 `.registry/topics/` 里拿出来
--------------------------------------------
`.registry/` 是**资产库**：信源评级、议题档案、检索协议、观点簇、调用账本。
它的读者是代码与下一次运行的 Agent，位置以点开头、默认隐藏，是**有意为之**。

但简报（md/HTML/PDF）的读者是**人**。把人的产物放进以点开头的目录，后果很具体：
① 文件管理器里默认看不见，用户找不到自己刚生成的东西；
② 与机器资产混在一起，清理资产时容易误删报告；
③ 目录名是议题名，同一议题跑第二轮时两份报告挤在同一层，靠文件名区分。

所以拆成两处：

    <项目目录>/分析报告库/<YYYYMMDD>-深入分析-<议题>/    ← 人读的产物（本模块管）
    <项目目录>/.registry/topics/<议题>/                  ← 机器资产（protocol/clusters）

为什么用中文目录名
------------------
用户原话是「方便用户查找」。这个目录**只出现在用户自己的机器上**，不进仓库
（`.gitignore` 已排除），所以没有任何理由为了命令行的方便把它写成英文。
代码里用 `LIBRARY_DIRNAME` 常量，改一处即可。

命名规则（唯一真源，别在别处另写一套）
-------------------------------------
    文件夹：<YYYYMMDD>-<深入分析|简要分析>-<议题>
    文档  ：<议题>·B站信息<深入报告|简报>.md        （HTML/PDF 同名）

**深入模式的标题后缀是「深入报告」、简要模式是「简报」**——这是 2026-10-22
用户明确要求的区分：两种模式的样本量与耗时差一个量级，产物却叫同一个名字，
读者（包括三个月后的自己）无法从标题判断这份东西能不能当依据。

路径必须由代码算
----------------
日期 + 模式 + 议题三者拼起来，手写会漂：`2026-10-22` 与 `20261022`、
`深入分析` 与 `深入`、议题里带 `/` 或 `:`（Windows 非法）。这些错都不会报错，
只会让报告散落在两三个目录里。所以这里给一个确定性的入口：

    python -m big.cli report-path --topic "议题名" --mode deep --mkdir
"""

from __future__ import annotations

import datetime as dt
import os
import pathlib
import re

_HERE = pathlib.Path(__file__).resolve().parent.parent.parent
LIBRARY_DIRNAME = "分析报告库"
DEFAULT_ROOT = _HERE / LIBRARY_DIRNAME

# 模式 → 文件夹里的中文标签。只接受这两个值：
# 加第三种模式（比如"对比"）时，要让 `parse_folder` 也能认，否则校验会静默跳过。
MODES = {"deep": "深入分析", "brief": "简要分析"}
# 模式 → 文档标题后缀
DOC_SUFFIX = {"deep": "B站信息深入报告", "brief": "B站信息简报"}

_MODE_ALIASES = {
    "deep": "deep", "深入": "deep", "深入分析": "deep", "深入模式": "deep",
    "brief": "brief", "简要": "brief", "简要分析": "brief", "简要模式": "brief",
    "简": "brief", "quick": "brief",
}

# Windows 不允许 <>:"/\|?* 与控制字符；macOS/Linux 也讨厌斜杠
_BAD_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_FOLDER_RE = re.compile(r"^(\d{8})-(深入分析|简要分析)-(.+)$")


def library_root() -> pathlib.Path:
    """报告库根目录。`BIG_REPORT_DIR` 可覆盖（换盘、放到网盘同步目录）。"""
    env = os.environ.get("BIG_REPORT_DIR")
    return pathlib.Path(env) if env else DEFAULT_ROOT


def norm_mode(mode: str | None) -> str:
    """把用户/Agent 的各种说法归一成 `deep` / `brief`。

    认不出来**不静默回落到 deep**——那会让"简要"跑成"深入"的目录名。
    """
    key = str(mode or "").strip().lower()
    if key in _MODE_ALIASES:
        return _MODE_ALIASES[key]
    raise ValueError(f"mode 只能是 deep / brief（或 深入/简要），收到 {mode!r}")


def safe_topic(topic: str, limit: int = 60) -> str:
    """议题名 → 能当文件夹与文件名的字符串。截断是为了别撞 Windows 260 字符上限。"""
    name = _BAD_FS.sub("-", str(topic or "").strip()).strip(". ")
    name = re.sub(r"\s+", " ", name)
    return name[:limit] or "未命名议题"


def stamp(date: str | dt.date | None = None) -> str:
    """日期 → `YYYYMMDD`。接受 `2026-10-22` / `2026/10/22` / date 对象。"""
    if date is None:
        return dt.date.today().strftime("%Y%m%d")
    if isinstance(date, dt.date):
        return date.strftime("%Y%m%d")
    digits = re.sub(r"\D", "", str(date))
    if len(digits) != 8:
        raise ValueError(f"date 需要 8 位数字（如 2026-10-22），收到 {date!r}")
    return digits


def folder_name(topic: str, mode: str = "deep", date=None) -> str:
    return f"{stamp(date)}-{MODES[norm_mode(mode)]}-{safe_topic(topic)}"


def doc_title(topic: str, mode: str = "deep") -> str:
    """报告一级标题：`<议题> · B站信息深入报告`。

    议题**照录用户的说法**，不改写——报告的标题是用户用来找它的线索。
    """
    return f"{str(topic).strip()} · {DOC_SUFFIX[norm_mode(mode)]}"


def paths(topic: str, mode: str = "deep", date=None, mkdir: bool = False) -> dict:
    """一次性算出这份报告的目录与三个同名文件。

    返回 `{topic, mode, mode_label, date, dir, md, html, pdf, title}`——
    所有字段都是 `str`，方便直接进 JSON / 打印。
    """
    m = norm_mode(mode)
    d = pathlib.Path(library_root()) / folder_name(topic, m, date)
    if mkdir:
        d.mkdir(parents=True, exist_ok=True)
    stem = f"{safe_topic(topic)}·{DOC_SUFFIX[m]}"
    return {
        "topic": str(topic).strip(),
        "mode": m,
        "mode_label": MODES[m],
        "date": stamp(date),
        "dir": str(d),
        "md": str(d / f"{stem}.md"),
        "html": str(d / f"{stem}.html"),
        "pdf": str(d / f"{stem}.pdf"),
        "title": doc_title(topic, m),
    }


def parse_folder(folder: str | pathlib.Path) -> dict | None:
    """从目录名反解出 `{date, mode, topic}`；不像报告库目录就返回 None。

    用途：交付前校验**标题后缀与所在目录的模式是否一致**。
    这条校验存在的理由很实在：模式与标题都靠人/模型手写，
    "在深入分析目录里放一份叫`简报`的文件"不会报错，只会让读者误判证据强度。
    """
    name = pathlib.Path(folder).name
    m = _FOLDER_RE.match(name)
    if not m:
        return None
    mode = norm_mode(m.group(2))
    return {"date": m.group(1), "mode": mode, "mode_label": MODES[mode],
            "topic": m.group(3)}


def locate(path: str | pathlib.Path) -> dict | None:
    """给定报告文件路径，返回它所在报告目录的解析结果 + `in_library`。

    只认**直接父目录**是报告库目录的情况（不向上递归找），
    免得把 `分析报告库/xxx/sub/a.md` 之类的手工文件也算成标准产物。
    """
    p = pathlib.Path(path).resolve()
    info = parse_folder(p.parent)
    if not info:
        return None
    root = library_root().resolve()
    try:
        inside = root in p.parents
    except OSError:                                            # pragma: no cover
        inside = False
    info["in_library"] = inside
    return info
