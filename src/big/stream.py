"""音视频流：dash 取流 + 并行分段下载。

为什么需要它
------------
B站文本来源有三种：人工 CC、AI 字幕、**无字幕**。新发的视频常常三者皆无。
而且「有没有字幕」必须带登录态判断——未登录时 `player/v2` 返回空字幕列表，
**看起来像"没字幕"**（实测：6 个视频 + 195 个分P，未登录时全部返回空列表）。

没有字幕就只剩两条路：
  * 本地 ASR 自建转写  → 需要**音轨**
  * 抽帧读画面         → 需要**视频流**

本模块负责把这两条路的原料取回来。

实测坑（都踩过）
----------------
1. **playurl 必须走 WBI 签名**：`/x/player/wbi/playurl`，否则部分出口 IP 拿不到流地址。
2. **dash 的 `baseUrl` 是自包含的**（含 init segment），单独下下来 ffmpeg 能直接解，
   不用拼 init 段。
3. **必须并行分段下载**：实测顺序下载 169 MB 要 40 分钟以上（开游戏加速器时
   单连接只有 0.044 MB/s），切成 8 段并行、段内 1 MB 子块 + partial 推进，
   同一文件 11 秒到手。
   ⚠️ 别把慢归给"CDN 按连接限速"——2026-10-18 实测：同一条命令关掉本机加速器后
   单连接就有 15.78 MB/s（**差 330 倍**）。慢的时候先查本地网络，再查 CDN。
4. **CDN 会半路 EOF**：响应头声明了长度，body 提前结束。`IncompleteRead` 异常对象
   **带着已读到的 partial 字节**——丢掉它整块重试，就是「块 × 重试」的时间黑洞。
5. **多源长度必须一致**：`baseUrl` 与 `backupUrl` 指向不同码流时长度会不同，
   混着下会拼出「长度对、内容错位」的文件，而且**事后查不出来**。
   只保留长度与主源一致的源。
"""

from __future__ import annotations

import http.client
import os
import pathlib
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .http import UA, wbi_fetch

REFERER = "https://www.bilibili.com/"
CHUNK = 1024 * 1024          # 子块 1 MB：太小会被 HTTP 往返开销拖死
JOBS = 8                     # 并行连接数


def _headers(cookie: str | None, extra: dict | None = None) -> dict:
    h = {"User-Agent": UA, "Referer": REFERER, "Origin": REFERER.rstrip("/"),
         "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9"}
    if cookie:
        h["Cookie"] = cookie
    if extra:
        h.update(extra)
    return h


# ------------------------------------------------------------------ 取流

def playurl(bvid: str, cid: int, cookie: str | None = None) -> dict:
    """取 dash 音视频流地址。**返回 dash 段**（含 video / audio 两个列表）。

    ⚠️ 返回的是 `data["dash"]`，不是 `data`——`pick_audio` / `pick_video` 吃的是 dash。
    早前返回 data 段，导致下游取到 `dash.get("video")` = 空 → 「没有视频流」。
    """
    st, res, err = wbi_fetch("/x/player/wbi/playurl",
                             {"bvid": bvid, "cid": cid, "fnval": 4048, "fnver": 0,
                              "fourk": 1, "platform": "pc"}, cookie)
    if st != 200 or not res or res.get("code") != 0:
        raise RuntimeError(f"playurl 失败：{err or (res or {}).get('message')}")
    data = res.get("data") or {}
    dash = data.get("dash")
    if not dash:
        raise RuntimeError("playurl 没有返回 dash（该视频可能不支持分P/需要登录）")
    return dash


def pick_audio(dash: dict) -> dict:
    auds = sorted(dash.get("audio") or [], key=lambda x: -(x.get("bandwidth") or 0))
    if not auds:
        raise RuntimeError("该视频没有独立音轨")
    return auds[0]


def pick_video(dash: dict, want: int = 720) -> dict:
    vids = dash.get("video") or []
    if not vids:
        raise RuntimeError("没有视频流")
    cand = [v for v in vids if v.get("id") == want or v.get("height") == want]
    if not cand:
        heights = sorted({v.get("height") for v in vids if v.get("height")})
        # 退而求其次：取不高于 want 的最高清晰度（读画面数字最低要 1080）
        lower = [v for v in vids if (v.get("height") or 0) <= want]
        if lower:
            cand = [max(lower, key=lambda v: (v.get("height") or 0,
                                              v.get("bandwidth") or 0))]
        else:
            raise RuntimeError(f"没有 {want}P，可选：{heights}")
    # 优先 avc1：av1 需要 libdav1d，部分 ffmpeg 构建没编进去
    cand.sort(key=lambda v: (0 if str(v.get("codecs", "")).startswith("avc1") else 1,
                             -(v.get("bandwidth") or 0)))
    return cand[0]


# ------------------------------------------------------------------ 下载

def probe_size(url: str, cookie: str | None = None, timeout: int = 15) -> int:
    """Range: bytes=0-1023 → 读 Content-Range 的总长。这是分段的前提。

    超时给 15 s 而不是 60 s：多源探测时每个源都要探一次，
    备用源挂掉时不该把整次下载拖住一分钟。
    """
    try:
        r = urllib.request.urlopen(
            urllib.request.Request(url, headers=_headers(cookie, {"Range": "bytes=0-1023"})),
            timeout=timeout)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"探测大小失败 HTTP {e.code}") from e
    cr = r.headers.get("Content-Range", "")
    r.close()
    if "/" not in cr:
        raise RuntimeError(f"服务器未返回 Content-Range（{cr!r}），无法确定总长")
    return int(cr.rsplit("/", 1)[1])


def _fetch_range(url: str, cookie: str | None, start: int, end: int,
                 timeout: int = 25) -> tuple[bytes, Exception | None]:
    """单次 Range 请求。返回 (bytes, err)。"""
    req = urllib.request.Request(
        url, headers=_headers(cookie, {"Range": f"bytes={start}-{end}"}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), None
    except http.client.IncompleteRead as e:
        # 关键：partial 字节要留下继续推，不是整块重试
        # ⚠️ IncompleteRead 在 http.client，**不在 urllib.error**。写成
        # `urllib.error.IncompleteRead` 时，Python 会在**匹配异常**阶段求值该属性
        # → AttributeError；这个 AttributeError 在 except 子句里抛出，同级的
        # `except Exception` 也接不住，于是向上穿透 → 整条重试链失效。
        # 症状：1080p 等大文件（更容易半路 EOF）必失败，且报错指向 urllib，看不出真因。
        # 2026-10-21 实测：1080p 抽帧 377 秒后抛
        # `AttributeError: module 'urllib.error' has no attribute 'IncompleteRead'`。
        return (e.partial or b""), e
    except Exception as e:                                    # noqa: BLE001
        return b"", e


def _segment(url: str, cookie: str | None, start: int, end: int,
             part_path: pathlib.Path, chunk: int = CHUNK) -> int:
    """下载 [start, end] 一段，边收边写。返回已写入字节数。"""
    pos = start
    with open(part_path, "wb") as f:
        while pos <= end:
            stop = min(pos + chunk - 1, end)
            data, err = _fetch_range(url, cookie, pos, stop)
            if data:
                f.write(data)
                pos += len(data)
            if err is not None and not data:
                raise RuntimeError(f"分段下载失败（{start}-{end}）：{err}")
            if err is not None and data:
                # 半个 chunk 就断了：继续从 pos 推，不丢弃已收字节
                continue
    return pos - start


def download(urls: list[str], out_path: pathlib.Path, cookie: str | None = None,
             jobs: int = JOBS, chunk: int = CHUNK, force: bool = False,
             timeout: int = 25) -> tuple[int, float]:
    """并行分段下载，返回 (总字节, 耗时秒)。

    断点续传：out_path 已存在的部分视为可信前缀，只补剩余区间。
    分段中间态写在 `.<name>.part{i}`，校验通过后才合并——**合并前主文件不变**，
    所以中途失败不会留下半截坏文件。
    """
    t0 = time.time()
    if not urls:
        raise RuntimeError("没有可用下载地址")
    total, last_err = None, None
    good: list[str] = []
    for u in urls:
        try:
            t = probe_size(u, cookie)
        except Exception as e:                                # noqa: BLE001
            last_err = e
            continue
        if total is None:
            total = t
        if t == total:
            good.append(u)
    if total is None:
        raise RuntimeError(f"所有源都探测不到总长：{last_err}")
    if total <= 0:
        raise RuntimeError(f"探测到的总长异常：{total}")
    if len(good) < len(urls):
        # 长度不同的源混着下会拼出「长度对、内容错位」的文件，事后查不出来
        good = good[:1] if good else urls[:1]
    url = good[0]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if force and out_path.exists():
        out_path.unlink()
    have = out_path.stat().st_size if out_path.exists() else 0
    if have > total:
        out_path.unlink()
        have = 0
    if have == total:
        return total, round(time.time() - t0, 1)

    # 切段：每段不超过 16 MB，段数受限以免打爆 CDN
    seg_len = 16 * 1024 * 1024
    starts = list(range(have, total, seg_len))
    parts = [out_path.with_name(f".{out_path.name}.part{i}") for i in range(len(starts))]

    def work(i: int) -> int:
        s = starts[i]
        e = min(s + seg_len - 1, total - 1)
        return _segment(url, cookie, s, e, parts[i], chunk=chunk)

    try:
        with ThreadPoolExecutor(max_workers=min(jobs, len(starts))) as ex:
            list(ex.map(work, range(len(starts))))
    except Exception:
        for p in parts:
            if p.exists():
                p.unlink()
        raise

    # 合并：先写临时文件，再原子替换
    tmp = out_path.with_name(f".{out_path.name}.merge")
    with open(tmp, "wb") as out:
        for p in parts:
            if not p.exists():
                raise RuntimeError(f"分段文件缺失：{p.name}")
            with open(p, "rb") as f:
                while True:
                    b = f.read(1024 * 1024)
                    if not b:
                        break
                    out.write(b)
    size = tmp.stat().st_size
    if size != total:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"合并后大小不符：{size} != {total}")
    os.replace(tmp, out_path)
    for p in parts:
        p.unlink(missing_ok=True)
    return total, round(time.time() - t0, 1)


def fetch_audio(bvid: str, cid: int, out_path: pathlib.Path,
                cookie: str | None = None, jobs: int = JOBS) -> tuple[int, float]:
    """下载音轨（转写用）。"""
    dash = playurl(bvid, cid, cookie)
    a = pick_audio(dash)
    urls = [a["baseUrl"]] + list(a.get("backupUrl") or [])
    return download(urls, out_path, cookie, jobs=jobs)


def fetch_video(bvid: str, cid: int, out_path: pathlib.Path, height: int = 720,
                cookie: str | None = None, jobs: int = JOBS) -> tuple[int, float]:
    """下载视频流（抽帧用）。height=1080 才读得清表格文字。"""
    dash = playurl(bvid, cid, cookie)
    v = pick_video(dash, height)
    urls = [v["baseUrl"]] + list(v.get("backupUrl") or [])
    return download(urls, out_path, cookie, jobs=jobs)
