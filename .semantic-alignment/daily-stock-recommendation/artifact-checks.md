# Artifact Checks

This is a mechanical check log comparing real artifacts with `realization-semantics.md`. Put synthesis in `audits.md`.

| ID | Artifact | Checked against | Result | Concrete note |
| --- | --- | --- | --- | --- |
| K1 | backend/scheduler.py, backend/app.py, frontend/src/pages/Tasks.tsx | R1 | pass | daily-recommendation 默认启用并按北京时间20:00运行；支持UI修改时刻；离线测试覆盖next_run、时间校验和晚间force刷新。 |
| K2 | backend/daily_recommendation.py and /tmp/tmp.kBSDhqVupz/daily-recommendations/latest.json | R2 | pass | 当前代码真实端到端产出5个方向、每方向5只、共25只互不重复候选；最终002739属于候选池；生成前有机械结构校验。 |
| K3 | backend/daily_recommendation.py and final E2E output | R3 | pass | 真实端到端25/25候选研报PDF正文读取成功；最终标的读取3份研报，均有PDF链接、正文摘录和逐篇分析。 |
| K4 | backend/daily_recommendation.py, backend/tests/test_daily_recommendation.py | R4 | pass | 行情、方向强度、资金、当日消息和研报形成可解释评分；AI被限制在候选池，缺失/越界/字段残缺均明确降级；最终E2E降级提示可见。 |
| K5 | frontend/src/pages/DailyRecommendation.tsx, README.md, backend/README.md | R5 | pass | 独立页面显示证据时间、分析模式、风险、失效条件和强免责声明；普通页面定位与公开文档已同步。 |
