# Realization Semantics

Realization semantics are intended artifact semantics: what the agent believes the produced artifact should mean after interpreting user semantics and filling implementation/design gaps.

| ID | Realization semantics | Scope | Relation to user semantics | Linked user semantics | Rationale | Status |
| --- | --- | --- | --- | --- | --- | --- |
| R1 | 夜间任务按北京时间固定时刻运行，而不是用每隔24小时近似。 | implementation | grounded | U1 | “每天晚上”要求日历时间语义，固定时间能避免重启漂移。 | active |
| R2 | 方向和候选必须保存完整五乘五证据矩阵，最终结论只能从这25只中选择。 | implementation | grounded | U2,U3 | 让筛选过程可追溯并可机械校验数量。 | active |
| R3 | 每只候选至少读取一份最新可用研报正文；最终标的读取最多三份，并保存链接、元数据与逐篇分析。 | implementation | added | U3,U4 | 将“读研报”和“对应研报分析”落实为可验证产物，同时限制抓取规模。 | active |
| R4 | 公开行情、行业强度、资金、消息、研报数据先形成可解释评分，再由用户配置的 AI 综合研判；AI 不可用时明确降级为量化结果，不伪造研报观点。 | global | added | U1,U2,U3 | 兼顾夜间自动运行、可解释性和数据真实性。 | active |
| R5 | 荐股模块独立展示高风险免责声明、证据时间和风险/失效条件，不承诺收益；原有中立页面不改变。 | local | added | U5 | 控制新增预测功能与原项目定位冲突的范围。 | active |
