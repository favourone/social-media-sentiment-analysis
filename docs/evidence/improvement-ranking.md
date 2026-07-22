# Improvement Priority Ranking

| Rank | Candidate | Type | Value | Cost/Risk | Priority index |
|---:|---|---|---:|---:|---:|
| 1 | 补充数据来源标识与可复现实验记录 | high-confidence implementation | 4.90 | 1.70 | 2.882 |
| 2 | 强化默认部署安全和依赖降级 | high-confidence implementation | 4.05 | 2.00 | 2.025 |
| 3 | 修复API筛选与预测数据可信度 | high-confidence implementation | 4.90 | 2.70 | 1.815 |
| 4 | 保持现状 | no change | 1.60 | 1.00 | 1.600 |
| 5 | 重构模型评估以消除数据泄漏和请求时训练 | high-confidence implementation | 4.75 | 3.70 | 1.284 |
| 6 | 仅优化大屏视觉和动效 | presentation only | 2.50 | 2.70 | 0.926 |
| 7 | 扩展真实社交媒体采集与用户研究 | uncertain experiment | 3.90 | 5.00 | 0.780 |

Priority index = weighted value / weighted cost-risk.
Applied value weights: impact=0.30, judge_relevance=0.20, urgency=0.10, feasibility=0.15, confidence=0.15, learning_value=0.10.
Applied cost-risk weights: effort=0.70, risk=0.30.
Treat close scores as ties, test sensitivity, and document any override.

## 敏感性与实施决策

在“评审相关性优先”和“可行性优先”两组替代权重下，数据来源标识仍保持第 1，默认安全/降级仍保持第 2，说明前两项排序稳定。API 可信度修复在两组权重下均为最高价值 4.90，但因工作量分母位列第 3。

“保持现状”在可行性权重提高时会因成本极低升至第 3；本次明确否决该结果，因为它无法满足已预定义的关键 API 合约门槛，并保留了已复现的数据失真和静默伪造。模型评估重构虽然优先指数较低，但它直接消除测试集泄漏与 GET 请求写模型的可信度风险，因此与前三项一并实施。真实社交媒体采集需要网络、授权、隐私审查和代表性验证，保留为下一阶段实验，不在本轮伪装成已完成成果。
