# 社交媒体舆情分析与热点事件预测系统

这是一个用于教学、算法验证和演示的 Flask 舆情分析原型，包含文本处理、情感分类、主题发现、传播情景模拟、时间序列预测和可视化大屏。

> 当前默认数据是程序生成的模拟数据。项目不会把模拟指标表述为真实社交媒体效果；大屏会显示数据类型和证据状态。

## 解决的问题

目标用户是需要快速演示舆情分析流程的学生和项目团队。系统把以下路径整合为一个可运行原型：

`数据准备 → 文本清洗/分词 → 模型训练与离线评估 → 聚合/预测 API → Web 大屏`

本轮改进优先解决了四个会破坏演示可信度的问题：

- 日期/话题筛选后，评论数曾错误地保留为全量；
- 非法预测参数曾返回 500，未知话题曾静默替换为内置曲线；
- 模型指标曾在 GET 请求中现场训练并写入模型文件；
- 词表曾在数据拆分前用全部文本构建，形成测试集信息泄漏。

基线、验收门槛、方案排序和验证记录位于 [`docs/evidence/`](docs/evidence/)。

## 快速开始

推荐 Python 3.9 及以上版本：

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

生成可复现的模拟数据：

```bash
python scripts/generate_data.py --seed 42 --reference-time 2026-07-01T12:00:00
```

该命令同时写入 `data/raw/metadata.json`，记录生成器、随机种子、参考时间、规模和使用限制。`data/` 被 Git 忽略，不会误提交数据集。

运行完整流水线：

```bash
python scripts/run_pipeline.py
```

流水线会：

1. 加载或生成数据；
2. 执行 jieba 分词、词频和 TF-IDF；
3. 先拆分原始文本，再仅用训练集建立 TextCNN 词表；
4. 用固定种子和分层拆分训练，在独立测试集计算 Accuracy、Precision、Recall、F1、损失和混淆矩阵；
5. 把只读评估报告写入 `data/processed/model_metrics.json`；
6. 运行 LDA、SIR 情景分析、ARIMA 预测，并按需导入 MongoDB。

启动大屏：

```bash
python -m web
```

访问 <http://localhost:5000>。

## 测试

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
```

测试使用临时夹具，不读取、覆盖或提交你的本地数据和模型。

## 数据与结论边界

### 模拟数据

`scripts/generate_data.py` 生成帖子、评论、互动量、时间和平台字段，全部是合成记录，只适合开发与演示。

### 公开情感语料派生数据

`scripts/download_real_data.py` 可下载 ChnSentiCorp 和 waimai_10k。只有原始评论文本和情感标签来自公开语料；话题、账号、时间、互动量和派生评论仍是合成字段，因此它不是“真实社交媒体舆情数据”。脚本会在来源清单中记录这一限制，并保持 TLS 证书校验。

### 模型解释

- TextCNN 指标只描述固定随机种子下的本地留出集，不证明跨平台或真实舆情效果。
- SIR 用讨论量曲线拟合有界参数，展示的是传播情景模拟；其中人群规模和 `R₀` 不能解释为真实平台用户规模或因果效应。
- ARIMA 给出当前本地序列的统计外推和 95% 区间；过滤后少于 10 个点时明确拒绝预测。
- 无数据、数据不足或来源未知时，API 会返回结构化错误，不会静默填入演示曲线。

## 主要 API

| 路径 | 作用 | 关键约束 |
|---|---|---|
| `/api/data_status` | 数据规模、日期范围、来源和评估状态 | 来源缺失时标记为 `inferred`/`unknown` |
| `/api/overview` | 帖子、关联评论、情感和平台统计 | 日期与话题同时作用于帖子和关联评论 |
| `/api/sentiment_trend` | 每日情感趋势 | 支持日期/话题筛选 |
| `/api/word_freq` | 词云数据 | 支持日期/话题筛选 |
| `/api/sir_prediction` | SIR 传播情景模拟 | `days` 为 1–90，至少 6 个观测点 |
| `/api/arima_prediction` | ARIMA 时间序列预测 | `steps` 为 1–30，至少 10 个观测点 |
| `/api/model_metrics` | 读取离线 TextCNN 评估报告 | 不在请求中训练；报告缺失返回 503 |
| `/api/alerts` | 负面情感阈值预警 | 支持日期/话题筛选 |

日期参数必须使用 `YYYY-MM-DD`，开始日期不能晚于结束日期。非法参数返回结构化 400；数据不足返回 422。

## 可选 MongoDB 与安全默认值

本地 JSON 是默认存储。只有显式设置 `MONGO_ENABLED=true` 时才尝试连接 MongoDB，避免未安装数据库时每个请求阻塞。

常用环境变量：

```text
MONGO_ENABLED=false
MONGO_HOST=localhost
MONGO_PORT=27017
MONGO_DB=sentiment_db
WEB_PORT=5000
WEB_DEBUG=false
CORS_ORIGINS=
RANDOM_SEED=42
```

Flask debug 默认关闭；同源大屏不默认开放跨域。需要独立前端时再用逗号分隔的 `CORS_ORIGINS` 显式授权。

## 项目结构

```text
├─ crawler/                 # 可选数据采集；fallback 明确标为 synthetic
├─ data/                    # 本地数据与离线评估产物（Git 忽略）
├─ docs/evidence/           # 基线、证据账本、排序、验收和复测结果
├─ models/                  # TextCNN、LDA、SIR、ARIMA
├─ processing/              # 清洗、分词、TF-IDF、词频
├─ scripts/                 # 数据生成、公开语料派生、完整流水线
├─ storage/                 # MongoDB / 本地 JSON 访问层
├─ tests/                   # API 合约与数据泄漏回归测试
├─ web/                     # Flask API 与 ECharts 大屏
├─ config.py
└─ requirements.txt
```

## 尚未完成的验证

- 没有代表性真实用户的任务成功率、完成时间或结构化访谈；
- 没有真实社交媒体、跨时间和跨平台的独立测试集；
- 没有官方比赛评分表，本轮优先级使用的是仓库内公开的临时权重；
- 爬虫在实际使用前仍需确认平台条款、授权、隐私和采样偏差。

下一项最高价值实验是：在合规前提下取得带明确来源与许可的目标场景数据，冻结独立测试集，并与词典或传统机器学习基线做同条件比较。
