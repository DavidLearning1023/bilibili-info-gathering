"""测试的全局隔离。

**为什么必须有这个文件**（2026-10-22 实测事故）：

`mcp_server._finish()` 每返回一次就往 `.registry/cost-log.jsonl` 追加一行记账，
而 `_finish` 会用 `sys._getframe(1).f_code.co_name` 取「调用者名」当工具名。
单元测试直接调 `_finish()` / 直接调 MCP 工具函数时，调用者就是测试函数本身，
于是真实账本里出现了 99 行 `test_finish_adds_envelope` 之类的记录，
再加 17 行 `scan_untrusted_text`（同一次测试跑一遍写一行）。

后果不是「脏」，是**数字是错的**：`window="all"` 的上游调用数与输出估算被测试
抬高了一倍多，而任何一次 `big cost` 都看不出来这是测试——它长得和真实记录一模一样。

修法放在 conftest 而不是逐个测试里 monkeypatch：新增测试的人不会记得加，
而这里**默认**就把账本指到 tmp。要测真实路径的，自己显式改 `registry.COST_LOG`。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from big import registry  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_cost_log(tmp_path, monkeypatch):
    """每个测试一个独立账本；测试永远不会碰到 `.registry/cost-log.jsonl`。"""
    monkeypatch.setattr(registry, "COST_LOG", tmp_path / "cost-log.jsonl")
    yield
