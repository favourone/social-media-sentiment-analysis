# 证据账本

| 主张或决策 | 状态 | 证据或来源 | 方法与条件 | 限制或下一测试 |
|---|---|---|---|---|
| 当前项目是 Flask + NLP/预测模型的舆情演示系统 | verified | `README.md`、`web/app.py`、`models/` | 仓库代码审计 | 未验证生产部署 |
| 当前大屏筛选会造成评论统计失真 | verified | `web/app.py` 与 Flask 基线请求 | 空结果筛选返回 0 帖子、8,000 评论 | 需用自动化回归测试固定 |
| 预测接口会静默使用模拟曲线 | verified | `web/app.py` 与未知话题请求 | 未知话题仍返回 200 和硬编码历史值 | 改为显式无数据响应 |
| 当前模型评估存在测试集词表泄漏 | verified | `api_model_metrics` 调用 `build_vocab(texts)` 后才拆分 | 静态数据流审计 | 改后需测试拆分顺序 |
| 当前数据为模拟数据 | inferred | `post_` ID、生成脚本、README 数据说明 | 对本地样本和生成器比对 | 需生成并读取来源清单转为 verified |
| 修复可信度链比视觉重做更值得优先 | inferred | 临时 MCDA 与基线失败 | 无官方评分细则 | 获得正式评分表后重新加权 |
| 改进后关键 API 合约达到 10/10 | verified | `tests/test_api_contract.py`、`validation.md` | 与基线相同的 10 个 Flask 测试客户端场景复测 | 仍需真实浏览器和用户任务验证 |
| 模型指标 API 不再在 GET 中训练或写检查点 | verified | `test_model_metrics_endpoint_is_read_only` | 临时目录中请求缺失/存在报告两种状态，模型目录始终为空 | 尚未对生产并发做压测 |
| TextCNN 词表仅由训练集构建 | verified | `test_vocabulary_is_built_from_training_text_only` | 120 条带唯一 token 的分层拆分夹具 | 未在本轮重新训练完整 2,000 条本地数据 |
| 当前本地 AI 话题可运行 SIR/ARIMA | verified | `validation.md` 原始复测记录 | 60 个观测点；SIR 7 天、ARIMA 7 步 | SIR NRMSE 0.2695，拟合有限；ARIMA 只适用于当前序列 |
| 项目已在真实社交媒体场景有效 | unknown | 无真实目标场景测试 | 无 | 下一步做带来源的数据验证与用户任务测试 |
