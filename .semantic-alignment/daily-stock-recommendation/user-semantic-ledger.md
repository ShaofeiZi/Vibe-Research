# User Semantic Ledger

This file records user semantic changes, not every user input. Use `add`, `update`, or `delete`; include the reason.

## Ledger

| ID | Date | Operation | Category | Before | After | Reason | Source | Current? | Recheck trigger |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| U1 | 2026-07-17 | add | goal | none | 新增每日荐股模块：依据每天定时获取的消息与市场数据，每晚产出一只最可能上涨的股票及代码。 | clarification | 用户目标原文 | yes | none |
| U2 | 2026-07-17 | add | local-design | none | 每日评选五个最可能上涨的方向，每个方向保留五只候选股票。 | clarification | 用户目标原文 | yes | none |
| U3 | 2026-07-17 | add | local-design | none | 读取候选股票研报，结合市场分析和每日消息综合评判最终股票。 | clarification | 用户目标原文 | yes | none |
| U4 | 2026-07-17 | add | content | none | 最终结果须包含股票名称、代码、对应研报和研报分析。 | clarification | 用户目标原文 | yes | none |
| U5 | 2026-07-17 | update | global-design | 项目公开定位为只展示客观数据、不荐股、不预测。 | 新增独立的本地每日荐股研究模块，允许用户配置的 AI 基于证据产出候选与单股结论；其他页面继续保持中立数据工具定位。 | clarification | 用户明确要求增加每日荐股模块 | yes | none |
