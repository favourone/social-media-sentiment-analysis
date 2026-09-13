# 算法升级 V3 记录（suanfa 分支）

日期：2026-09-13。本轮把"规则/统计"基线升级为"预训练模型 + 语义聚类 + 混合预测"，全部改动保留原基线作为显式回退路径。

## 改动范围

| 模块 | 原算法 | 新算法 | 代码位置 | 版本号 |
|---|---|---|---|---|
| 情感分析 | 透明词典（`transparent_lexicon_v2`） | 微调中文 BERT/RoBERTa | `crawler/adapters.py::score_sentiment`、`models/bert_sentiment.py`、`scripts/finetune_bert_sentiment.py` | `bert_finetuned_v3` |
| 事件聚合 | 字符 n-gram TF-IDF（`char_ngram_tfidf_v2`） | BERTopic（Sentence Embedding + UMAP + HDBSCAN） | `services/intelligence.py::_group_by_bertopic` | `bertopic_v3` |
| 趋势预测 | ARIMA、SIR 独立运行 | 混合预测：ARIMA/SIR 基线 + LSTM 残差 + SIR 形状先验 | `models/hybrid_predictor.py`、`services/tasks.py::_hybrid_analysis` | `hybrid_arima_sir_lstm_v3` |

`ALERT_ALGORITHM_VERSION` 保持 `explainable_alert_rules_v2`；预警证据里的 `clustering_algorithm_version` 与 `sentiment_method_counts` 会如实反映当次运行实际使用的算法。

## 设计要点

1. **引擎开关与显式回退**。情感分析顺序为"来源标签 > BERT > 词典"；聚类在 `CLUSTERING_ENGINE=auto` 且信号量足够（`BERTOPIC_MIN_DOCS`，默认 8）时才启用 BERTopic；混合预测在 PyTorch 缺失或观测点不足 10 时返回 `available=false` 并给出原因。任何环节失败都不会伪造结果，与平台"不自动填入模拟数据"的边界一致。
2. **可解释性**。BERT 结果附带词典交叉核对证据；BERTopic 事件在 `metrics.clustering_engine` 中记录主题数、噪声点数和自动生成的中文主题词（jieba 分词 c-TF-IDF）；混合预测输出双基线预测、LSTM 融合权重、R₀ 和验证残差标准差。
3. **可复现性**。BERTopic 固定 `random_state=42`；混合预测固定随机种子并按时间切分训练/验证（后 20% 窗口），不做随机打乱，避免时序泄漏；BERT 微调脚本输出 `training_meta.json`（数据量、超参、逐 epoch 指标、最优 macro-F1）。
4. **中文适配**。BERTopic 必须显式传 `language=None`：默认 `english` 会触发内部 `[^A-Za-z0-9 ]` 清洗，把中文正文剥成空文档导致空词表错误；c-TF-IDF 使用 jieba 分词器生成可读主题词。

## 复现步骤

```powershell
conda activate cv
python -m pip install -r requirements-ml.txt

# 情感微调（0=负面，1=正面；支持 JSON/JSONL/CSV）
python scripts/finetune_bert_sentiment.py --train <数据文件> --model-name hfl/chinese-roberta-wwm-ext --epochs 4

# 启用语义聚类（.env 已默认 CLUSTERING_ENGINE=auto）
python -m web
```

混合预测通过分析任务触发：`POST /api/v1/analysis-jobs {"type": "hybrid"}` 或 `full`。

## 验证记录

- 本机 Conda `cv` 环境：`python -m unittest discover -s tests` **59/59 通过**（原 44 + 新增 15）。
- 新增测试 `tests/test_suanfa_algorithms.py` 覆盖：引擎开关与回退、BERT 结果注入与证据结构、来源标签优先级、BERTopic 注入确定性向量后的语义分组与版本标记、聚类失败回退 n-gram、混合预测区间与最小样本约束。
- 旧测试通过模块级 `setUpModule` 固定基线引擎（`SENTIMENT_ENGINE=lexicon`、`CLUSTERING_ENGINE=ngram`），断言契约不受 `.env` 影响。
- CI 覆盖率口径（仅 `test_v2_monitoring`）：**81%（1013/1250）** ≥ 80% 门禁；CI 环境未安装 bertopic，相关分支标记 `# pragma: no cover` 并由 cv 环境的专项测试覆盖。

## 边界声明

- 情感模型准确率取决于训练数据分布；"校园测试集 94%" 类指标必须来自真实人工标注的留存集，验收前不得作为既成事实引用。
- BERTopic 的"主题连贯性提升"需要在与基线相同样本上做主题词人工对比后才能写进答辩材料。
- 混合预测的置信区间来自验证残差，样本量小则区间偏窄；预测不构成因果结论。
