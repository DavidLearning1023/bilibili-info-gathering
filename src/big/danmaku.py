"""弹幕：B站独有的第三条信息通道。

为什么值得单独一条通道
----------------------
评论告诉你"他们事后怎么想"，弹幕告诉你"他们当场在哪里炸了"。三个结构性优势：

  1. **秒级对齐**：每条弹幕带视频内时间戳，可直接对上口播段落；
     评论只有 ctime（发表时间），与视频内容没有对应关系。
  2. **全量**：一条 25 分钟视频能拿到 2,000+ 条，而评论样本通常只取 100–300 条。
  3. **反应纯度高**：是"看片那一刻"的反应，未经事后编辑，也不受热度排序影响。

最实用的一条：**广告披露常常藏在弹幕里**。实测某期简介为空、无商单标注，
但 t=635.2s 就有「本期推广 XX 电竞椅」——评论区里没有这个信息。
同一期还有观众自己做的精确到秒的纠偏（"指正，A 和 B 是两个机构"）。

只做三件事，不做全量 NLP：密度曲线（哪段炸了）、高重复句（集体口号）、
披露关键词扫描（有没有广告线索，**不判断是谁说的**）。

一条否证（别走回头路）
----------------------
网上传「p[6] = crc32(uid)，可反查 UP 主」——实测不成立。已知 UP 主 mid 的 crc32
与该条确定是作者自述弹幕的哈希对不上；又试了取反、int 4/8 字节、md5[:8]、sha1[:8]
五种变体全部不匹配。所以本模块**不承诺识别发送者**，只输出内容线索。

接口坑
------
`/x/v1/dm/list.so` 返回 XML（无需登录）。CDN 声明 `Content-Encoding: deflate`
但实际是 **raw deflate（无 zlib 头）**，`zlib.decompress()` 会报
"incorrect header check"，必须用 `zlib.decompress(data, -zlib.MAX_WBITS)`。
且无论请求头怎么声明都返回同一份压缩内容。
"""

from __future__ import annotations

import gzip
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from collections import Counter

from .http import UA
from .video import fmt_ts

DISCLOSURE_KW = ("本期", "推广", "赞助", "广告", "合作", "恰饭", "商务", "感谢")


def fetch_xml(cid: int, bvid: str, timeout: int = 40) -> bytes:
    url = "https://api.bilibili.com/x/v1/dm/list.so?" + urllib.parse.urlencode({"oid": cid})
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": f"https://www.bilibili.com/video/{bvid}/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return _decompress(raw)


def _decompress(raw: bytes) -> bytes:
    """三种可能：明文 XML / raw deflate / gzip。少判一种就抛 ParseError，
    而报错位置（ET.fromstring）离病因很远。"""
    if raw[:1] == b"<":
        return raw
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    for wbits in (-zlib.MAX_WBITS, zlib.MAX_WBITS, zlib.MAX_WBITS | 32):
        try:
            out = zlib.decompress(raw, wbits)
            if out[:1] == b"<":
                return out
        except Exception:                                     # noqa: BLE001
            continue
    return raw


def parse(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    out = []
    for d in root.findall("d"):
        p = (d.get("p") or "").split(",")
        if len(p) < 8:
            continue
        try:
            t = float(p[0])
        except ValueError:
            continue
        out.append({"t": t, "mode": p[1], "pool": p[5],
                    "uhash": (p[6] or "").lower().zfill(8),
                    "text": (d.text or "").strip()})
    return [x for x in out if x["text"]]


def analyze(items: list[dict], duration: int = 0, top: int = 5,
            samples: int = 5, min_repeat: int = 3) -> dict:
    """把弹幕压成"位置信号"，不返回原文全量。

    密度粒度随片长自适应：≤40 分钟按分钟，>40 分钟按 5 分钟。
    不这么切的话，一小时的视频会打出 60+ 行纯数字——那是成本不是信息。
    """
    if not items:
        return {"n": 0, "note": "弹幕为空（可能被关闭或需要登录）"}
    gran = 60 if (duration or 0) <= 40 * 60 else 300
    unit = "每分钟" if gran == 60 else "每 5 分钟"
    buckets = Counter(int(d["t"] // gran) for d in items)
    mx = max(buckets.values())

    peaks = []
    for m, n in buckets.most_common(top):
        lo, hi = m * gran, m * gran + gran
        seg = sorted((d for d in items if lo <= d["t"] < hi), key=lambda x: x["t"])
        step = max(1, len(seg) // samples)
        peaks.append({
            "span": f"{fmt_ts(lo)}–{fmt_ts(min(hi, duration or hi))}",
            "n": n,
            "samples": [f"{fmt_ts(d['t'])} {d['text'][:50]}" for d in seg[::step][:samples]],
        })

    txt = [d["text"] for d in items]
    rep = [(t, c) for t, c in Counter(txt).most_common(30) if c >= min_repeat][:12]
    repeated = [{"text": t[:40], "count": c,
                 "first": fmt_ts(min((d["t"] for d in items if d["text"] == t), default=0))}
                for t, c in rep]

    hits = [d for d in items if any(k in d["text"] for k in DISCLOSURE_KW)
            and len(d["text"]) <= 60]
    disclosure = {
        "n": len(hits),
        "first_at": fmt_ts(min((x["t"] for x in hits), default=0)) if hits else None,
        "items": [f"{fmt_ts(d['t'])} {d['text'][:70]}"
                  for d in sorted(hits, key=lambda x: x["t"])[:10]],
    }

    # 密度曲线只给前 lead 个桶的原始序列，用于判断"是单峰还是全程均匀"，
    # 不给全量——全量就是几十个数字，占上下文不产出信息。
    series = [{"bucket": (f"{m:02d}" if gran == 60 else fmt_ts(m * gran)),
               "n": buckets.get(m, 0)} for m in range(max(buckets) + 1)]
    return {
        "n": len(items),
        "granularity": unit,
        "max_per_bucket": mx,
        "peaks": peaks,
        "repeated": repeated,
        "disclosure": disclosure,
        "series": series,
        "note": "密度曲线只回答「哪一段炸了」，把它对齐到口播段落，"
                "用在「延伸发散」与「评论区结构性偏差」两处，不单开一章。",
    }
