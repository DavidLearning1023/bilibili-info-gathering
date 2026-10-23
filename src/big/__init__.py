"""big —— 从 B站海量视频里为一个议题取数的小工具包。

分层很明确：
  http / video / comments / danmaku / media / search  —— 取数与计算，确定性代码
  registry                                           —— 资产积累
  mcp_server                                         —— 把上面这些暴露成 MCP 工具

**判断留给人/模型，取数交给这里。** 双轴分级、离场规则、冗余度标记这些方法论
写在 SKILL.md 与 references/ 里，不进代码——代码只管"拿到干净的数据"。
"""

__version__ = "1.0.0"
