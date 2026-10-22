"""评论区图片：阈值决策 → 并行下载 → 拼联系表。

为什么要"联系表"而不是逐张看
----------------------------
一条视频可能有一百多条图片评论。逐张读图会让上下文和 token 直接爆掉。
把前 N 张拼成一张带编号的网格图，模型先看整版（一眼判断哪几张有信息量），
再回读个别原图——这是唯一能压住成本又不丢证据的做法。

判据用「图片评论占总赞」而不是「含图条数」：实测某期 3.4% 的条数拿走 25.5% 的赞，
按条数会被跳过，按赞量必须看。阈值 15% / 5% 是在前 100 条顶层评论的深度下标定的，
所以判据计算固定在该深度（见 comments.comment_stats）。

图片可直接下载，只需 `Referer: https://www.bilibili.com/`，无需登录。
"""

from __future__ import annotations

import os
import pathlib
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from .http import UA

REFERER = "https://www.bilibili.com/"
MAX_BYTES = 15 * 1024 * 1024

# 运行产物根目录：MCP 与 CLI 共用，避免两处各写一份默认值
OUT_ROOT = pathlib.Path(os.environ.get("BIG_OUT_DIR", str(pathlib.Path.cwd() / ".bigout")))

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhl.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def decide(meta: dict, must: float = 15.0, sample: float = 5.0) -> tuple[str, int]:
    """返回 (verdict, 建议下载张数)。"""
    share = meta.get("picture_like_share", 0) or 0
    if share >= must:
        return "must", 20
    if share >= sample:
        return "sample", 10
    return "skip", 0


def _pick_pictures(cmts: list[dict], top_n: int) -> list[dict]:
    """按赞量取前 top_n 条带图评论，保留赞/正文/原尺寸（长图要提醒回读）。"""
    withpic = [c for c in cmts if c.get("pictures") and not c.get("is_reply")]
    withpic.sort(key=lambda c: -(c.get("likes") or 0))
    out = []
    for i, c in enumerate(withpic[:top_n], 1):
        p = c["pictures"][0]
        out.append({"rank": i, "rpid": c["rpid"], "likes": c.get("likes") or 0,
                    "url": p["url"], "w": p.get("w") or 0, "h": p.get("h") or 0,
                    "text": (c.get("content") or "")[:60]})
    return out


def _download(item: dict, outdir: pathlib.Path) -> tuple[dict, pathlib.Path | None]:
    url = item["url"]
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
        ext = ".jpg"
    fn = outdir / f"{item['rank']:02d}_{item['likes']}_{item['rpid']}{ext}"
    if fn.exists() and fn.stat().st_size > 0:
        return item, fn
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER})
            with urllib.request.urlopen(req, timeout=30) as r:
                # 体积上限：评论里偶有几十 MB 的长截图/动图，一口气读进内存会把并发打爆。
                data = r.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                return item, None
            if data:
                fn.write_bytes(data)
                return item, fn
        except Exception:                                     # noqa: BLE001
            if attempt < 2:
                time.sleep(1.2 * (attempt + 1))
    return item, None


def _build_sheets(items: list[dict], outdir: pathlib.Path, cols: int = 5,
                  cell: int = 320, per_sheet: int = 20) -> list[pathlib.Path]:
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                font = ImageFont.truetype(p, max(13, cell // 21))
                break
            except Exception:                                 # noqa: BLE001
                continue
    if font is None:
        font = ImageFont.load_default()

    label_h = max(22, cell // 13)
    pad = 6
    sheets = []
    for si in range(0, len(items), per_sheet):
        chunk = items[si:si + per_sheet]
        # 行数按**这一张实际有几格**算，不按 per_sheet 预留。否则微表（6 张、
        # per_sheet=20）会画出 7 行高的画布，下面 5 行全空白——视觉上没坏，
        # 但白占 4 倍像素，等于白付 token。
        rows = (len(chunk) + cols - 1) // cols
        w = cols * (cell + pad) + pad
        h = rows * (cell + pad + label_h) + pad
        sheet = Image.new("RGB", (w, h), (250, 250, 248))
        d = ImageDraw.Draw(sheet)
        for k, it in enumerate(chunk):
            cx = pad + (k % cols) * (cell + pad)
            cy = pad + (k // cols) * (cell + pad + label_h)
            if it.get("file") and os.path.exists(it["file"]):
                try:
                    im = Image.open(it["file"])
                    if getattr(im, "n_frames", 1) > 1:
                        im.seek(0)
                    im = im.convert("RGB")
                    im.thumbnail((cell, cell))
                    sheet.paste(im, (cx + (cell - im.width) // 2,
                                     cy + label_h + (cell - im.height) // 2))
                except Exception:                             # noqa: BLE001
                    d.text((cx + 6, cy + label_h + 6), "读取失败", fill=(190, 0, 0), font=font)
            else:
                d.text((cx + 6, cy + label_h + 6), "未下载", fill=(190, 0, 0), font=font)
            d.rectangle([cx, cy + label_h, cx + cell, cy + label_h + cell], outline=(215, 215, 205))
            # 超长图（宽高比 > 4）在 320px 格子里会被压成一条，必须提醒回读原图。
            w_, h_ = it.get("w") or 0, it.get("h") or 0
            flag = ""
            if w_ and h_:
                if h_ / w_ > 4:
                    flag = " 长图"
                elif w_ / h_ > 4:
                    flag = " 宽图"
            d.text((cx + 2, cy + 3), f"#{it['rank']:02d} {it['likes']}赞{flag}",
                   fill=(25, 25, 25), font=font)
        out = outdir / f"sheet_{si // per_sheet + 1:02d}.jpg"
        sheet.save(out, quality=88)
        sheets.append(out)
    return sheets


def contact_sheet(bvid: str, cmts: list[dict], meta: dict, outdir: str | pathlib.Path,
                  top_n: int = 0, cols: int = 5, cell: int = 320,
                  force: bool = False) -> dict:
    """一次做完：判阈值 → 下载 → 拼图。返回格号清单，供模型按号回读原图。

    force=True 时跳过「图片占总赞」的阈值闸门——广扫阶段用的就是这条路：
    判据是为"深挖"设计的（前 100 条深度、15%/5% 两档），而广扫只有十几条评论样本，
    阈值算出来的 verdict 不可比。但热评带图这件事本身有价值（高赞 = 已被看见），
    所以广扫改用 force + 小尺寸微表，成本压到最低。
    """
    verdict, default_n = decide(meta)
    n = top_n or default_n
    if verdict == "skip" and not top_n and not force:
        return {"verdict": "skip", "n": 0,
                "note": "图片评论占总赞不足 5%，本轮不必读图"}
    if n == 0:
        return {"verdict": verdict, "n": 0, "note": "没有符合条件的图片评论"}
    items = _pick_pictures(cmts, n)
    if not items:
        return {"verdict": verdict, "n": 0, "note": "样本里没有带图评论"}
    out = pathlib.Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    ok = 0
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(_download, it, out): it for it in items}
        for f in as_completed(futs):
            it, fn = f.result()
            it["file"] = str(fn) if fn else None
            ok += 1 if fn else 0
    sheets = _build_sheets(items, out, cols=cols, cell=cell)
    return {
        "verdict": verdict,
        "n": len(items),
        "downloaded": ok,
        "sheets": [str(s) for s in sheets],
        "grid": [{"#": it["rank"], "likes": it["likes"], "rpid": it["rpid"],
                  "size": f"{it.get('w')}x{it.get('h')}",
                  "text": it.get("text") or "（纯图片）",
                  "file": it.get("file")} for it in items],
        "note": "先看联系表整版判断哪几格有信息量，再按 #编号回读个别原图；"
                "数字与结论只认原图，缩略图仅用于定位。",
    }


def micro_sheet(bvid: str, cmts: list[dict], outdir: str | pathlib.Path,
                top_n: int = 6, cols: int = 3, cell: int = 160) -> dict:
    """广扫阶段的低成本判图：热评里带图的取前几张，拼一张小表。

    为什么广扫也要判图
    ------------------
    高赞 = 已经被大量观众看见，这个事实本身就是信息；而「这条评论是纯图/图文」
    在纯文本采样里会被压缩成"（纯图片）"三个字，等于把最热的那条信息整条丢掉。

    为什么成本可控
    --------------
    * 只取热评 **中带图** 的那几条（热评 15 条里通常 0–5 条带图），不是全部评论；
    * 格子 160px、3 列，一屏就是全部；
    * 只在「热评里确实有图」时才下载，没图的候选 0 次请求。

    局限（必须知道）：160px 只能判断"这张图是什么类型"（梗图/截图/图表/照片），
    **读不出数字**。要读数字必须回读原图或走 contact_sheet。
    """
    res = contact_sheet(bvid, cmts, {}, outdir, top_n=top_n, cols=cols,
                        cell=cell, force=True)
    res["mode"] = "micro"
    res["limit"] = ("160px 微表只能判类型，读不出数字；"
                    "要引用具体数字必须回读原图（grid 里的 file 字段）")
    return res
