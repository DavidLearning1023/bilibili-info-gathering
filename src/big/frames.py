"""段级抽帧：只在字幕出现「明显影响理解且无法纠正」的错误时用。

为什么不是全片抽帧
------------------
全片抽帧 = 下载整段视频 + 几十到上百张图，产出的是"我看过这个视频"，
不是"我核对了这个数字"。只有当**某个具体断言**因字幕错误而无法确认时，
抽那一秒附近的画面才是有价值的。

抽帧的判据（段级决策）
----------------------
* **触发**：字幕/口播里出现数字、专有名词、参数名，且**明显读错**
  （不是"可能读错"，是"这个错会改变结论"）
* **动作**：抽该时间点附近的一张画面
* **产出**：一张图 + 它在联系表里的编号
* **不做**：全片抽帧、逐镜头检测、为了"更全面"而抽帧

⚠️ 数字以画面为准，不以逐字稿为准。这条让结论的可靠性高于字幕。
但也别默认全抽——先按上面的判据筛，命中才抽。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

FFMPEG_CANDIDATES = [
    os.environ.get("FFMPEG_BIN", ""),
    "ffmpeg",                      # PATH 里能找到就用它（最通用）
    # Windows 常见安装位置；自定义安装请设 FFMPEG_BIN，或把 ffmpeg 加进 PATH
    r"C:\ffmpeg\bin\ffmpeg.exe",
    "/opt/homebrew/bin/ffmpeg",
    "/usr/bin/ffmpeg",
]


def ffmpeg_bin() -> str | None:
    """找到 ffmpeg；找不到返回 None（**不要 sys.exit**，MCP 工具里退出会杀死 server）。"""
    for c in FFMPEG_CANDIDATES:
        if not c:
            continue
        if os.path.isfile(c):
            return c
        w = shutil.which(c)
        if w:
            return w
    return None


def ffmpeg_hint() -> str:
    return ("没找到 ffmpeg。装一个（winget install Gyan.FFmpeg）"
            "，或设环境变量 FFMPEG_BIN 指向 ffmpeg.exe。"
            "没有 ffmpeg 就无法抽帧——此时请在结论里注明"
            "「该处字幕疑似有误，但未做画面核对」。")


def hms(sec: float) -> str:
    sec = max(0.0, float(sec))
    return f"{int(sec) // 3600:02d}:{int(sec) % 3600 // 60:02d}:{int(sec) % 60:02d}"


def _fname_hms(sec: float) -> str:
    """给**文件名**用的时间戳。

    ⚠️ 不能直接用 `hms()`——它含冒号，而 **冒号在 Windows 上是非法文件名字符**。
    真实发生过：ffmpeg 报 "Error muxing a packet / Invalid argument"，
    看起来像解码失败，实际是输出路径根本创建不出来（目录里一张图都没有）。
    """
    return hms(sec).replace(":", "-")


def extract_at(video: pathlib.Path, seconds: list[float], outdir: pathlib.Path,
               prefix: str = "f", quality: int = 2,
               timeout: int = 120) -> list[dict]:
    """在给定时间点各抽一帧。返回 [{sec, path, ok, err}]。

    用**输出侧定位**（`-ss` 放在 `-i` 之后）：输入侧定位只落在关键帧上，
    偏差可能好几秒——那正是我们要核对的时刻，不能偏。
    """
    bin_ = ffmpeg_bin()
    if not bin_:
        return [{"sec": s, "path": None, "ok": False, "err": ffmpeg_hint()}
                for s in seconds]
    outdir.mkdir(parents=True, exist_ok=True)
    out = []
    for i, s in enumerate(seconds, 1):
        dst = outdir / f"{prefix}_{i:02d}_{_fname_hms(s)}.jpg"
        cmd = [bin_, "-hide_banner", "-loglevel", "error",
               "-i", str(video), "-ss", f"{max(0.0, float(s)):.3f}",
               "-frames:v", "1", "-q:v", str(quality), "-y", str(dst)]
        try:
            r = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                               errors="replace", timeout=timeout)
            if r.returncode != 0 or not dst.exists():
                out.append({"sec": s, "path": None, "ok": False,
                            "err": (r.stderr or "").strip()[-300:] or "ffmpeg 未产出文件"})
            else:
                out.append({"sec": s, "path": str(dst), "ok": True, "err": None})
        except subprocess.TimeoutExpired:
            out.append({"sec": s, "path": None, "ok": False, "err": "ffmpeg 超时"})
        except OSError as e:
            out.append({"sec": s, "path": None, "ok": False, "err": str(e)})
    return out


def verify_seek(frames: list[dict]) -> dict:
    """抽帧正确性自检：不同时间点抽出的图**必须不同**。

    若多个时间点抽出的图完全一样（字节级相同），说明定位失败——
    一直在读第 0 帧，而 ffmpeg **不会报错**。这是静默错误：
    看起来拿到了画面，其实时间点全是错的。

    返回 {"ok": bool, "unique": n, "n": m, "note": str}
    """
    import hashlib
    ok = [f for f in frames if f.get("ok") and f.get("path")]
    if len(ok) < 2:
        return {"ok": True, "unique": len(ok), "n": len(ok),
                "note": "只抽了一帧，无法验证定位（多抽两个时间点可验证）"}
    hs = set()
    for f in ok:
        try:
            hs.add(hashlib.sha256(pathlib.Path(f["path"]).read_bytes()).hexdigest())
        except OSError:
            pass
    n, u = len(ok), len(hs)
    if u == 1 and n > 1:
        return {"ok": False, "unique": u, "n": n,
                "note": ("所有时间点抽出的图完全相同 → 定位失败（一直在读第 0 帧）。"
                         "不要使用这批图；换清晰度或改用带时间戳的其它方式核对。")}
    return {"ok": True, "unique": u, "n": n,
            "note": f"{n} 帧中有 {u} 张不同画面，定位正常"}


def contact_sheet(frames: list[dict], outdir: pathlib.Path, cols: int = 4,
                  cell: int = 480) -> dict:
    """把抽到的帧拼成一张带编号的网格图。返回 {sheet, grid}。"""
    ok = [f for f in frames if f.get("ok") and f.get("path")]
    if not ok:
        return {"sheet": None, "grid": []}
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return {"sheet": None, "grid": [],
                "note": "未安装 Pillow，无法拼联系表（可 pip install pillow）；"
                        "单张原图路径仍在 grid 里"}

    outdir.mkdir(parents=True, exist_ok=True)
    imgs = []
    for f in ok:
        try:
            im = Image.open(f["path"]).convert("RGB")
        except Exception:                                      # noqa: BLE001
            continue
        ratio = cell / max(im.width, 1)
        im = im.resize((cell, max(1, int(im.height * ratio))))
        imgs.append((f, im))
    if not imgs:
        return {"sheet": None, "grid": []}

    rows = (len(imgs) + cols - 1) // cols
    h = max(im.height for _, im in imgs)
    sheet = Image.new("RGB", (cols * cell, rows * (h + 26)), (245, 245, 245))
    draw = ImageDraw.Draw(sheet)
    grid = []
    for idx, (f, im) in enumerate(imgs):
        r, c = divmod(idx, cols)
        x, y = c * cell, r * (h + 26)
        sheet.paste(im, (x, y + 26))
        draw.rectangle([x, y, x + cell, y + 25], fill=(28, 28, 28))
        draw.text((x + 6, y + 7), f"#{idx + 1}  {hms(f['sec'])}", fill=(255, 255, 255))
        grid.append({"#": idx + 1, "sec": f["sec"], "ts": hms(f["sec"]),
                     "file": f["path"]})
    dst = outdir / "frames_sheet.jpg"
    sheet.save(dst, quality=88)
    return {"sheet": str(dst), "grid": grid}
