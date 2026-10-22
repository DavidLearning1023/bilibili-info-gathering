"""HTTP 底座：WBI 签名、限速、BV 解析、凭据加载。

只用标准库，不引入 requests——这个包要能被任何 Python 3.10+ 直接跑起来。

三个必须知道的坑（都是实测踩出来的）：

1. **必须走 WBI 签名。** `/x/web-interface/view` 不带签名时，部分出口 IP 会被风控
   返回 412；带签名的 `/x/web-interface/wbi/view` 正常。
2. **评论分页只能用游标。** `pn` 页码参数已被上游废弃，传了会被忽略、每次返回同一批
   数据，结果成倍重复。见 comments.py。
3. **请求间隔不能太短。** 默认 400ms，批量扫描时可适当放宽；412 是 IP 级限流，
   换 cookie 没用。
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.bilibili.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# WBI 签名用的固定重排表（B站前端硬编码，非本项目发明）
MIXIN_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
             33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
             26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
             20, 34, 44, 52]

RATE_LIMIT_MS = int(os.environ.get("BIG_RATE_LIMIT_MS", "400"))
TIMEOUT = int(os.environ.get("BIG_TIMEOUT", "25"))

_last_request = [0.0]
_mixin_key: list[str | None] = [None]
_key_fetched_at = [0.0]
KEY_TTL = 6 * 3600          # WBI 密钥按天滚动，缓存 6 小时足够，且不会跨日失效

# ---------------- HTTP 请求计数（批次 B1 的 cost.calls 来源） ----------------
#
# 为什么需要它：「这一轮打了几次上游」是 Agent 判断"还要不要继续挖"的唯一客观依据，
# 而 `scan_topic` 的返回**没有任何出参成本**——只有入参回显。
#
# 口径（写清楚，避免以后被误读）：
#   * 一次 `fetch()` = 1 次；`wbi_fetch()` 内部走 `fetch()`，也算 1
#   * `wbi_key()` 取密钥本身也是一次 `fetch()`，算 1
#   * 评论的**游标翻页**每页各算 1（所以 n=100 的评论请求可能是 2–4 次）
#   * 用锁是因为 `scan_topic` 的评论拉取是 4 线程并发（`+=` 不是原子的）
_call_lock = threading.Lock()
_call_count = [0]


def call_count() -> int:
    """自进程启动起累计发出的 HTTP 请求数。"""
    with _call_lock:
        return _call_count[0]


def _count_call() -> None:
    with _call_lock:
        _call_count[0] += 1


def _throttle() -> None:
    now = time.time() * 1000
    wait = RATE_LIMIT_MS - (now - _last_request[0])
    if wait > 0:
        time.sleep(wait / 1000.0)
    _last_request[0] = time.time() * 1000


def fetch(path: str, params: dict | None = None, cookie: str | None = None,
          timeout: int | None = None) -> tuple[int | None, dict | None, str | None]:
    """返回 (http_status, parsed_json_or_None, error_text)。不抛异常。"""
    _throttle()
    _count_call()
    url = BASE + path
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url += "?" + urllib.parse.urlencode(clean)
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/",
               "Accept": "application/json, text/plain, */*",
               "Accept-Language": "zh-CN,zh;q=0.9", "Origin": "https://www.bilibili.com"}
    if cookie:
        headers["Cookie"] = cookie
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
            body = r.read().decode("utf-8", "ignore")
            return r.status, json.loads(body), None
    except urllib.error.HTTPError as e:
        hint = ("（HTTP 412 = 风控拦截；已用 WBI 签名，若仍 412 请降频或等待几分钟）"
                if e.code == 412 else "")
        return e.code, None, f"HTTP {e.code} {hint}"
    except Exception as e:                                    # noqa: BLE001
        return None, None, f"{type(e).__name__}: {e}"


def wbi_key() -> str:
    now = time.time()
    if _mixin_key[0] and now - _key_fetched_at[0] < KEY_TTL:
        return _mixin_key[0]
    st, d, err = fetch("/x/web-interface/nav")
    if st != 200 or not d:
        raise RuntimeError(f"无法获取 WBI 密钥：{err}")
    img = d["data"]["wbi_img"]
    raw = (img["img_url"].rsplit("/", 1)[-1].split(".")[0]
           + img["sub_url"].rsplit("/", 1)[-1].split(".")[0])
    _mixin_key[0] = "".join(raw[i] for i in MIXIN_TAB)[:32]
    _key_fetched_at[0] = now
    return _mixin_key[0]


def wbi_fetch(path: str, params: dict, cookie: str | None = None,
              timeout: int | None = None) -> tuple[int | None, dict | None, str | None]:
    """带 WBI 签名的请求。签名流程与 B站前端一致：参数排序 → 剔除 !'()* → md5(query + key)。"""
    mk = wbi_key()
    p = {k: v for k, v in params.items() if v is not None}
    p["wts"] = int(time.time())
    q = urllib.parse.urlencode([
        (k, "".join(c for c in str(v) if c not in "!'()*")) for k, v in sorted(p.items())
    ])
    p["w_rid"] = hashlib.md5((q + mk).encode()).hexdigest()
    return fetch(path, p, cookie, timeout)


def parse_bvid(raw: str) -> str:
    """从裸 BV 号 / 完整 URL / b23.tv 短链里取 BV 号。

    短链要跟随一次重定向。早期实现只做正则，短链直接失败——文档承诺了、代码没做，
    属于必须补的洞。这里保留兜底：解析失败就抛出可读的错误，不要静默返回原串。
    """
    if not raw:
        raise ValueError("输入为空")
    m = re.search(r"(BV[0-9A-Za-z]{10})", raw)
    if m:
        return m.group(1)
    if re.search(r"https?://(b23\.tv|bili2233\.cn)/", raw):
        try:
            req = urllib.request.Request(raw, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                final = r.geturl()
            m = re.search(r"(BV[0-9A-Za-z]{10})", final)
            if m:
                return m.group(1)
        except Exception as e:                                # noqa: BLE001
            raise ValueError(f"短链解析失败（{e}）：{raw}；请改用完整 URL 或裸 BV 号") from e
    raise ValueError(f"无法从输入中解析 BV 号：{raw}")


def load_cookie(cookie_file: str | None = None) -> str | None:
    """凭据来源：显式文件 > 环境变量 BILI_SESSDATA > ~/.bilibili-mcp/config.json。

    只有取字幕需要凭据；元数据、评论、弹幕都不需要登录。
    本函数**不写**任何文件、不缓存到磁盘——开源项目里凭据只能由用户自己提供。
    """
    if cookie_file:
        p = pathlib.Path(cookie_file)
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                sess = d.get("sessdata") or d.get("SESSDATA")
                if sess:
                    bits = [f"SESSDATA={sess}"]
                    for key, name in (("bili_jct", "bili_jct"), ("dedeuserid", "DedeUserID")):
                        if d.get(key):
                            bits.append(f"{name}={d[key]}")
                    return "; ".join(bits)
            except Exception:                                 # noqa: BLE001
                pass
    env = os.environ.get("BILI_SESSDATA")
    if env:
        parts = [s.strip() for s in env.split(";") if s.strip()]
        if all("=" not in s for s in parts):
            return "; ".join(f"{k}={v}" for k, v in
                             zip(("SESSDATA", "bili_jct", "DedeUserID"), parts))
        return env
    p = pathlib.Path.home() / ".bilibili-mcp" / "config.json"
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            sess = d.get("sessdata") or d.get("SESSDATA")
            if sess:
                bits = [f"SESSDATA={sess}"]
                for key, name in (("bili_jct", "bili_jct"), ("dedeuserid", "DedeUserID")):
                    if d.get(key):
                        bits.append(f"{name}={d[key]}")
                return "; ".join(bits)
        except Exception:                                     # noqa: BLE001
            pass
    return None
