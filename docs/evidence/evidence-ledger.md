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
| V1 新增核心模块覆盖率达到 80% 门槛 | verified | `tests/test_product_workflow.py`、`product-v1-validation.md` | Conda `cv`，coverage 同一命令复测，880/1076 语句，82% | 任务模块单独为 71%；覆盖率不等于业务有效性 |
| 采集失败不会自动产生模拟帖子 | verified | `test_unconfigured_live_collector_pauses_without_fake_data` | 未接受许可证时任务进入 paused，错误码明确，入库数为 0 | 尚未真实扫码采集 |
| SQLite 能阻止重复平台记录 | verified | `test_platform_source_id_is_unique`、导入闭环测试 | 同一微博 ID 重复写入；第二次 12 条全部计为重复 | 未做多进程高并发写入压测 |
| 单管理员业务接口受登录和 CSRF 保护 | verified | `test_protected_endpoints_require_login_and_csrf` | Flask 测试客户端检查 401/403 与成功登录 | 登录限流为单进程内存状态，未做分布式部署 |
| PDF 与 CSV 报告可生成和下载 | verified | `test_pdf_report_can_be_generated_and_downloaded`、CSV 对应用例 | 固定导入夹具；检查 PDF/CSV 文件签名 | 未做多页报告视觉人工复核 |
| Docker Compose 可冷启动并持久化重启 | unknown | 当前主机没有 `docker` 命令 | 未执行 | 在装有 Docker 的机器运行构建、健康检查和卷重启验收 |
| 外部 MediaCrawler 能在真实微博完成授权采集 | unknown | 仅完成固定版本适配器与伪进程测试 | 无真实账号和扫码执行 | 合规授权后做人工冒烟测试 |
